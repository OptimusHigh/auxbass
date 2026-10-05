"""
TG Player - YouTube & YouTube Music Provider
Extracts metadata and audio from YouTube and YouTube Music tracks, albums, and playlists using yt-dlp.
"""
import os
import re
import asyncio
import logging
import aiohttp
from typing import Optional, List, Dict, Any, Tuple, Callable

import yt_dlp
from yt_dlp.utils import download_range_func

from shared.config import get_settings, get_proxy_url
from shared.matching import clean_track_metadata
from shared.images import crop_image_file_to_square
from ..base import (
    BaseMusicProvider,
    SourceEntity,
    TrackMetadata,
    DownloadedAudio,
    EntityType,
    calculate_preview_range,
)

logger = logging.getLogger(__name__)

# Matches youtube.com, music.youtube.com, youtu.be
YT_URL_PATTERN = re.compile(
    r"^https?://(?:(?:music|www|m)\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=|embed/|v/|shorts/|playlist\?list=|channel/|browse/|album/)?[\w\-_./?=&]+",
    re.IGNORECASE,
)


def _improve_yt_thumbnail(thumb_url: Optional[str]) -> Optional[str]:
    """
    Ensure YouTube thumbnail is valid.
    Do NOT blindly replace hqdefault with maxresdefault if query tokens or sqp signatures exist,
    as that breaks the signature and returns 404 on YouTube's CDN.
    """
    if not thumb_url:
        return None
    return thumb_url


def _get_yt_thumbnail_candidates(thumb_url: Optional[str], video_id: Optional[str] = None) -> List[str]:
    """
    Generate ordered list of candidate thumbnail URLs for a YouTube video.
    Starts with provided URL, then tries standard YouTube CDN formats
    from highest quality down to guaranteed fallbacks (hqdefault, mqdefault).
    """
    candidates: List[str] = []
    if thumb_url and thumb_url.startswith("http"):
        candidates.append(thumb_url)

    if not video_id and thumb_url:
        m = re.search(r"/vi/([a-zA-Z0-9_\-]{11})/", thumb_url)
        if m:
            video_id = m.group(1)

    if video_id:
        standards = [
            f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        ]
        for url in standards:
            if url not in candidates:
                candidates.append(url)

    return candidates


def _extract_yt_thumbnail(entry: Optional[dict], fallback: Optional[str] = None) -> Optional[str]:
    """Extract best available thumbnail from a YouTube entry dictionary."""
    if not entry or not isinstance(entry, dict):
        return _improve_yt_thumbnail(fallback)

    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        # Pick largest by width / height / preference
        sorted_thumbs = sorted(
            [t for t in thumbnails if isinstance(t, dict) and t.get("url")],
            key=lambda x: (x.get("width") or 0) * (x.get("height") or 0) + (x.get("preference") or 0),
            reverse=True,
        )
        if sorted_thumbs:
            return sorted_thumbs[0]["url"]

    direct = entry.get("thumbnail") or fallback
    return _improve_yt_thumbnail(direct)


def _parse_artist_and_title(
    raw_title: str,
    uploader: Optional[str] = None,
    channel: Optional[str] = None,
    track_field: Optional[str] = None,
    artist_field: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Extract clean artist and title from YouTube video metadata.
    Handles YouTube Music 'Artist - Topic' channels and 'Artist - Title' video titles.
    """
    # 1. If explicit track/artist metadata is present in yt-dlp info (common in YouTube Music)
    if track_field and artist_field:
        clean_t, clean_a = clean_track_metadata(track_field, artist_field)
        return clean_a or artist_field, clean_t or track_field

    raw_clean_title = raw_title.strip()
    raw_artist = (uploader or channel or "YouTube").strip()

    # Strip ' - Topic' suffix from YouTube Music auto-generated channels
    if raw_artist.lower().endswith(" - topic"):
        raw_artist = raw_artist[:-8].strip()

    # 2. Check if title contains 'Artist - Title'
    if " - " in raw_clean_title:
        parts = raw_clean_title.split(" - ", 1)
        part_artist = parts[0].strip()
        part_title = parts[1].strip()
        if part_artist and part_title:
            clean_t, clean_a = clean_track_metadata(part_title, part_artist)
            return clean_a or part_artist, clean_t or part_title

    clean_t, clean_a = clean_track_metadata(raw_clean_title, raw_artist)
    return clean_a or raw_artist, clean_t or raw_clean_title


class YouTubeMusicProvider(BaseMusicProvider):
    """YouTube and YouTube Music audio provider powered by yt-dlp."""

    name: str = "youtube"

    def _get_ydl_opts(self, extra: Optional[dict] = None) -> dict:
        """Create yt-dlp options dictionary with timeouts and proxy configured."""
        settings = get_settings()
        opts = {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": settings.ytdlp_timeout,
        }
        proxy = get_proxy_url()
        if proxy:
            opts["proxy"] = proxy
        if extra:
            opts.update(extra)
        return opts

    def can_handle(self, url: str) -> bool:
        clean_url = url.strip()
        if not YT_URL_PATTERN.match(clean_url):
            return False
        # Ensure it's a youtube domain
        return any(d in clean_url.lower() for d in ("youtube.com", "youtu.be", "music.youtube.com"))

    async def resolve_entity(self, url: str) -> SourceEntity:
        """Inspect YouTube/YouTube Music URL to determine if it's a single track or playlist/album."""
        clean_url = url.strip()

        def _extract():
            ydl_opts = self._get_ydl_opts({
                "extract_flat": "in_playlist",
                "skip_download": True,
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(clean_url, download=False)

        info = await asyncio.to_thread(_extract)
        if not info:
            raise ValueError(f"Could not resolve YouTube URL: {clean_url}")

        is_playlist = (
            info.get("_type") == "playlist"
            or "entries" in info
            or "list=" in clean_url
        )

        if is_playlist:
            raw_entries = info.get("entries") or []
            entries = list(raw_entries)
            title = info.get("title") or "YouTube Playlist"
            author = info.get("uploader") or info.get("channel") or "YouTube"
            if author.lower().endswith(" - topic"):
                author = author[:-8].strip()
            cover = _extract_yt_thumbnail(info, info.get("thumbnail"))

            clean_lower = clean_url.lower()
            is_album = (
                "album" in clean_lower
                or "list=olak5uy_" in clean_lower
                or "browse/mpreb_" in clean_lower
                or "album" in title.lower()
            )
            entity_type = EntityType.ALBUM if is_album else EntityType.PLAYLIST

            return SourceEntity(
                provider_name=self.name,
                entity_type=entity_type,
                url=clean_url,
                title=title,
                author=author,
                cover_url=cover,
                track_count=len(entries),
                raw_data=info,
            )
        else:
            raw_title = info.get("title") or "YouTube Track"
            uploader = info.get("uploader") or info.get("channel")
            track_field = info.get("track")
            artist_field = info.get("artist")
            artist, title = _parse_artist_and_title(raw_title, uploader, track_field=track_field, artist_field=artist_field)
            cover = _extract_yt_thumbnail(info, info.get("thumbnail"))

            return SourceEntity(
                provider_name=self.name,
                entity_type=EntityType.TRACK,
                url=clean_url,
                title=title,
                author=artist,
                cover_url=cover,
                track_count=1,
                raw_data=info,
            )

    async def fetch_tracklist(self, entity: SourceEntity) -> List[TrackMetadata]:
        """Fetch all track metadata from the entity."""
        if entity.entity_type == EntityType.TRACK:
            raw = entity.raw_data or {}
            raw_title = raw.get("title") or entity.title
            uploader = raw.get("uploader") or raw.get("channel") or entity.author
            track_field = raw.get("track")
            artist_field = raw.get("artist")
            artist, title = _parse_artist_and_title(raw_title, uploader, track_field=track_field, artist_field=artist_field)
            duration = int(raw.get("duration") or 0) or None
            cover = _extract_yt_thumbnail(raw, entity.cover_url)

            return [
                TrackMetadata(
                    provider_name=self.name,
                    url=entity.url,
                    title=title,
                    artist=artist,
                    duration=duration,
                    cover_url=cover,
                    external_id=str(raw.get("id") or ""),
                    extra={"uploader": uploader},
                )
            ]

        # Playlist / Album
        raw = entity.raw_data or {}
        entries = raw.get("entries")

        if not entries:
            def _extract_full():
                ydl_opts = self._get_ydl_opts({
                    "extract_flat": True,
                    "skip_download": True,
                })
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(entity.url, download=False)

            info = await asyncio.to_thread(_extract_full)
            entries = info.get("entries") or []

        tracks: List[TrackMetadata] = []
        album_name_val = entity.title if entity.entity_type == EntityType.ALBUM else None
        for idx, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue

            raw_title = entry.get("title") or f"Track {idx}"
            uploader = entry.get("uploader") or entry.get("channel") or entity.author
            track_field = entry.get("track")
            artist_field = entry.get("artist")
            artist, title = _parse_artist_and_title(raw_title, uploader, track_field=track_field, artist_field=artist_field)

            track_url = entry.get("webpage_url") or entry.get("url") or ""
            video_id = str(entry.get("id") or "")
            if not track_url and video_id:
                track_url = f"https://www.youtube.com/watch?v={video_id}"
            elif track_url and not track_url.startswith("http"):
                track_url = f"https://www.youtube.com/watch?v={track_url}"

            duration = int(entry.get("duration") or 0) or None
            cover = _extract_yt_thumbnail(entry, entity.cover_url)

            tracks.append(
                TrackMetadata(
                    provider_name=self.name,
                    url=track_url or f"{entity.url}#{idx}",
                    title=title,
                    artist=artist,
                    album=album_name_val,
                    duration=duration,
                    cover_url=cover,
                    track_number=idx,
                    external_id=video_id or str(idx),
                    extra={"uploader": uploader},
                )
            )

        return tracks

    async def download_track(
        self,
        track_meta: TrackMetadata,
        temp_dir: str,
        progress_hook: Optional[Callable[[int], None]] = None,
        chunk_only: bool = False,
        chunk_duration: int = 30,
        chunk_start: Optional[int] = None,
    ) -> DownloadedAudio:
        """Download track to MP3 320kbps (or 192kbps for fast 30s preview chunk) and fetch high quality cover artwork."""
        os.makedirs(temp_dir, exist_ok=True)
        out_template = os.path.join(temp_dir, "audio.%(ext)s")

        # Calculate preview range avoiding empty intros
        start_sec, end_sec = (0, chunk_duration)
        if chunk_only:
            if chunk_start is not None:
                start_sec = max(0, chunk_start)
                end_sec = start_sec + chunk_duration
            else:
                start_sec, end_sec = calculate_preview_range(track_meta.duration, chunk_seconds=chunk_duration)
            logger.info(
                f"[YouTube] Downloading {chunk_duration}s preview chunk for "
                f"'{track_meta.artist} - {track_meta.title}' (range: {start_sec}s..{end_sec}s)"
            )

        def _yt_progress(d):
            if not progress_hook:
                return
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                if total > 0:
                    pct = min(99, max(0, int((downloaded / total) * 100)))
                    try:
                        progress_hook(pct)
                    except Exception:
                        pass
            elif d.get("status") == "finished":
                try:
                    progress_hook(100)
                except Exception:
                    pass

        def _download():
            ydl_opts = self._get_ydl_opts({
                "format": "bestaudio/best",
                "outtmpl": out_template,
                "concurrent_fragment_downloads": 5,
                "color": "never",
                "progress_hooks": [_yt_progress] if progress_hook else [],
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192" if chunk_only else "320",
                    }
                ],
            })
            if chunk_only:
                ydl_opts["download_ranges"] = download_range_func(None, [(start_sec, end_sec)])
                ydl_opts["force_keyframes_at_cuts"] = True

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(track_meta.url, download=True)

        await asyncio.to_thread(_download)

        expected_audio = os.path.join(temp_dir, "audio.mp3")
        if not os.path.exists(expected_audio):
            for f in os.listdir(temp_dir):
                if f.lower().endswith((".mp3", ".m4a", ".opus", ".ogg")):
                    expected_audio = os.path.join(temp_dir, f)
                    break

        if not os.path.exists(expected_audio):
            raise FileNotFoundError(f"Failed to download audio for track: {track_meta.title}")

        file_size = os.path.getsize(expected_audio)

        # Download cover artwork with fallback candidate chain
        cover_path = None
        settings = get_settings()
        proxy = settings.proxy_url.strip() if settings.proxy_url else None

        video_id = str(track_meta.external_id or "") if len(str(track_meta.external_id or "")) == 11 else None
        candidates = _get_yt_thumbnail_candidates(track_meta.cover_url, video_id=video_id)

        if candidates:
            temp_cover = os.path.join(temp_dir, "cover.jpg")
            try:
                async with aiohttp.ClientSession() as session:
                    for cand_url in candidates:
                        try:
                            async with session.get(cand_url, timeout=aiohttp.ClientTimeout(total=8), proxy=proxy) as resp:
                                if resp.status == 200:
                                    content = await resp.read()
                                    if len(content) > 1000:
                                        with open(temp_cover, "wb") as f:
                                            f.write(content)
                                        cover_path = temp_cover
                                        track_meta.cover_url = cand_url
                                        logger.info(f"[YouTube] Downloaded cover for '{track_meta.title}' from {cand_url}")
                                        break
                        except Exception as cand_err:
                            logger.debug(f"[YouTube] Candidate cover failed ({cand_url}): {cand_err}")
            except Exception as e:
                logger.warning(f"[YouTube] Cover download failed for {track_meta.title}: {e}")
                cover_path = None

        # Fallback: if YouTube CDN didn't return an image, search official high-res artwork via cover_search
        if not cover_path and track_meta.artist and track_meta.title:
            try:
                from ...enrichment.cover_search import search_cover_suggestions
                search_q = f"{track_meta.artist} {track_meta.title}".strip()
                suggestions = await search_cover_suggestions(search_q, limit_per_source=2)
                if suggestions:
                    best_art = suggestions[0].get("cover_url")
                    if best_art:
                        temp_cover = os.path.join(temp_dir, "cover.jpg")
                        async with aiohttp.ClientSession(trust_env=True) as session:
                            async with session.get(best_art, timeout=aiohttp.ClientTimeout(total=8), proxy=proxy) as resp:
                                if resp.status == 200:
                                    content = await resp.read()
                                    if len(content) > 1000:
                                        with open(temp_cover, "wb") as f:
                                            f.write(content)
                                        cover_path = temp_cover
                                        track_meta.cover_url = best_art
                                        logger.info(f"[YouTube] Sourced official album artwork for '{track_meta.title}' from {best_art[:60]}")
            except Exception as sugg_err:
                logger.debug(f"[YouTube] Fallback cover search failed: {sugg_err}")

        # If no cover succeeded, ensure track_meta.cover_url is None instead of a broken link
        if not cover_path:
            track_meta.cover_url = None
        elif os.path.exists(cover_path):
            # Center-crop cover to 1:1 square, trimming letterbox/pillarbox borders
            crop_image_file_to_square(cover_path)

        if chunk_only:
            if track_meta.duration and track_meta.duration > chunk_duration:
                track_meta.extra["full_duration"] = track_meta.duration
            track_meta.duration = chunk_duration
            track_meta.extra["is_chunk"] = True

        return DownloadedAudio(
            audio_path=expected_audio,
            cover_path=cover_path,
            metadata=track_meta,
            file_size=file_size,
            mime_type="audio/mpeg",
        )

    async def search(self, query: str, limit: int = 30) -> List[TrackMetadata]:
        """
        Search tracks by keyword on YouTube Music (with fallback to general YouTube search).
        """
        clean_q = query.strip()
        if not clean_q:
            return []

        def _search_ytm():
            ydl_opts = self._get_ydl_opts({
                "extract_flat": True,
                "skip_download": True,
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # 1. Try YouTube Music search prefix
                try:
                    res = ydl.extract_info(f"ytmsearch{limit}:{clean_q}", download=False)
                    entries = res.get("entries") or []
                    if entries:
                        return entries
                except Exception as ytm_err:
                    logger.debug(f"[YouTube] ytmsearch failed, falling back to ytsearch: {ytm_err}")

                # 2. Fallback to standard YouTube search
                res = ydl.extract_info(f"ytsearch{limit}:{clean_q}", download=False)
                return res.get("entries") or []

        try:
            entries = await asyncio.to_thread(_search_ytm)
        except Exception as e:
            logger.warning(f"[YouTube] Search failed for '{clean_q}': {e}")
            return []

        results: List[TrackMetadata] = []
        for idx, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue

            raw_title = entry.get("title") or "YouTube Track"
            uploader = entry.get("uploader") or entry.get("channel") or "YouTube"
            track_field = entry.get("track")
            artist_field = entry.get("artist")
            artist, title = _parse_artist_and_title(raw_title, uploader, track_field=track_field, artist_field=artist_field)

            vid = str(entry.get("id") or "")
            t_url = entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={vid}"
            dur = int(entry.get("duration") or 0) or None
            cover = _extract_yt_thumbnail(entry)

            results.append(
                TrackMetadata(
                    provider_name=self.name,
                    url=t_url,
                    title=title,
                    artist=artist,
                    duration=dur,
                    cover_url=cover,
                    track_number=idx,
                    external_id=vid,
                    extra={"uploader": uploader},
                )
            )

        return results
