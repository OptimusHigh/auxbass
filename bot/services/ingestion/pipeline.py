"""
TG Player - Ingestion Pipeline
Orchestrates downloading, deduplication, Telegram upload, and database registration.
"""
import os
import shutil
import logging
import asyncio
import re
import tempfile
from datetime import datetime, timezone
from typing import Optional, Callable, Any, List, Dict

import aiohttp
from aiogram import Bot
from aiogram.types import FSInputFile, BufferedInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from sqlalchemy import select, and_, or_, func

from shared.config import get_settings, get_proxy_url
from shared.database import get_session
from shared.images import crop_image_to_square
from shared.models import (
    Track,
    Playlist,
    PlaylistTrack,
    UserLibrary,
    LibrarySource,
    ForwardSourceType,
    UserChannel,
)
from shared.matching import (
    normalize_artist,
    normalize_title,
    clean_track_metadata,
    fuzzy_match_artist,
    fuzzy_match_title,
    extract_version_markers,
    extract_version_detail,
    are_version_details_compatible,
    is_bogus_album_name,
    sanitize_album_name,
)

from bot.services.tracks import track_service
from bot.services.albums import album_service
from .base import TrackMetadata, EntityType, SourceEntity
from .job_manager import IngestionJob, JobStatus, job_manager
from .registry import provider_registry

logger = logging.getLogger(__name__)
settings = get_settings()

VERSION_SUFFIX_PATTERN = re.compile(
    r'\s+[-–—]\s*(?:.*?(?:remix|refix|re-fix|mix|edit|version|dub|bootleg|rework|flip|vip|remaster|deluxe).*?)$',
    re.IGNORECASE
)


def _robust_norm_title(t: Optional[str]) -> str:
    if not t:
        return ""
    cleaned = VERSION_SUFFIX_PATTERN.sub('', t)
    return normalize_title(cleaned)


def _score_candidate(
    t: Track,
    clean_title: str,
    clean_artist: str,
    norm_title: str,
    norm_artist: str,
    robust_title: str,
    duration: Optional[int],
    allow_chunk: bool = False,
) -> int:
    is_cand_chunk = bool(getattr(t, "is_chunk", False))
    # Chunks should never be matched when searching for full tracks
    if is_cand_chunk and not allow_chunk:
        return -1

    t_clean_title, t_clean_artist = clean_track_metadata(t.title, t.artist)
    t_norm_artist = t.normalized_artist or normalize_artist(t_clean_artist)
    t_norm_title = normalize_title(t_clean_title)
    t_robust_title = _robust_norm_title(t_clean_title)

    # Check artist compatibility
    artist_match = (
        (norm_artist == t_norm_artist)
        or (norm_artist in t_norm_artist)
        or (t_norm_artist in norm_artist)
        or (fuzzy_match_artist(clean_artist, t_clean_artist) >= 0.75)
    )
    if not artist_match:
        return -1

    dur_diff = abs(t.duration - duration) if (t.duration and duration) else None

    # Strict duration check only applies to full tracks (chunks have 30s duration by design)
    if not is_cand_chunk:
        if duration and t.duration and dur_diff is not None and dur_diff > 7:
            return -1

    # Version markers compatibility check (strictly distinguish live, acoustic, remix, studio)
    t_markers = extract_version_markers(t.title)
    req_markers = extract_version_markers(clean_title)
    if t_markers != req_markers:
        return -1

    # Specific version details compatibility check (distinguish Loadstar Remix vs Xilent Remix, etc.)
    if not are_version_details_compatible(clean_title, t.title):
        return -1

    t_v = extract_version_detail(t.title)
    req_v = extract_version_detail(clean_title)

    score = 0
    if t.title and clean_title and t.title.lower() == clean_title.lower():
        score = 100
    elif t_norm_title and norm_title and t_norm_title == norm_title:
        score = 85 if (t_v and req_v and t_v == req_v) else 80
    elif t_robust_title and robust_title and t_robust_title == robust_title:
        score = 85 if (t_v and req_v and t_v == req_v) else 60
    elif fuzzy_match_title(clean_title, t_clean_title) >= 0.75:
        score = 40
    else:
        return -1

    # Prefer full tracks over chunks
    if is_cand_chunk:
        score = max(1, score - 15)
    elif dur_diff is not None:
        score += max(0, 10 - dur_diff)

    return score


async def _find_existing_track(
    title: str,
    artist: str,
    duration: Optional[int],
    session: Optional[Any] = None,
    allow_chunk: bool = False,
    url: Optional[str] = None,
) -> Optional[Track]:
    """
    Check if track with matching title & artist (or exact source URL) already exists in global library.
    Uses multi-level tolerant recognition (exact -> normalized -> version-stripped -> fuzzy)
    with strict duration tolerance (<= 7s) to prevent duplicate downloads and channel spam.
    When allow_chunk=False (default), 30s preview chunks are never returned.
    """
    clean_title, clean_artist = clean_track_metadata(title, artist)
    norm_artist = normalize_artist(clean_artist)
    norm_title = normalize_title(clean_title)
    robust_title = _robust_norm_title(clean_title)

    async def _execute_lookup(s):
        # 0. Check direct source_url match first if URL provided
        if url:
            url_query = select(Track).where(
                Track.source_url == url,
                Track.is_unavailable == False,
            )
            if not allow_chunk:
                url_query = url_query.where(Track.is_chunk == False)
            url_track = await s.scalar(url_query)
            if url_track:
                # Sanity check: if searching for full track (not allow_chunk) and duration > 45,
                # do not accept a track whose stored duration is <= 35 or file_size < 1MB as a full track!
                if not allow_chunk and duration and duration > 45:
                    if (url_track.duration and url_track.duration <= 35) or (url_track.file_size and url_track.file_size < 1000000 and (url_track.duration or 0) <= 45):
                        url_track = None
                if url_track:
                    return url_track

        # 1. Search candidates by normalized artist or artist match
        query = select(Track).where(
            and_(
                or_(
                    Track.normalized_artist == norm_artist,
                    Track.artist.ilike(f"%{clean_artist}%"),
                ),
                Track.is_unavailable == False,
            )
        )
        if not allow_chunk:
            query = query.where(Track.is_chunk == False)
        result = await s.execute(query)
        candidates = result.scalars().all()

        # 2. If no candidates found by artist, try searching by title
        if not candidates:
            query2 = select(Track).where(
                and_(
                    or_(
                        Track.title.ilike(clean_title),
                        Track.title.ilike(f"%{clean_title}%"),
                    ),
                    Track.is_unavailable == False,
                )
            )
            if not allow_chunk:
                query2 = query2.where(Track.is_chunk == False)
            result2 = await s.execute(query2)
            candidates = result2.scalars().all()

        if not candidates:
            return None

        best_candidate = None
        best_score = -1

        for t in candidates:
            score = _score_candidate(t, clean_title, clean_artist, norm_title, norm_artist, robust_title, duration, allow_chunk=allow_chunk)
            if score > best_score:
                best_score = score
                best_candidate = t

        return best_candidate

    if session:
        return await _execute_lookup(session)
    async with get_session() as new_session:
        return await _execute_lookup(new_session)


async def _find_existing_tracks_batch(
    items: List[Any],
    session: Optional[Any] = None,
) -> List[Optional[Track]]:
    """
    Batch find existing tracks for a list of items using a single indexed query.
    Falls back to individual _find_existing_track only when no batch candidate matches.
    """
    if not items:
        return []

    async def _execute_batch(s):
        prepared = []
        norm_artists = set()
        for item in items:
            title = getattr(item, "title", "") or ""
            artist = getattr(item, "artist", "") or ""
            dur = getattr(item, "duration", None)
            clean_title, clean_artist = clean_track_metadata(title, artist)
            n_art = normalize_artist(clean_artist)
            n_tit = normalize_title(clean_title)
            r_tit = _robust_norm_title(clean_title)
            prepared.append((clean_title, clean_artist, n_tit, n_art, r_tit, dur))
            if n_art:
                norm_artists.add(n_art)

        candidates_by_artist: Dict[str, List[Track]] = {}
        if norm_artists:
            query = select(Track).where(
                and_(
                    Track.normalized_artist.in_(norm_artists),
                    Track.is_unavailable == False,
                    Track.is_chunk == False,
                )
            )
            res = await s.execute(query)
            for t in res.scalars().all():
                norm_a = t.normalized_artist or ""
                if norm_a not in candidates_by_artist:
                    candidates_by_artist[norm_a] = []
                candidates_by_artist[norm_a].append(t)

        results: List[Optional[Track]] = []
        for clean_title, clean_artist, n_tit, n_art, r_tit, dur in prepared:
            candidates = candidates_by_artist.get(n_art, [])
            best_candidate = None
            best_score = -1
            for cand in candidates:
                score = _score_candidate(cand, clean_title, clean_artist, n_tit, n_art, r_tit, dur)
                if score > best_score:
                    best_score = score
                    best_candidate = cand

            if best_candidate:
                results.append(best_candidate)
            else:
                found = await _find_existing_track(clean_title, clean_artist, dur, session=s)
                results.append(found)

        return results

    if session:
        return await _execute_batch(session)
    async with get_session() as new_session:
        return await _execute_batch(new_session)


class IngestionPipeline:
    """Executes an IngestionJob end-to-end."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def execute_job(
        self,
        job: IngestionJob,
        progress_callback: Optional[Callable[[IngestionJob], Any]] = None,
    ):
        """Run the full ingestion pipeline for a given job."""
        job.status = JobStatus.IN_PROGRESS
        job.updated_at = datetime.now(timezone.utc)
        if progress_callback:
            await progress_callback(job)

        provider = provider_registry.get_provider(job.provider_name)
        if not provider:
            job.status = JobStatus.FAILED
            job.error_message = f"Unknown provider: {job.provider_name}"
            job.updated_at = datetime.now(timezone.utc)
            if progress_callback:
                await progress_callback(job)
            return

        try:
            try:
                e_type = EntityType(job.entity_type)
            except (ValueError, KeyError):
                e_type = EntityType.PLAYLIST if (job.playlist_id or (job.total_tracks and job.total_tracks > 1)) else EntityType.TRACK

            entity = SourceEntity(
                provider_name=job.provider_name,
                entity_type=e_type,
                url=job.url,
                title=job.title,
                author=job.author,
                cover_url=job.cover_url,
                track_count=job.total_tracks,
                raw_data=getattr(job, "raw_data", None),
            )

            # 1. Fetch full tracklist (or use pre-parsed custom tracks from Exportify CSV or Likes batch)
            if getattr(job, "custom_tracks", None):
                tracks_meta = []
                for idx, t in enumerate(job.custom_tracks, start=1):
                    extra = dict(t.get("extra") or {})
                    if t.get("genre") and "genre" not in extra:
                        extra["genre"] = t.get("genre")
                    if t.get("tags") and "tags" not in extra:
                        extra["tags"] = t.get("tags")
                    if job.provider_name == "soundcloud":
                        extra["is_soundcloud"] = True

                    tracks_meta.append(
                        TrackMetadata(
                            provider_name=job.provider_name,
                            url=t.get("url") or f"https://open.spotify.com/track/{t.get('external_id') or t.get('id') or idx}",
                            title=t.get("title", "Unknown Track"),
                            artist=t.get("artist", "Unknown Artist"),
                            album=t.get("album"),
                            duration=t.get("duration"),
                            cover_url=t.get("cover_url") or job.cover_url,
                            external_id=str(t.get("external_id") or t.get("id") or idx),
                            extra=extra,
                        )
                    )
            else:
                tracks_meta = await provider.fetch_tracklist(entity)
                # If multiple individual track URLs were selected, resolve the others if not in tracklist
                if job.selected_urls and len(job.selected_urls) > len(tracks_meta):
                    existing_urls = {t.url for t in tracks_meta}
                    for u in job.selected_urls:
                        if u not in existing_urls:
                            try:
                                u_ent = await provider.resolve_entity(u)
                                u_tracks = await provider.fetch_tracklist(u_ent)
                                for ut in u_tracks:
                                    if ut.url not in existing_urls:
                                        tracks_meta.append(ut)
                                        existing_urls.add(ut.url)
                            except Exception as res_err:
                                logger.warning(f"Could not resolve selected URL {u}: {res_err}")

            if not tracks_meta:
                job.status = JobStatus.FAILED
                job.error_message = "No tracks found to import"
                job.updated_at = datetime.now(timezone.utc)
                if progress_callback:
                    await progress_callback(job)
                return

            # Filter tracks if user made a selective import
            if job.selected_urls:
                selected_set = {u.strip().rstrip("/") for u in job.selected_urls}
                if not getattr(job, "custom_tracks", None) or len(selected_set) < len(tracks_meta):
                    filtered = [t for t in tracks_meta if (t.url and t.url.strip().rstrip("/")) in selected_set]
                    if filtered:
                        tracks_meta = filtered

            job.total_tracks = len(tracks_meta)
            job.updated_at = datetime.now(timezone.utc)
            if progress_callback:
                await progress_callback(job)

            # 2. If it's an album, register Album entity in Auxbass
            if job.entity_type == EntityType.ALBUM.value and not job.album_id:
                try:
                    album_artist = job.author or (tracks_meta[0].artist if tracks_meta else "Unknown Artist")
                    job.album_id = await album_service.find_or_create_album(
                        album_name=job.title,
                        artist_name=album_artist,
                        cover_url=job.cover_url,
                        total_tracks=len(tracks_meta),
                    )
                except Exception as alb_err:
                    logger.warning(f"[Ingestion] Could not find_or_create_album: {alb_err}")

            # Also create a playlist record in Auxbass for user playlist browsing
            if job.entity_type in (EntityType.PLAYLIST.value, EntityType.ALBUM.value) and not job.playlist_id:
                async with get_session() as session:
                    playlist = Playlist(
                        owner_id=job.user_id,
                        name=job.title,
                        description=f"Imported from {job.provider_name.title()}: {job.url}",
                        custom_cover_url=job.cover_url,
                        is_public=False,
                    )
                    session.add(playlist)
                    await session.commit()
                    await session.refresh(playlist)
                    job.playlist_id = playlist.id

            # Upload playlist cover to user's Telegram channel if available
            if job.playlist_id:
                async with get_session() as session:
                    playlist = await session.get(Playlist, job.playlist_id)
                    if playlist:
                        cover_candidate = job.cover_url or playlist.custom_cover_url
                        if not cover_candidate and getattr(job, "custom_tracks", None):
                            cover_candidate = job.custom_tracks[0].get("cover_url")
                        if not cover_candidate and tracks_meta and tracks_meta[0].cover_url:
                            cover_candidate = tracks_meta[0].cover_url

                        if cover_candidate and cover_candidate.startswith("http"):
                            try:
                                tg_cover_url = await self._upload_playlist_cover(
                                    session=session,
                                    playlist_id=playlist.id,
                                    user_id=job.user_id,
                                    playlist_name=playlist.name,
                                    cover_url=cover_candidate,
                                )
                                if tg_cover_url:
                                    playlist.custom_cover_url = tg_cover_url
                                    await session.commit()
                                    job.cover_url = tg_cover_url
                            except Exception as e:
                                logger.warning(f"[Ingestion] Could not upload playlist cover: {e}")

            # 3. Target chat for bot upload
            # If buffer chat is configured, send there to avoid spamming the user's DM.
            # Otherwise send to the user's chat.
            target_chat_id = settings.scanner_buffer_chat_id or job.user_id

            # 4. Ingest each track
            for idx, track_meta in enumerate(tracks_meta):
                if job.status == JobStatus.CANCELLED:
                    logger.info(f"Ingestion job {job.id} cancelled by user.")
                    break

                job.current_track_title = f"{track_meta.artist} - {track_meta.title}"
                job.current_step = "Поиск аудио 320 kbps"
                job.download_percent = 0
                job.updated_at = datetime.now(timezone.utc)
                # Sanitize album field: if importing a playlist or likes, do not let playlist name leak into track album
                if job.entity_type == EntityType.ALBUM.value:
                    if not track_meta.album or is_bogus_album_name(track_meta.album):
                        track_meta.album = job.title
                else:
                    if track_meta.album and (track_meta.album == job.title or is_bogus_album_name(track_meta.album)):
                        track_meta.album = None

                try:
                    # A. Deduplication check: exists globally?
                    existing = await _find_existing_track(
                        track_meta.title, track_meta.artist, track_meta.duration
                    )

                    if existing:
                        logger.info(f"[Ingestion] Found existing track {existing.id} ({existing.artist} - {existing.title})")
                        job.current_step = "Трек уже в базе (мгновенно)"
                        job.download_percent = 100
                        job.updated_at = datetime.now(timezone.utc)

                        # Add existing track to user's library
                        await track_service.save_track(
                            user_id=job.user_id,
                            file_id=existing.file_id,
                            file_unique_id=existing.file_unique_id,
                            title=existing.title,
                            artist=existing.artist,
                            duration=existing.duration,
                            library_source=LibrarySource.UPLOADED,
                            enrich=False,
                            cover_url=track_meta.cover_url,
                            genre=track_meta.extra.get("genre"),
                            tags=track_meta.extra.get("tags"),
                            album_name=track_meta.album,
                            source_provider=job.provider_name,
                        )

                        # Add to playlist if playlist import
                        if job.playlist_id:
                            await self._add_track_to_playlist(job.playlist_id, existing.id, len(job.imported_track_ids) + 1)

                        # Assign to album if album import
                        if job.album_id:
                            try:
                                await album_service.assign_track_to_album(
                                    track_id=existing.id,
                                    album_id=job.album_id,
                                    track_number=len(job.imported_track_ids) + 1,
                                )
                            except Exception as alb_err:
                                logger.warning(f"[Ingestion] Failed assigning existing track {existing.id} to album {job.album_id}: {alb_err}")

                        # Auto-forward to user's Telegram backup channel if active
                        try:
                            from bot.services.channels import get_channel_service
                            ch_svc = get_channel_service()
                            if ch_svc:
                                await ch_svc.forward_track_to_channel(job.user_id, existing.id)
                        except Exception as e:
                            logger.debug(f"[Ingestion] Channel forward skipped for existing track {existing.id}: {e}")

                        job.imported_track_ids.append(existing.id)
                        job.skipped_tracks += 1
                        job.processed_tracks += 1
                        job.download_percent = None
                        job.current_step = None
                        job.updated_at = datetime.now(timezone.utc)
                        if progress_callback:
                            await progress_callback(job)
                        continue

                    # B. Download audio to temporary directory
                    with tempfile.TemporaryDirectory() as temp_dir:
                        if job_manager.download_semaphore.locked():
                            job.current_step = "Ожидание очереди загрузки..."
                        else:
                            job.current_step = "Скачивание аудиопотока"
                        job.download_percent = 0
                        job.updated_at = datetime.now(timezone.utc)
                        if progress_callback:
                            await progress_callback(job)

                        def on_download_progress(pct: int):
                            job.download_percent = pct
                            job.current_step = f"Скачивание аудио ({pct}%)"
                            job.updated_at = datetime.now(timezone.utc)

                        async with job_manager.download_semaphore:
                            job.current_step = "Скачивание аудиопотока"
                            downloaded = await provider.download_track(
                                track_meta, temp_dir, progress_hook=on_download_progress
                            )

                        # Check Telegram 50MB limit
                        if downloaded.file_size > 50 * 1024 * 1024:
                            logger.warning(f"[Ingestion] File {downloaded.audio_path} exceeds 50MB ({downloaded.file_size} bytes). Skipping.")
                            job.failed_tracks += 1
                            job.processed_tracks += 1
                            job.download_percent = None
                            job.current_step = None
                            job.updated_at = datetime.now(timezone.utc)
                            continue

                        # C. Upload to Telegram
                        job.current_step = "Загрузка в Telegram"
                        job.download_percent = 100
                        job.updated_at = datetime.now(timezone.utc)

                        effective_meta = downloaded.metadata or track_meta
                        if job.entity_type == EntityType.ALBUM.value:
                            if not effective_meta.album or is_bogus_album_name(effective_meta.album):
                                effective_meta.album = job.title
                        else:
                            if effective_meta.album and (effective_meta.album == job.title or is_bogus_album_name(effective_meta.album)):
                                effective_meta.album = None

                        safe_filename = f"{effective_meta.artist} - {effective_meta.title}.mp3".replace("/", "-")
                        audio_input = FSInputFile(downloaded.audio_path, filename=safe_filename)
                        thumb_input = None
                        if downloaded.cover_path and os.path.exists(downloaded.cover_path):
                            thumb_input = FSInputFile(downloaded.cover_path)

                        sent_msg = None
                        retry_attempts = 3
                        while retry_attempts > 0:
                            try:
                                sent_msg = await self.bot.send_audio(
                                    chat_id=target_chat_id,
                                    audio=audio_input,
                                    title=effective_meta.title,
                                    performer=effective_meta.artist,
                                    duration=effective_meta.duration,
                                    thumbnail=thumb_input,
                                )
                                break
                            except TelegramRetryAfter as e:
                                logger.warning(f"[Ingestion] Flood control hit: wait {e.retry_after}s")
                                await asyncio.sleep(e.retry_after + 1)
                                retry_attempts -= 1
                            except TelegramBadRequest as e:
                                logger.error(f"[Ingestion] Bad request uploading audio: {e}")
                                break

                        if not sent_msg or not sent_msg.audio:
                            job.failed_tracks += 1
                            job.processed_tracks += 1
                            job.download_percent = None
                            job.current_step = None
                            job.updated_at = datetime.now(timezone.utc)
                            continue

                        job.uploaded_chat_id = target_chat_id
                        job.uploaded_message_id = sent_msg.message_id

                        # D. Register in database & trigger enrichment
                        job.current_step = "Сохранение в медиатеку"
                        job.updated_at = datetime.now(timezone.utc)

                        track_cover_url = None
                        if sent_msg and sent_msg.audio and sent_msg.audio.thumbnail:
                            track_cover_url = f"/api/images/{sent_msg.audio.thumbnail.file_id}"
                        elif effective_meta.cover_url:
                            track_cover_url = effective_meta.cover_url

                        save_result = await track_service.save_track(
                            user_id=job.user_id,
                            file_id=sent_msg.audio.file_id,
                            file_unique_id=sent_msg.audio.file_unique_id,
                            title=effective_meta.title,
                            artist=effective_meta.artist,
                            duration=sent_msg.audio.duration or effective_meta.duration,
                            file_size=sent_msg.audio.file_size or downloaded.file_size,
                            mime_type=sent_msg.audio.mime_type or "audio/mpeg",
                            file_name=safe_filename,
                            forward_source_type=ForwardSourceType.BOT,
                            forward_source_name=job.provider_name,
                            library_source=LibrarySource.UPLOADED,
                            enrich=True,
                            cover_url=track_cover_url,
                            genre=effective_meta.extra.get("genre"),
                            tags=effective_meta.extra.get("tags"),
                            album_name=effective_meta.album,
                            source_provider=job.provider_name,
                        )

                        # E. Add to playlist
                        if job.playlist_id:
                            await self._add_track_to_playlist(job.playlist_id, save_result.track_id, len(job.imported_track_ids) + 1)

                        # If album import, assign to album
                        if job.album_id:
                            try:
                                await album_service.assign_track_to_album(
                                    track_id=save_result.track_id,
                                    album_id=job.album_id,
                                    track_number=len(job.imported_track_ids) + 1,
                                )
                            except Exception as alb_err:
                                logger.warning(f"[Ingestion] Failed assigning new track {save_result.track_id} to album {job.album_id}: {alb_err}")

                        # Auto-forward to user's Telegram backup channel if active
                        try:
                            from bot.services.channels import get_channel_service
                            ch_svc = get_channel_service()
                            if ch_svc:
                                await ch_svc.forward_track_to_channel(job.user_id, save_result.track_id)
                        except Exception as e:
                            logger.debug(f"[Ingestion] Channel forward failed for new track {save_result.track_id}: {e}")

                        job.imported_track_ids.append(save_result.track_id)
                        job.processed_tracks += 1
                        job.download_percent = None
                        job.current_step = None
                        job.updated_at = datetime.now(timezone.utc)
                        if progress_callback:
                            await progress_callback(job)

                    # F. Telegram flood delay between tracks
                    if idx < len(tracks_meta) - 1:
                        await asyncio.sleep(1.5)

                except Exception as e:
                    clean_msg = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(e))
                    if "drm protected" in clean_msg.lower() or "защищён drm" in clean_msg.lower():
                        logger.warning(f"[Ingestion] Skipped DRM protected track {track_meta.title}: {clean_msg}")
                    else:
                        logger.error(f"[Ingestion] Failed to import track {track_meta.title}: {clean_msg}")
                    job.failed_tracks += 1
                    job.processed_tracks += 1
                    job.download_percent = None
                    job.current_step = None
                    job.updated_at = datetime.now(timezone.utc)
                    if progress_callback:
                        await progress_callback(job)

            # Done processing all tracks
            if job.status != JobStatus.CANCELLED:
                job.status = JobStatus.COMPLETED
                job.current_track_title = None
                job.current_step = "Импорт завершен"
                job.download_percent = None
                job.updated_at = datetime.now(timezone.utc)
                if progress_callback:
                    await progress_callback(job)

        except Exception as e:
            logger.error(f"[Ingestion] Job {job.id} failed with unhandled error: {e}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.updated_at = datetime.now(timezone.utc)
            if progress_callback:
                await progress_callback(job)

    async def _add_track_to_playlist(self, playlist_id: int, track_id: int, position: int):
        """Associate imported track with the created or target playlist."""
        try:
            async with get_session() as session:
                # Ensure it's not already in playlist
                existing = await session.scalar(
                    select(PlaylistTrack).where(
                        PlaylistTrack.playlist_id == playlist_id,
                        PlaylistTrack.track_id == track_id,
                    )
                )
                if not existing:
                    max_pos = await session.scalar(
                        select(func.coalesce(func.max(PlaylistTrack.position), 0)).where(
                            PlaylistTrack.playlist_id == playlist_id
                        )
                    )
                    pt = PlaylistTrack(
                        playlist_id=playlist_id,
                        track_id=track_id,
                        position=(max_pos or 0) + 1,
                    )
                    session.add(pt)
                    await session.commit()
        except Exception as e:
            logger.warning(f"[Ingestion] Failed to add track {track_id} to playlist {playlist_id}: {e}")

    async def _upload_playlist_cover(
        self,
        session,
        playlist_id: int,
        user_id: int,
        playlist_name: str,
        cover_url: str,
    ) -> Optional[str]:
        """Download playlist cover from external URL and upload to user's Telegram channel or PM."""
        if not cover_url or not cover_url.startswith("http"):
            return None

        content = None
        detected_ext = "jpg"
        proxy = get_proxy_url()
        try:
            async with aiohttp.ClientSession(trust_env=True) as http_client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                }
                async with http_client.get(cover_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), proxy=proxy) as resp:
                    if resp.status != 200:
                        logger.warning(f"[Ingestion] Failed to download playlist cover from {cover_url[:60]}: HTTP {resp.status}")
                        return None
                    content = await resp.read()
                    ct = resp.headers.get("Content-Type", "").lower()
                    if "png" in ct:
                        detected_ext = "png"
                    elif "webp" in ct:
                        detected_ext = "webp"
        except Exception as e:
            logger.warning(f"[Ingestion] Error downloading playlist cover {cover_url[:60]}: {e}")
            return None

        if not content or len(content) < 12:
            return None

        # Crop playlist cover to 1:1 square
        content, detected_ext = crop_image_to_square(content)

        # Resolve target chat: user's backup channel if active, otherwise user PM
        user_channel = await session.scalar(
            select(UserChannel).where(UserChannel.user_id == user_id, UserChannel.is_active == True)
        )
        target_chat_id = user_channel.channel_id if user_channel else user_id
        safe_filename = f"cover_{playlist_id}.{detected_ext}"
        caption = f"🖼 <b>Обложка плейлиста</b>: {playlist_name}\n\n#playlist_{playlist_id} #cover"

        sent_msg = None
        try:
            sent_msg = await self.bot.send_photo(
                chat_id=target_chat_id,
                photo=BufferedInputFile(file=content, filename=safe_filename),
                caption=caption,
            )
        except Exception as e:
            logger.warning(f"[Ingestion] Failed to upload playlist cover to target {target_chat_id}: {e}")
            if user_channel and target_chat_id != user_id:
                try:
                    sent_msg = await self.bot.send_photo(
                        chat_id=user_id,
                        photo=BufferedInputFile(file=content, filename=safe_filename),
                        caption=caption,
                    )
                except Exception as err:
                    logger.error(f"[Ingestion] Failed to upload playlist cover to user PM fallback: {err}")
                    return None
            else:
                return None

        photos = sent_msg.photo if sent_msg else []
        if not photos:
            return None

        file_id = photos[-1].file_id
        logger.info(f"[Ingestion] Uploaded playlist {playlist_id} cover to Telegram: file_id={file_id[:20]}...")
        return f"/api/images/{file_id}"
