"""
TG Player - SoundCloud Provider
Extracts metadata and audio from SoundCloud tracks, playlists, and sets using yt-dlp.
"""
import os
import re
import asyncio
import logging
import aiohttp
import time
from typing import Optional, List, Dict, Any, Tuple, Callable

import yt_dlp
from yt_dlp.utils import download_range_func

from shared.config import get_settings, get_proxy_url
from ..base import (
    BaseMusicProvider, SourceEntity, TrackMetadata, DownloadedAudio, EntityType,
    calculate_preview_range,
)

logger = logging.getLogger(__name__)

SC_URL_PATTERN = re.compile(
    r"^https?://(?:(?:m|www)\.)?(?:soundcloud\.com|on\.soundcloud\.com)/[\w\-_./]+",
    re.IGNORECASE
)


def _improve_sc_thumbnail(thumb_url: Optional[str]) -> Optional[str]:
    """Upgrade SoundCloud thumbnail to 500x500 resolution."""
    if not thumb_url:
        return None
    for pattern in ("-large.", "-small.", "-badge.", "-t67x67.", "-t300x300.", "-crop."):
        if pattern in thumb_url:
            return thumb_url.replace(pattern, "-t500x500.")
    return thumb_url


def _extract_sc_thumbnail(entry: Optional[dict], fallback: Optional[str] = None) -> Optional[str]:
    """Extract best available thumbnail from a SoundCloud entry dictionary."""
    if not entry or not isinstance(entry, dict):
        return _improve_sc_thumbnail(fallback)

    # 1. Check thumbnails list (populated in extract_flat mode)
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        # High quality targets
        for target_id in ("t500x500", "original", "t300x300", "crop", "large"):
            for t in thumbnails:
                if isinstance(t, dict) and t.get("id") == target_id and t.get("url"):
                    return _improve_sc_thumbnail(t["url"])

        # Fallback to the last one (usually highest quality)
        for t in reversed(thumbnails):
            if isinstance(t, dict) and t.get("url"):
                return _improve_sc_thumbnail(t["url"])

    # 2. Check direct fields
    direct = entry.get("thumbnail") or entry.get("artwork_url") or entry.get("avatar_url") or fallback
    return _improve_sc_thumbnail(direct)


def _parse_artist_and_title(raw_title: str, uploader: Optional[str]) -> tuple[str, str]:
    """
    SoundCloud titles often follow 'Artist - Title' format.
    Extract clean artist and title.
    """
    clean_title = raw_title.strip()
    artist = (uploader or "SoundCloud").strip()

    if " - " in clean_title:
        parts = clean_title.split(" - ", 1)
        part_artist = parts[0].strip()
        part_title = parts[1].strip()
        if part_artist and part_title:
            return part_artist, part_title

    return artist, clean_title


def _extract_sc_tags(
    tag_list: Optional[str] = None,
    description: Optional[str] = None,
    raw_tags: Optional[List[str]] = None,
) -> List[str]:
    """Parse tags and hashtags from SoundCloud tag_list, tags list, and description."""
    tags: List[str] = []

    if isinstance(raw_tags, list):
        for t in raw_tags:
            if isinstance(t, str) and t.strip():
                clean_t = t.strip().lower()
                if clean_t and clean_t not in tags:
                    tags.append(clean_t)

    if tag_list and isinstance(tag_list, str):
        matches = re.findall(r'"([^"]+)"|(\S+)', tag_list)
        for quoted, unquoted in matches:
            t = (quoted or unquoted).strip().lower()
            if t and t not in tags:
                tags.append(t)

    if description and isinstance(description, str):
        hashtags = re.findall(r'#([A-Za-z0-9_\u0400-\u04FF]+)', description)
        for h in hashtags:
            clean_h = h.strip().lower()
            if clean_h and clean_h not in tags:
                tags.append(clean_h)

    return tags[:10]


class SoundCloudProvider(BaseMusicProvider):
    """SoundCloud music provider powered by yt-dlp."""
    name: str = "soundcloud"

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
        return bool(SC_URL_PATTERN.match(url.strip()))

    async def resolve_entity(self, url: str) -> SourceEntity:
        """Inspect SoundCloud URL to determine if it's a single track or playlist/set."""
        clean_url = url.strip()

        def _extract():
            ydl_opts = self._get_ydl_opts({
                "extract_flat": "in_playlist",
                "skip_download": True,
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(clean_url, download=False)

        try:
            info = await asyncio.to_thread(_extract)
        except Exception as e:
            err_str = str(e).lower()
            if "drm protected" in err_str or "proxy" in err_str or "unable to download" in err_str or "geo restriction" in err_str:
                logger.info(f"[SoundCloud] yt-dlp inspection failed for '{clean_url}', attempting oEmbed fallback: {e}")
                try:
                    info = await self._resolve_drm_via_oembed(clean_url)
                except Exception as oe_err:
                    logger.warning(f"[SoundCloud] oEmbed fallback failed: {oe_err}")
                    raise e from None
            else:
                raise

        if not info:
            raise ValueError(f"Could not resolve SoundCloud URL: {clean_url}")

        is_playlist = (
            info.get("_type") == "playlist"
            or "entries" in info
            or "/sets/" in clean_url
        )

        if is_playlist:
            raw_entries = info.get("entries") or []
            entries = list(raw_entries)
            title = info.get("title") or "SoundCloud Playlist"
            author = info.get("uploader") or info.get("channel") or "SoundCloud"
            cover = _extract_sc_thumbnail(info, info.get("thumbnail"))

            set_type = str(info.get("set_type") or "").lower()
            title_lower = title.lower()
            is_album = (
                set_type in ("album", "ep", "compilation")
                or "album" in title_lower
                or " ep" in title_lower
                or title_lower.endswith(" ep")
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
            raw_title = info.get("title") or "SoundCloud Track"
            uploader = info.get("uploader") or info.get("artist")
            artist, title = _parse_artist_and_title(raw_title, uploader)
            cover = _extract_sc_thumbnail(info, info.get("thumbnail"))
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

    async def _resolve_drm_via_oembed(self, url: str) -> dict:
        """Fetch basic track metadata from SoundCloud oEmbed API for DRM tracks."""
        import aiohttp
        import urllib.parse
        encoded_url = urllib.parse.quote(url, safe='')
        oembed_url = f"https://soundcloud.com/oembed?format=json&url={encoded_url}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        settings = get_settings()
        proxy = settings.proxy_url.strip() if settings.proxy_url else None
        timeout = aiohttp.ClientTimeout(total=settings.ytdlp_timeout)
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(oembed_url, timeout=timeout, proxy=proxy) as resp:
                if resp.status != 200:
                    raise ValueError(f"SoundCloud oEmbed failed (HTTP {resp.status})")
                data = await resp.json()

        author = data.get("author_name") or "SoundCloud"
        raw_title = data.get("title") or "Track"
        # Often formatted as: "Track Title by author"
        if raw_title.lower().endswith(f" by {author.lower()}"):
            raw_title = raw_title[: -(len(author) + 4)].strip()

        # Extract track id from iframe html if possible
        html = data.get("html") or ""
        m = re.search(r"/tracks%2F(\d+)", html) or re.search(r"/tracks/(\d+)", html)
        ext_id = m.group(1) if m else None

        return {
            "_type": "url",
            "title": raw_title,
            "uploader": author,
            "artist": author,
            "thumbnail": data.get("thumbnail_url"),
            "id": ext_id,
            "is_drm": True,
        }

    async def fetch_tracklist(self, entity: SourceEntity) -> List[TrackMetadata]:
        """Fetch all track metadata from the entity."""
        if entity.entity_type == EntityType.TRACK:
            raw = entity.raw_data or {}
            raw_title = raw.get("title") or entity.title
            uploader = raw.get("uploader") or entity.author
            artist, title = _parse_artist_and_title(raw_title, uploader)
            duration = int(raw.get("duration") or 0) or None
            cover = _extract_sc_thumbnail(raw, entity.cover_url)

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

        # Playlist / Set
        raw = entity.raw_data or {}
        entries = raw.get("entries")

        # If flat entries don't have full info, or if entries are missing, re-extract
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
            uploader = entry.get("uploader") or entity.author
            artist, title = _parse_artist_and_title(raw_title, uploader)
            track_url = entry.get("webpage_url") or entry.get("url") or ""
            if track_url and not track_url.startswith("http"):
                track_url = f"https://soundcloud.com/{track_url.lstrip('/')}"
            if not track_url:
                track_url = f"{entity.url}#{idx}"
            duration = int(entry.get("duration") or 0) or None
            cover = _extract_sc_thumbnail(entry, entity.cover_url)

            tracks.append(
                TrackMetadata(
                    provider_name=self.name,
                    url=track_url,
                    title=title,
                    artist=artist,
                    album=album_name_val,
                    duration=duration,
                    cover_url=cover,
                    track_number=idx,
                    external_id=str(entry.get("id") or ""),
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
        """Download track to MP3 and fetch high quality cover artwork. Supports 30s preview chunks."""
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
                f"[SoundCloud] Downloading {chunk_duration}s preview chunk for "
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
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": out_template,
                "concurrent_fragment_downloads": 5,
                "color": "never",
                "progress_hooks": [_yt_progress] if progress_hook else [],
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192" if chunk_only else "0",
                    }
                ],
                "quiet": True,
                "no_warnings": True,
            }
            if chunk_only:
                ydl_opts["download_ranges"] = download_range_func(None, [(start_sec, end_sec)])
                ydl_opts["force_keyframes_at_cuts"] = True

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(track_meta.url, download=True)
            except Exception as e:
                err_msg = str(e)
                if "drm protected" in err_msg.lower():
                    raise ValueError("DRM_PROTECTED") from e
                raise

        info_dict = None
        try:
            info_dict = await asyncio.to_thread(_download)
        except Exception as e:
            if "drm protected" in str(e).lower() or "DRM_PROTECTED" in str(e):
                logger.info(
                    f"SoundCloud DRM encountered for '{track_meta.artist} - {track_meta.title}'. "
                    f"Resolving unencrypted alternative stream..."
                )
                from ..audio_resolver import audio_resolver
                try:
                    return await audio_resolver.resolve_and_download(
                        track_meta, temp_dir, exclude_urls={track_meta.url}, progress_hook=progress_hook,
                        chunk_only=chunk_only, chunk_duration=chunk_duration, chunk_start=chunk_start,
                    )
                except Exception as resolve_err:
                    logger.warning(f"AudioResolver fallback failed for '{track_meta.title}': {resolve_err}")
                    raise ValueError("Этот трек защищён DRM (SoundCloud Go+) и недоступен для бесплатного воспроизведения.") from e
            raise

        # Expected output is audio.mp3
        expected_audio = os.path.join(temp_dir, "audio.mp3")
        if not os.path.exists(expected_audio):
            # Fallback: look for any audio file in temp_dir
            for f in os.listdir(temp_dir):
                if f.lower().endswith((".mp3", ".m4a", ".opus", ".ogg")):
                    expected_audio = os.path.join(temp_dir, f)
                    break

        if not os.path.exists(expected_audio):
            raise FileNotFoundError(f"Failed to download audio for track: {track_meta.title}")

        file_size = os.path.getsize(expected_audio)

        # Enrich track_meta with original metadata extracted from SoundCloud
        if isinstance(info_dict, dict):
            sc_thumb = _extract_sc_thumbnail(info_dict, track_meta.cover_url)
            if sc_thumb:
                track_meta.cover_url = _improve_sc_thumbnail(sc_thumb)

            sc_genre = info_dict.get("genre")
            if sc_genre and isinstance(sc_genre, str) and sc_genre.strip():
                track_meta.extra["genre"] = sc_genre.strip()

            sc_tags = _extract_sc_tags(
                tag_list=info_dict.get("tag_list"),
                description=info_dict.get("description"),
                raw_tags=info_dict.get("tags"),
            )
            if sc_tags:
                track_meta.extra["tags"] = sc_tags

            raw_title = info_dict.get("title")
            uploader = info_dict.get("uploader") or info_dict.get("artist")
            if raw_title:
                parsed_artist, parsed_title = _parse_artist_and_title(raw_title, uploader)
                if not track_meta.title or track_meta.title in ("Track", "SoundCloud Track"):
                    track_meta.title = parsed_title
                if not track_meta.artist or track_meta.artist in ("Artist", "SoundCloud"):
                    track_meta.artist = parsed_artist

            track_meta.extra["is_soundcloud"] = True

        # Download thumbnail cover if available
        cover_path = None
        if track_meta.cover_url:
            cover_path = os.path.join(temp_dir, "cover.jpg")
            try:
                proxy = get_proxy_url()
                async with aiohttp.ClientSession(trust_env=True) as session:
                    async with session.get(track_meta.cover_url, timeout=aiohttp.ClientTimeout(total=10), proxy=proxy) as resp:
                        if resp.status == 200:
                            content = await resp.read()
                            with open(cover_path, "wb") as f:
                                f.write(content)
                        else:
                            cover_path = None
            except Exception as e:
                logger.warning(f"Could not download cover for track {track_meta.title}: {e}")
                cover_path = None

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

    _search_cache: Dict[str, Tuple[float, List[TrackMetadata]]] = {}
    _SEARCH_CACHE_TTL = 300  # 5 minutes

    @staticmethod
    def _is_drm_preview_duration(raw_duration) -> bool:
        """
        Detect SoundCloud Go+ DRM preview snippets.
        yt-dlp extract_flat returns duration=30.0 for DRM-protected tracks
        (the length of the free preview), not the real track duration.
        We check for exactly 30.0 seconds (float) which is the hallmark of a Go+ snippet.
        """
        if raw_duration is None:
            return False
        try:
            dur = float(raw_duration)
        except (TypeError, ValueError):
            return False
        return abs(dur - 30.0) < 0.5  # exactly 30s (±0.5s tolerance)

    async def _resolve_real_duration(self, track_url: str) -> Optional[int]:
        """
        Try to get the real track duration from SoundCloud API v2 /resolve endpoint.
        Returns duration in seconds, or None on failure.
        """
        try:
            client_id = await self.get_client_id()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            import urllib.parse
            encoded_url = urllib.parse.quote(track_url, safe='')
            api_url = f"https://api-v2.soundcloud.com/resolve?url={encoded_url}&client_id={client_id}"

            settings = get_settings()
            proxy = settings.proxy_url.strip() if settings.proxy_url else None
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(api_url, timeout=timeout, proxy=proxy) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            # SoundCloud API v2 returns full_duration in milliseconds
            full_dur_ms = data.get("full_duration") or data.get("duration")
            if full_dur_ms and isinstance(full_dur_ms, (int, float)) and full_dur_ms > 0:
                return int(full_dur_ms / 1000)
        except Exception as e:
            logger.debug(f"[SoundCloud] Failed to resolve real duration for {track_url}: {e}")
        return None

    async def _enrich_drm_previews(self, results: List[TrackMetadata]) -> None:
        """
        For tracks detected as DRM previews (duration=30s), try to resolve real durations
        via SoundCloud API v2 in parallel. Marks tracks with extra['is_drm_preview'] flag
        and replaces the fake 30s duration with the real one when available.
        """
        drm_tracks = [r for r in results if self._is_drm_preview_duration(r.duration)]
        if not drm_tracks:
            return

        # Resolve real durations concurrently (max 5 at a time to be polite)
        semaphore = asyncio.Semaphore(5)

        async def _resolve_one(track: TrackMetadata):
            async with semaphore:
                real_dur = await self._resolve_real_duration(track.url)
                track.extra["is_drm_preview"] = True
                if real_dur and real_dur > 30:
                    track.extra["real_duration"] = real_dur
                    track.duration = real_dur
                    logger.debug(
                        f"[SoundCloud] Resolved real duration for DRM track "
                        f"'{track.artist} - {track.title}': {real_dur}s"
                    )

        await asyncio.gather(*[_resolve_one(t) for t in drm_tracks], return_exceptions=True)

    async def search(self, query: str, limit: int = 30) -> List[TrackMetadata]:
        """Search SoundCloud for tracks matching query."""
        clean_query = query.strip()
        # SoundCloud scsearch cannot handle hashtags
        if not clean_query or clean_query.startswith("#"):
            return []

        cache_key = clean_query.lower()
        now = time.time()
        if cache_key in self._search_cache:
            ts, cached_results = self._search_cache[cache_key]
            if now - ts < self._SEARCH_CACHE_TTL and len(cached_results) >= limit:
                return cached_results[:limit]

        def _search():
            proxy = get_proxy_url()
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
            }
            if proxy:
                ydl_opts["proxy"] = proxy
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"scsearch{limit}:{clean_query}", download=False)

        try:
            info = await asyncio.to_thread(_search)
            entries = info.get("entries") or []
        except Exception as e:
            logger.warning(f"SoundCloud search failed for query '{clean_query}': {e}")
            return []

        results: List[TrackMetadata] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_title = entry.get("title") or "SoundCloud Track"
            uploader = entry.get("uploader") or entry.get("channel") or "SoundCloud"
            artist, title = _parse_artist_and_title(raw_title, uploader)
            track_url = entry.get("webpage_url") or entry.get("url") or ""
            if not track_url:
                continue
            raw_duration = entry.get("duration")
            duration = int(raw_duration or 0) or None
            cover = _extract_sc_thumbnail(entry)

            sc_genre = entry.get("genre") if isinstance(entry.get("genre"), str) else None
            sc_tags = _extract_sc_tags(
                tag_list=entry.get("tag_list"),
                description=entry.get("description"),
                raw_tags=entry.get("tags"),
            )
            extra_data = {
                "uploader": uploader,
                "is_soundcloud": True,
            }
            if sc_genre and sc_genre.strip():
                extra_data["genre"] = sc_genre.strip()
            if sc_tags:
                extra_data["tags"] = sc_tags

            results.append(
                TrackMetadata(
                    provider_name=self.name,
                    url=track_url,
                    title=title,
                    artist=artist,
                    duration=duration,
                    cover_url=cover,
                    external_id=str(entry.get("id") or ""),
                    extra=extra_data,
                )
            )

        # Enrich DRM preview tracks with real durations from SoundCloud API v2
        await self._enrich_drm_previews(results)

        self._search_cache[cache_key] = (now, results)
        if len(self._search_cache) > 200:
            for k in list(self._search_cache.keys()):
                if now - self._search_cache[k][0] >= self._SEARCH_CACHE_TTL:
                    self._search_cache.pop(k, None)

        return results

    _cached_client_id: Optional[str] = None

    async def get_client_id(self) -> str:
        """Get or discover a valid SoundCloud API client_id using yt-dlp."""
        if self._cached_client_id:
            return self._cached_client_id

        def _discover():
            proxy = get_proxy_url()
            ydl_opts = {"quiet": True, "no_warnings": True}
            if proxy:
                ydl_opts["proxy"] = proxy
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ie = yt_dlp.extractor.soundcloud.SoundcloudUserIE(ydl)
                ie.initialize()
                return ie._CLIENT_ID

        try:
            cid = await asyncio.to_thread(_discover)
            if cid:
                self._cached_client_id = cid
                return cid
        except Exception as e:
            logger.warning(f"Failed to extract SoundCloud client_id via yt-dlp: {e}")

        # Fallback default client_id
        return "Pb72ranhoyt6gw7hM7TkzUItXlMWSNSo"

    async def resolve_user_profile(self, username_or_url: str, auth_token: Optional[str] = None) -> dict:
        """
        Resolve SoundCloud user profile by username or URL.
        Returns user info dictionary: external_id, username, display_name, avatar_url, profile_url, likes_count, tracks_count.
        """
        raw = username_or_url.strip()
        if "soundcloud.com/" in raw:
            m = re.search(r"soundcloud\.com/([a-zA-Z0-9_\-]+)", raw)
            permalink = m.group(1) if m else raw.rstrip("/").split("/")[-1]
        else:
            permalink = raw.lstrip("@")

        client_id = await self.get_client_id()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        if auth_token:
            headers["Authorization"] = f"OAuth {auth_token.strip()}"

        target_url = f"https://soundcloud.com/{permalink}"
        api_url = f"https://api-v2.soundcloud.com/resolve?url={target_url}&client_id={client_id}"
        proxy = get_proxy_url()

        async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
            async with session.get(api_url, proxy=proxy) as resp:
                if resp.status == 404:
                    raise ValueError(f"Пользователь SoundCloud '{permalink}' не найден.")
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"SoundCloud API error ({resp.status}): {text[:100]}")
                data = await resp.json()

        avatar = _improve_sc_thumbnail(data.get("avatar_url"))
        likes_count = data.get("likes_count") or data.get("public_favorites_count") or 0
        tracks_count = data.get("track_count") or 0

        return {
            "external_id": str(data.get("id")),
            "username": data.get("permalink") or permalink,
            "display_name": data.get("username") or permalink,
            "profile_url": data.get("permalink_url") or target_url,
            "avatar_url": avatar,
            "likes_count": likes_count,
            "tracks_count": tracks_count,
        }

    async def fetch_user_likes(
        self,
        external_id_or_username: str,
        limit: int = 50,
        next_href: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> tuple[List[TrackMetadata], Optional[str]]:
        """
        Fetch liked tracks for a SoundCloud user.
        Returns (list_of_tracks, next_cursor_href).
        """
        client_id = await self.get_client_id()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        if auth_token:
            headers["Authorization"] = f"OAuth {auth_token.strip()}"

        if next_href:
            req_url = next_href
            if "client_id=" not in req_url:
                delim = "&" if "?" in req_url else "?"
                req_url = f"{req_url}{delim}client_id={client_id}"
        else:
            user_id = str(external_id_or_username).strip()
            if not user_id.isdigit():
                profile = await self.resolve_user_profile(user_id, auth_token=auth_token)
                user_id = profile["external_id"]

            req_url = f"https://api-v2.soundcloud.com/users/{user_id}/likes?limit={limit}&client_id={client_id}"

        proxy = get_proxy_url()
        async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
            async with session.get(req_url, proxy=proxy) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"SoundCloud likes error ({resp.status}): {text[:100]}")
                data = await resp.json()

        collection = data.get("collection") or []
        next_url = data.get("next_href")

        tracks: List[TrackMetadata] = []
        for item in collection:
            if not isinstance(item, dict):
                continue
            tr = item.get("track") or item
            if not isinstance(tr, dict) or not tr.get("title"):
                continue

            raw_title = tr.get("title") or "SoundCloud Track"
            user_obj = tr.get("user") or {}
            uploader = user_obj.get("username") or "SoundCloud"
            artist, title = _parse_artist_and_title(raw_title, uploader)

            track_url = tr.get("permalink_url") or ""
            if not track_url and tr.get("permalink"):
                track_url = f"https://soundcloud.com/{user_obj.get('permalink', 'artist')}/{tr.get('permalink')}"
            if not track_url:
                continue

            dur_raw = tr.get("duration") or 0
            duration = int(dur_raw / 1000) if dur_raw > 1000 else int(dur_raw) or None
            cover = _improve_sc_thumbnail(tr.get("artwork_url") or user_obj.get("avatar_url"))

            sc_genre = tr.get("genre") if isinstance(tr.get("genre"), str) else None
            sc_tags = _extract_sc_tags(
                tag_list=tr.get("tag_list"),
                description=tr.get("description"),
            )
            extra_data = {
                "uploader": uploader,
                "liked_at": item.get("created_at"),
                "is_soundcloud": True,
            }
            if sc_genre and sc_genre.strip():
                extra_data["genre"] = sc_genre.strip()
            if sc_tags:
                extra_data["tags"] = sc_tags

            tracks.append(
                TrackMetadata(
                    provider_name=self.name,
                    url=track_url,
                    title=title,
                    artist=artist,
                    duration=duration,
                    cover_url=cover,
                    external_id=str(tr.get("id") or ""),
                    extra=extra_data,
                )
            )

        return tracks, next_url

    async def fetch_user_tracks(
        self,
        external_id_or_username: str,
        limit: int = 50,
        next_href: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> tuple[List[TrackMetadata], Optional[str]]:
        """
        Fetch uploaded tracks for a SoundCloud user.
        Returns (list_of_tracks, next_cursor_href).
        """
        client_id = await self.get_client_id()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        if auth_token:
            headers["Authorization"] = f"OAuth {auth_token.strip()}"

        if next_href:
            req_url = next_href
            if "client_id=" not in req_url:
                delim = "&" if "?" in req_url else "?"
                req_url = f"{req_url}{delim}client_id={client_id}"
        else:
            user_id = str(external_id_or_username).strip()
            if not user_id.isdigit():
                profile = await self.resolve_user_profile(user_id, auth_token=auth_token)
                user_id = profile["external_id"]

            req_url = f"https://api-v2.soundcloud.com/users/{user_id}/tracks?limit={limit}&client_id={client_id}"

        proxy = get_proxy_url()
        async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
            async with session.get(req_url, proxy=proxy) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"SoundCloud user tracks error ({resp.status}): {text[:100]}")
                data = await resp.json()

        collection = data.get("collection") or []
        next_url = data.get("next_href")

        tracks: List[TrackMetadata] = []
        for item in collection:
            if not isinstance(item, dict):
                continue
            tr = item.get("track") or item
            if not isinstance(tr, dict) or not tr.get("title"):
                continue

            raw_title = tr.get("title") or "SoundCloud Track"
            user_obj = tr.get("user") or {}
            uploader = user_obj.get("username") or "SoundCloud"
            artist, title = _parse_artist_and_title(raw_title, uploader)

            track_url = tr.get("permalink_url") or ""
            if not track_url and tr.get("permalink"):
                track_url = f"https://soundcloud.com/{user_obj.get('permalink', 'artist')}/{tr.get('permalink')}"
            if not track_url:
                continue

            dur_raw = tr.get("duration") or 0
            duration = int(dur_raw / 1000) if dur_raw > 1000 else int(dur_raw) or None
            cover = _improve_sc_thumbnail(tr.get("artwork_url") or user_obj.get("avatar_url"))

            sc_genre = tr.get("genre") if isinstance(tr.get("genre"), str) else None
            sc_tags = _extract_sc_tags(
                tag_list=tr.get("tag_list"),
                description=tr.get("description"),
            )
            extra_data = {
                "uploader": uploader,
                "created_at": tr.get("created_at"),
                "is_soundcloud": True,
            }
            if sc_genre and sc_genre.strip():
                extra_data["genre"] = sc_genre.strip()
            if sc_tags:
                extra_data["tags"] = sc_tags

            tracks.append(
                TrackMetadata(
                    provider_name=self.name,
                    url=track_url,
                    title=title,
                    artist=artist,
                    duration=duration,
                    cover_url=cover,
                    external_id=str(tr.get("id") or ""),
                    extra=extra_data,
                )
            )

        return tracks, next_url

    async def fetch_user_playlists(
        self,
        external_id_or_username: str,
        limit: int = 50,
        next_href: Optional[str] = None,
        auth_token: Optional[str] = None,
        playlist_type: str = "all",  # "all", "created", "liked"
    ) -> tuple[List[dict], Optional[str]]:
        """
        Fetch created and/or liked playlists for a SoundCloud user.
        Returns (list_of_playlist_dicts, next_cursor_href).
        """
        client_id = await self.get_client_id()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        if auth_token:
            headers["Authorization"] = f"OAuth {auth_token.strip()}"

        user_id = str(external_id_or_username).strip()
        if not user_id.isdigit():
            profile = await self.resolve_user_profile(user_id, auth_token=auth_token)
            user_id = profile["external_id"]

        playlists: List[dict] = []
        next_cursor = None

        def _format_playlist(p: dict, is_liked: bool) -> Optional[dict]:
            if not isinstance(p, dict) or not p.get("title"):
                return None
            p_user = p.get("user") or {}
            author = p_user.get("username") or p_user.get("permalink") or "SoundCloud"
            dur_ms = p.get("duration") or 0
            duration_sec = int(dur_ms / 1000) if dur_ms > 1000 else int(dur_ms)
            
            # Extract artwork
            raw_art = p.get("artwork_url")
            if not raw_art and p.get("tracks") and isinstance(p["tracks"], list) and len(p["tracks"]) > 0:
                first_tr = p["tracks"][0]
                if isinstance(first_tr, dict):
                    raw_art = first_tr.get("artwork_url")
            if not raw_art:
                raw_art = p_user.get("avatar_url")
            artwork = _improve_sc_thumbnail(raw_art)

            return {
                "id": str(p.get("id")),
                "title": p.get("title") or "SoundCloud Playlist",
                "permalink_url": p.get("permalink_url") or "",
                "artwork_url": artwork,
                "track_count": int(p.get("track_count") or len(p.get("tracks", [])) or 0),
                "duration": duration_sec,
                "author": author,
                "author_avatar": _improve_sc_thumbnail(p_user.get("avatar_url")),
                "description": p.get("description"),
                "is_public": bool(p.get("public", True)),
                "is_liked": is_liked,
                "created_at": p.get("created_at"),
            }

        raw_target = str(playlist_id_or_url).strip()
        proxy = get_proxy_url()

        async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
            # 1. Created playlists
            if playlist_type in ("all", "created"):
                created_url = f"https://api-v2.soundcloud.com/users/{user_id}/playlists?limit={limit}&client_id={client_id}"
                try:
                    async with session.get(created_url, proxy=proxy) as resp:
                        if resp.status == 200:
                            c_data = await resp.json()
                            for item in c_data.get("collection") or []:
                                formatted = _format_playlist(item, is_liked=False)
                                if formatted:
                                    playlists.append(formatted)
                except Exception as e:
                    logger.warning(f"Failed to fetch created playlists for user {user_id}: {e}")

            # 2. Liked playlists
            if playlist_type in ("all", "liked"):
                liked_url = next_href or f"https://api-v2.soundcloud.com/users/{user_id}/playlist_likes?limit={limit}&client_id={client_id}"
                try:
                    async with session.get(liked_url, proxy=proxy) as resp:
                        if resp.status == 200:
                            l_data = await resp.json()
                            next_cursor = l_data.get("next_href")
                            for item in l_data.get("collection") or []:
                                p_obj = item.get("playlist") or item
                                formatted = _format_playlist(p_obj, is_liked=True)
                                if formatted:
                                    # Deduplicate if somehow already added
                                    if not any(pl["id"] == formatted["id"] for pl in playlists):
                                        playlists.append(formatted)
                except Exception as e:
                    logger.warning(f"Failed to fetch liked playlists for user {user_id}: {e}")

        return playlists, next_cursor

    async def fetch_playlist_tracks(
        self,
        playlist_id_or_url: str,
        auth_token: Optional[str] = None,
    ) -> tuple[dict, List[TrackMetadata]]:
        """
        Fetch full playlist info and all its tracks.
        Handles resolving track stub IDs via tracks?ids=... in batches of 50.
        Returns (playlist_dict, list_of_tracks).
        """
        client_id = await self.get_client_id()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        if auth_token:
            headers["Authorization"] = f"OAuth {auth_token.strip()}"

        raw_target = str(playlist_id_or_url).strip()
        proxy = get_proxy_url()

        async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
            if raw_target.startswith("http"):
                # Resolve URL
                resolve_url = f"https://api-v2.soundcloud.com/resolve?url={raw_target}&client_id={client_id}"
                async with session.get(resolve_url, proxy=proxy) as resp:
                    if resp.status != 200:
                        raise ValueError(f"Failed to resolve playlist URL ({resp.status})")
                    playlist_data = await resp.json()
            else:
                # Direct playlist ID
                pl_url = f"https://api-v2.soundcloud.com/playlists/{raw_target}?client_id={client_id}"
                async with session.get(pl_url, proxy=proxy) as resp:
                    if resp.status != 200:
                        raise ValueError(f"Failed to fetch playlist {raw_target} ({resp.status})")
                    playlist_data = await resp.json()

            p_user = playlist_data.get("user") or {}
            author = p_user.get("username") or p_user.get("permalink") or "SoundCloud"
            dur_ms = playlist_data.get("duration") or 0
            duration_sec = int(dur_ms / 1000) if dur_ms > 1000 else int(dur_ms)
            artwork = _improve_sc_thumbnail(playlist_data.get("artwork_url") or p_user.get("avatar_url"))

            playlist_info = {
                "id": str(playlist_data.get("id")),
                "title": playlist_data.get("title") or "SoundCloud Playlist",
                "permalink_url": playlist_data.get("permalink_url") or "",
                "artwork_url": artwork,
                "track_count": int(playlist_data.get("track_count") or len(playlist_data.get("tracks", [])) or 0),
                "duration": duration_sec,
                "author": author,
                "author_avatar": _improve_sc_thumbnail(p_user.get("avatar_url")),
                "description": playlist_data.get("description"),
                "is_public": bool(playlist_data.get("public", True)),
                "created_at": playlist_data.get("created_at"),
            }

            raw_tracks = playlist_data.get("tracks") or []
            resolved_dict: Dict[int, dict] = {}
            stubs_to_fetch: List[int] = []

            for t in raw_tracks:
                if not isinstance(t, dict):
                    continue
                t_id = t.get("id")
                if not t_id:
                    continue
                if t.get("permalink_url") and t.get("title"):
                    resolved_dict[t_id] = t
                else:
                    stubs_to_fetch.append(t_id)

            # Batch fetch missing track stubs in chunks of 50
            if stubs_to_fetch:
                for i in range(0, len(stubs_to_fetch), 50):
                    chunk_ids = stubs_to_fetch[i : i + 50]
                    ids_str = "%2C".join(str(cid) for cid in chunk_ids)
                    batch_url = f"https://api-v2.soundcloud.com/tracks?ids={ids_str}&client_id={client_id}"
                    try:
                        async with session.get(batch_url) as b_resp:
                            if b_resp.status == 200:
                                b_data = await b_resp.json()
                                for bt in b_data:
                                    if isinstance(bt, dict) and bt.get("id"):
                                        resolved_dict[bt["id"]] = bt
                    except Exception as b_err:
                        logger.warning(f"Failed to batch resolve playlist tracks: {b_err}")

            # Assemble TrackMetadata list in original order
            tracks: List[TrackMetadata] = []
            for idx, item in enumerate(raw_tracks, start=1):
                if not isinstance(item, dict):
                    continue
                t_id = item.get("id")
                full_t = resolved_dict.get(t_id) or item
                if not full_t.get("title") and not full_t.get("permalink_url"):
                    continue

                raw_title = full_t.get("title") or f"Track {idx}"
                t_user = full_t.get("user") or {}
                uploader = t_user.get("username") or author
                artist, title = _parse_artist_and_title(raw_title, uploader)

                track_url = full_t.get("permalink_url") or ""
                if not track_url and full_t.get("permalink"):
                    track_url = f"https://soundcloud.com/{t_user.get('permalink', 'artist')}/{full_t.get('permalink')}"
                if not track_url:
                    continue

                t_dur = full_t.get("duration") or 0
                track_duration = int(t_dur / 1000) if t_dur > 1000 else int(t_dur) or None
                cover = _improve_sc_thumbnail(full_t.get("artwork_url") or artwork)

                sc_genre = full_t.get("genre") if isinstance(full_t.get("genre"), str) else None
                sc_tags = _extract_sc_tags(
                    tag_list=full_t.get("tag_list"),
                    description=full_t.get("description"),
                )

                extra_data = {
                    "uploader": uploader,
                    "is_soundcloud": True,
                    "playlist_title": playlist_info["title"],
                }
                if sc_genre and sc_genre.strip():
                    extra_data["genre"] = sc_genre.strip()
                if sc_tags:
                    extra_data["tags"] = sc_tags

                is_album = playlist_info.get("set_type") in ("album", "ep", "compilation") or "album" in playlist_info.get("title", "").lower()
                album_val = playlist_info["title"] if is_album else None

                tracks.append(
                    TrackMetadata(
                        provider_name=self.name,
                        url=track_url,
                        title=title,
                        artist=artist,
                        album=album_val,
                        duration=track_duration,
                        cover_url=cover,
                        track_number=idx,
                        external_id=str(full_t.get("id") or ""),
                        extra=extra_data,
                    )
                )

            return playlist_info, tracks


