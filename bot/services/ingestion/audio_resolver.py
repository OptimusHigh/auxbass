"""
TG Player - Unified Audio Sourcing Engine
Finds and downloads unencrypted high-quality audio streams matching
metadata (artist, title, duration) for Spotify tracks, SoundCloud DRM fallbacks,
and YouTube Music fallback audio sourcing.
"""
import os
import re
import logging
import asyncio
import tempfile
from typing import Optional, List, Set, Callable, Tuple

import aiohttp
import yt_dlp
from yt_dlp.utils import download_range_func

from shared.config import get_settings, get_proxy_url
from shared.matching import (
    clean_track_metadata,
    fuzzy_match_artist,
    fuzzy_match_title,
    extract_version_markers,
    are_version_details_compatible,
    ARTIST_MATCH_THRESHOLD,
    TITLE_MATCH_THRESHOLD,
)
from .base import TrackMetadata, DownloadedAudio, calculate_preview_range

logger = logging.getLogger(__name__)


class AudioResolver:
    """
    Resolves an unencrypted audio stream for any given TrackMetadata,
    matching duration, artist, and title, and downloads it in 320kbps MP3.
    Features candidate retry loop and automatic fallback to YouTube Music on DRM.
    """

    MAX_DURATION_DIFF_SECONDS = 15

    def _get_ydl_opts(self, extra: Optional[dict] = None) -> dict:
        settings = get_settings()
        opts = {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": settings.ytdlp_timeout,
            "retries": 5,
            "fragment_retries": 10,
            "file_access_retries": 3,
            "extractor_retries": 3,
        }
        proxy = get_proxy_url()
        if proxy:
            opts["proxy"] = proxy
        if extra:
            opts.update(extra)
        return opts

    async def resolve_and_download(
        self,
        track_meta: TrackMetadata,
        temp_dir: str,
        exclude_urls: Optional[Set[str]] = None,
        progress_hook: Optional[Callable[[int], None]] = None,
        chunk_only: bool = False,
        chunk_duration: int = 30,
        chunk_start: Optional[int] = None,
    ) -> DownloadedAudio:
        """
        Find an alternative unencrypted audio stream matching the track,
        download it at 320kbps MP3 (or 192kbps 30s chunk), and tag it with original metadata and cover.
        Tries SoundCloud candidates with retry loop, then falls back to YouTube Music.
        """
        clean_title = (track_meta.title or "").strip().lower()
        clean_artist = (track_meta.artist or "").strip().lower()
        if clean_title in ("spotify item", "unknown track", "") or clean_artist in ("artist", "unknown artist", ""):
            raise ValueError(
                f"Невозможно подобрать аудио: некорректные метаданные '{track_meta.artist} - {track_meta.title}'. "
                f"Проверьте настройки прокси (PROXY_URL в .env) для корректного получения информации о треке."
            )

        os.makedirs(temp_dir, exist_ok=True)
        exclude_set = set(exclude_urls or set())
        if track_meta.url:
            exclude_set.add(track_meta.url)

        search_query = f"{track_meta.artist} {track_meta.title}".strip()
        first_artist = track_meta.artist.split(",")[0].split(" feat")[0].split(" ft")[0].strip()
        downloaded_audio_path = None
        matched_candidate_title = None
        matched_candidate_url = None

        # =========================================================================
        # PHASE 1: Try SoundCloud candidates (with retry loop over multiple entries)
        # =========================================================================
        sc_candidates = await self._search_candidates(search_query, limit=10)
        ranked_sc = self._rank_candidates(track_meta, sc_candidates, exclude_set)

        if not ranked_sc and first_artist != track_meta.artist:
            alt_query = f"{first_artist} {track_meta.title}".strip()
            more_sc = await self._search_candidates(alt_query, limit=10)
            ranked_sc = self._rank_candidates(track_meta, more_sc, exclude_set)

        is_studio = not bool(extract_version_markers(track_meta.title))

        for cand in ranked_sc:
            # If user wanted studio version, do not settle for a mismatched candidate if YouTube can have studio
            if is_studio and cand.get("_penalty", 0) > 150:
                logger.info(
                    f"[AudioResolver] Skipping mismatched SoundCloud candidate '{cand.get('title')}' "
                    f"(penalty={cand.get('_penalty')}), checking other candidates or YouTube..."
                )
                continue

            cand_url = cand.get("webpage_url") or cand.get("url")
            cand_title = cand.get("title") or "SoundCloud Track"
            try:
                logger.info(
                    f"[AudioResolver] Trying SoundCloud candidate for '{track_meta.artist} - {track_meta.title}': "
                    f"'{cand_title}' ({cand_url})"
                )
                downloaded_audio_path = await self._download_stream(
                    cand_url, temp_dir, progress_hook=progress_hook,
                    chunk_only=chunk_only, chunk_duration=chunk_duration,
                    chunk_start=chunk_start, total_duration=track_meta.duration,
                )
                matched_candidate_title = cand_title
                matched_candidate_url = cand_url
                break
            except Exception as e:
                err_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(e))
                logger.warning(f"[AudioResolver] SoundCloud candidate '{cand_title}' failed: {err_clean}. Trying next candidate...")

        # =========================================================================
        # PHASE 2: Fallback to YouTube Music / YouTube
        # =========================================================================
        if not downloaded_audio_path:
            logger.info(
                f"[AudioResolver] SoundCloud candidates unavailable, DRM-protected, or version-mismatched for "
                f"'{track_meta.artist} - {track_meta.title}'. Falling back to YouTube Music..."
            )
            yt_candidates = await self._search_youtube_candidates(search_query, limit=10)
            ranked_yt = self._rank_candidates(track_meta, yt_candidates, exclude_set)

            # Ensure we have resilient alternative candidates if primary pool is small or sub-optimal
            best_penalty = ranked_yt[0].get("_penalty", 999.0) if ranked_yt else 999.0
            if len(ranked_yt) < 3 or best_penalty > 80.0:
                more_queries = []
                if is_studio:
                    more_queries.append(f"{first_artist} {track_meta.title} audio".strip())
                if first_artist != track_meta.artist:
                    more_queries.append(f"{first_artist} {track_meta.title}".strip())
                more_queries.append(f"{track_meta.artist} {track_meta.title} topic".strip())

                seen_urls = {c.get("webpage_url") or c.get("url") for c in ranked_yt}
                for q in more_queries:
                    try:
                        more_yt = await self._search_youtube_candidates(q, limit=6)
                        for mc in self._rank_candidates(track_meta, more_yt, exclude_set):
                            mc_url = mc.get("webpage_url") or mc.get("url")
                            if mc_url and mc_url not in seen_urls:
                                seen_urls.add(mc_url)
                                ranked_yt.append(mc)
                    except Exception as q_err:
                        logger.debug(f"[AudioResolver] Auxiliary YouTube query '{q}' failed: {q_err}")

                ranked_yt.sort(key=lambda x: x.get("_penalty", 999.0))

            for cand in ranked_yt:
                cand_url = cand.get("webpage_url") or cand.get("url")
                if not cand_url and cand.get("id"):
                    cand_url = f"https://www.youtube.com/watch?v={cand['id']}"
                cand_title = cand.get("title") or "YouTube Track"
                try:
                    logger.info(
                        f"[AudioResolver] Trying YouTube candidate for '{track_meta.artist} - {track_meta.title}': "
                        f"'{cand_title}' ({cand_url})"
                    )
                    downloaded_audio_path = await self._download_stream(
                        cand_url, temp_dir, progress_hook=progress_hook,
                        chunk_only=chunk_only, chunk_duration=chunk_duration,
                        chunk_start=chunk_start, total_duration=track_meta.duration,
                    )
                    matched_candidate_title = cand_title
                    matched_candidate_url = cand_url
                    break
                except Exception as e:
                    err_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(e))
                    logger.warning(f"[AudioResolver] YouTube candidate '{cand_title}' failed: {err_clean}. Trying next candidate...")

        if not downloaded_audio_path or not os.path.exists(downloaded_audio_path):
            raise ValueError(
                f"Не удалось найти доступный незашифрованный аудиопоток для '{track_meta.artist} - {track_meta.title}'."
            )

        logger.info(
            f"[AudioResolver] Successfully sourced audio for '{track_meta.artist} - {track_meta.title}' "
            f"via '{matched_candidate_title}' ({matched_candidate_url})"
        )

        # Download high-quality cover artwork if available
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
                logger.warning(f"[AudioResolver] Failed to download cover for {track_meta.title}: {e}")
                cover_path = None

        if not cover_path:
            track_meta.cover_url = None

        file_size = os.path.getsize(downloaded_audio_path)

        if chunk_only:
            if track_meta.duration and track_meta.duration > chunk_duration:
                track_meta.extra["full_duration"] = track_meta.duration
            track_meta.duration = chunk_duration
            track_meta.extra["is_chunk"] = True

        return DownloadedAudio(
            audio_path=downloaded_audio_path,
            cover_path=cover_path,
            metadata=track_meta,
            file_size=file_size,
            mime_type="audio/mpeg",
        )

    async def _search_candidates(self, query: str, limit: int = 10) -> List[dict]:
        """Search SoundCloud for potential unencrypted candidate streams."""
        def _search():
            ydl_opts = self._get_ydl_opts({
                "extract_flat": True,
                "skip_download": True,
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                res = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                return res.get("entries") or []

        try:
            return await asyncio.to_thread(_search)
        except Exception as e:
            logger.warning(f"[AudioResolver] SoundCloud candidate search failed for '{query}': {e}")
            return []

    async def _search_youtube_candidates(self, query: str, limit: int = 6) -> List[dict]:
        """Search YouTube / YouTube Music for potential candidate streams."""
        def _search_yt():
            ydl_opts = self._get_ydl_opts({
                "extract_flat": True,
                "skip_download": True,
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    res = ydl.extract_info(f"ytmsearch{limit}:{query}", download=False)
                    entries = res.get("entries") or []
                    if entries:
                        return entries
                except Exception as ytm_err:
                    logger.debug(f"[AudioResolver] ytmsearch failed, falling back to ytsearch: {ytm_err}")

                res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                return res.get("entries") or []

        try:
            return await asyncio.to_thread(_search_yt)
        except Exception as e:
            logger.warning(f"[AudioResolver] YouTube candidate search failed for '{query}': {e}")
            return []

    def _rank_candidates(
        self,
        target: TrackMetadata,
        candidates: List[dict],
        exclude_urls: Set[str],
    ) -> List[dict]:
        """Score candidates based on duration match, title similarity, and version alignment (live vs studio)."""
        valid_candidates: List[Tuple[float, dict]] = []

        target_markers = extract_version_markers(target.title)
        if target.extra and target.extra.get("tags"):
            tags_list = target.extra.get("tags")
            if isinstance(tags_list, list):
                target_markers |= extract_version_markers(" ".join(str(t) for t in tags_list if t))

        for c in candidates:
            if not isinstance(c, dict):
                continue
            cand_url = c.get("webpage_url") or c.get("url") or ""
            if not cand_url and c.get("id"):
                cand_url = f"https://www.youtube.com/watch?v={c['id']}"

            if not cand_url or cand_url in exclude_urls:
                continue

            cand_dur = int(c.get("duration") or 0)
            cand_title = c.get("title") or ""
            cand_uploader = (c.get("uploader") or c.get("channel") or "")

            # Check duration difference
            if target.duration and cand_dur:
                diff = abs(cand_dur - target.duration)
                if diff > self.MAX_DURATION_DIFF_SECONDS:
                    # Allow relaxed fallback tier up to 35s diff if title is a high-confidence match
                    clean_target_t = target.title.lower()
                    clean_cand_t = cand_title.lower()
                    if diff <= 35 and (fuzzy_match_title(target.title, cand_title) >= 0.7 or clean_target_t in clean_cand_t):
                        diff += 60.0  # Heavy penalty so exact duration matches take priority
                    else:
                        continue
            else:
                diff = 10  # neutral penalty if duration unknown

            # Check title similarity
            title_score = fuzzy_match_title(target.title, cand_title)
            if title_score < 0.35 and target.title.lower() not in cand_title.lower():
                continue

            cand_markers = extract_version_markers(cand_title)

            # Version alignment: distinguish studio vs live/acoustic/remix
            version_penalty = 0.0
            version_bonus = 0.0

            if target_markers != cand_markers or not are_version_details_compatible(target.title, cand_title):
                # Version mismatch (e.g. user wants studio, candidate is live, or vice-versa, or different remix)
                version_penalty = 250.0
            else:
                # Both versions agree (e.g. both are live, or both are studio, or same remix)
                if target_markers:
                    version_bonus -= 15.0

            # Studio official upload bonuses (Artist - Topic or Official Audio)
            if not target_markers:
                if cand_uploader.lower().endswith(" - topic"):
                    version_bonus -= 30.0  # Official album upload by YouTube Music
                elif "(official audio)" in cand_title.lower() or "[official audio]" in cand_title.lower():
                    version_bonus -= 20.0
                elif "(audio)" in cand_title.lower() or "[audio]" in cand_title.lower():
                    version_bonus -= 10.0

            # Total penalty: lower is better
            total_penalty = diff + (1.0 - title_score) * 20 + version_penalty + version_bonus
            c_with_score = dict(c)
            c_with_score["_penalty"] = total_penalty
            valid_candidates.append((total_penalty, c_with_score))

        valid_candidates.sort(key=lambda x: x[0])
        return [c for _, c in valid_candidates]

    def _pick_best_candidate(
        self,
        target: TrackMetadata,
        candidates: List[dict],
        exclude_urls: Set[str],
    ) -> Optional[dict]:
        """Score candidates and return the single best one (for backwards compatibility)."""
        ranked = self._rank_candidates(target, candidates, exclude_urls)
        return ranked[0] if ranked else None

    async def _download_stream(
        self,
        url: str,
        temp_dir: str,
        progress_hook: Optional[Callable[[int], None]] = None,
        chunk_only: bool = False,
        chunk_duration: int = 30,
        chunk_start: Optional[int] = None,
        total_duration: Optional[int] = None,
    ) -> str:
        """Download candidate audio stream to MP3 at 320kbps (or 192kbps 30s preview chunk)."""
        out_template = os.path.join(temp_dir, "resolved_audio.%(ext)s")

        # Calculate preview range avoiding empty intros
        start_sec, end_sec = (0, chunk_duration)
        if chunk_only:
            if chunk_start is not None:
                start_sec = max(0, chunk_start)
                end_sec = start_sec + chunk_duration
            else:
                start_sec, end_sec = calculate_preview_range(total_duration, chunk_seconds=chunk_duration)
            logger.info(
                f"[AudioResolver] Downloading {chunk_duration}s preview chunk for stream {url} "
                f"(range: {start_sec}s..{end_sec}s)"
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

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            def _dl():
                ydl_opts = self._get_ydl_opts({
                    "format": "bestaudio/best",
                    "outtmpl": out_template,
                    # Single fragment connection on retry to avoid TLS handshake congestion
                    "concurrent_fragment_downloads": 1 if attempt > 1 else 3,
                    "color": "never",
                    "progress_hooks": [_yt_progress] if progress_hook else [],
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192" if chunk_only else "0",
                        }
                    ],
                })
                if chunk_only:
                    ydl_opts["download_ranges"] = download_range_func(None, [(start_sec, end_sec)])
                    ydl_opts["force_keyframes_at_cuts"] = True

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

            try:
                await asyncio.to_thread(_dl)
                break
            except Exception as dl_err:
                err_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(dl_err))
                err_lower = err_clean.lower()
                is_net_err = any(k in err_lower for k in ("_ssl", "handshake", "timed out", "timeout", "connection", "transport"))
                if attempt < max_attempts and is_net_err:
                    logger.warning(
                        f"[AudioResolver] Transient network/SSL glitch downloading '{url}' (attempt {attempt}/{max_attempts}): {err_clean}. "
                        f"Retrying with single-connection mode..."
                    )
                    await asyncio.sleep(1.5)
                    continue
                raise dl_err

        expected_audio = os.path.join(temp_dir, "resolved_audio.mp3")
        if not os.path.exists(expected_audio):
            for f in os.listdir(temp_dir):
                if f.lower().endswith((".mp3", ".m4a", ".opus", ".ogg")):
                    expected_audio = os.path.join(temp_dir, f)
                    break

        if not os.path.exists(expected_audio):
            raise FileNotFoundError(f"Failed to extract audio from resolved stream: {url}")

        return expected_audio


# Global singleton instance
audio_resolver = AudioResolver()
