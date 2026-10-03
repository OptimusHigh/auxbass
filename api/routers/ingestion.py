"""
TG Player API - External Music Ingestion Router
Provides endpoints for link preview, background import, and job tracking.
"""
import os
import re
import logging
import asyncio
import tempfile
import io
import csv
import json
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, BufferedInputFile
from aiogram.client.default import DefaultBotProperties
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.config import get_settings
from shared.bot_factory import create_bot
from shared.database import get_session, get_db
from shared.models import (
    User, Track, UserLibrary, AlbumTrack, Playlist, PlaylistTrack, LibrarySource,
    ForwardSourceType, UserExternalAccount, UserImportFile, UserChannel, ChannelMessage, ChannelMessageStatus, utcnow
)
from shared.matching import is_bogus_album_name
from api.routers.auth import get_current_user, TelegramUser
from api.schemas.tracks import TrackResponse
from api.utils.responses import track_to_response
from bot.services.tracks import track_service

from bot.services.ingestion import (
    provider_registry,
    job_manager,
    IngestionPipeline,
    JobStatus,
    EntityType,
    TrackMetadata,
)
from bot.services.ingestion.pipeline import _find_existing_track, _find_existing_tracks_batch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])
settings = get_settings()

class SearchItemResponse(BaseModel):
    provider: str
    url: str
    title: str
    artist: str
    album: Optional[str] = None
    duration: Optional[int] = None
    cover_url: Optional[str] = None
    external_id: Optional[str] = None
    genre: Optional[str] = None
    tags: Optional[List[str]] = None
    in_library: bool = False
    in_channel: bool = False
    already_in_tg: bool = False
    track_id: Optional[int] = None
    is_drm_preview: bool = False  # True if this is a DRM-protected preview snippet (SoundCloud Go+)


class QuickImportRequest(BaseModel):
    url: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration: Optional[int] = None
    cover_url: Optional[str] = None
    genre: Optional[str] = None
    tags: Optional[List[str]] = None
    add_to_library: bool = False
    preview_only: bool = False


class QuickImportResponse(BaseModel):
    track: TrackResponse
    already_existed: bool


# ============== External Account Schemas (canonical source: api.schemas.social) ==============
from api.schemas.social import (
    ExternalAccountResponse,
    ExternalAccountPrivacyUpdate,
    SoundCloudLikeItem,
    SoundCloudTrackItem,
    SoundCloudPlaylistItem,
    UserLikesResponse,
    UserTracksResponse,
    UserPlaylistsResponse,
    PlaylistTracksResponse,
    ConnectAccountRequest,
)


def _account_to_response(account: UserExternalAccount) -> ExternalAccountResponse:
    """Format UserExternalAccount DB model into ExternalAccountResponse with privacy settings."""
    return ExternalAccountResponse(
        provider=account.provider,
        username=account.username,
        display_name=account.display_name,
        profile_url=account.profile_url,
        permalink_url=account.profile_url,
        avatar_url=account.avatar_url,
        likes_count=account.likes_count,
        tracks_count=account.tracks_count,
        connected=True,
        last_synced_at=account.last_synced_at.isoformat() if account.last_synced_at else None,
        show_on_profile=getattr(account, "show_on_profile", True) if getattr(account, "show_on_profile", None) is not None else True,
        show_playlists=getattr(account, "show_playlists", True) if getattr(account, "show_playlists", None) is not None else True,
        show_tracks=getattr(account, "show_tracks", True) if getattr(account, "show_tracks", None) is not None else True,
    )



class TrackPreviewItem(BaseModel):
    url: str
    title: str
    artist: str
    album: Optional[str] = None
    duration: Optional[int] = None
    cover_url: Optional[str] = None
    genre: Optional[str] = None
    tags: Optional[List[str]] = None
    in_library: bool = False
    already_in_tg: bool = False
    track_id: Optional[int] = None


class ExportifyTrackItem(BaseModel):
    url: str
    title: str
    artist: str
    album: Optional[str] = None
    duration: Optional[int] = None
    cover_url: Optional[str] = None
    track_uri: Optional[str] = None
    isrc: Optional[str] = None
    in_library: bool = False
    in_channel: bool = False
    already_in_tg: bool = False
    track_id: Optional[int] = None
    liked_at: Optional[str] = None


class ExportifyPreviewResponse(BaseModel):
    filename: str
    total_tracks: int
    new_tracks_count: int
    in_library_count: int
    in_channel_count: int
    already_in_tg_count: int
    tracks: List[ExportifyTrackItem]
    file_id: Optional[str] = None
    import_file_id: Optional[int] = None


class ExportifyStartRequest(BaseModel):
    title: Optional[str] = "Spotify Import"
    tracks: List[ExportifyTrackItem]
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    target_playlist_id: Optional[int] = None
    cover_url: Optional[str] = None


class PreviewRequest(BaseModel):
    url: str


class PreviewResponse(BaseModel):
    provider: str
    entity_type: str
    url: str
    title: str
    author: Optional[str] = None
    cover_url: Optional[str] = None
    track_count: int = 1
    tracks: List[TrackPreviewItem] = []
    is_album: bool = False


class StartImportRequest(BaseModel):
    url: str
    title: Optional[str] = None
    selected_urls: Optional[List[str]] = None
    tracks: Optional[List[Dict[str, Any]]] = None
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    cover_url: Optional[str] = None


class JobResponse(BaseModel):
    id: str
    user_id: int
    url: str
    provider_name: str
    entity_type: str
    title: str
    author: Optional[str] = None
    cover_url: Optional[str] = None
    total_tracks: int
    processed_tracks: int
    skipped_tracks: int
    failed_tracks: int
    current_track_title: Optional[str] = None
    current_step: Optional[str] = None
    download_percent: Optional[int] = None
    status: str
    progress_percent: int
    error_message: Optional[str] = None
    playlist_id: Optional[int] = None
    album_id: Optional[int] = None
    imported_track_ids: List[int] = []
    selected_urls: Optional[List[str]] = None
    created_at: str
    updated_at: str


def _get_active_bot() -> Bot:
    """Retrieve active global Bot instance from api.main or create fallback."""
    import api.main
    if getattr(api.main, "api_bot", None):
        return api.main.api_bot
    return create_bot()


async def _ensure_user_in_db(user: TelegramUser):
    """Ensure user exists in database."""
    async with get_session() as session:
        db_user = await session.get(User, user.id)
        if not db_user:
            db_user = User(
                id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            session.add(db_user)
            await session.commit()


@router.post("/preview", response_model=PreviewResponse)
async def preview_url(
    req: PreviewRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Inspect an external music URL (SoundCloud, etc.) and return its metadata with tracks and library status."""
    url = req.url.strip()
    provider = provider_registry.find_provider(url)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported URL. Supported providers: {', '.join(provider_registry.list_providers())}",
        )

    try:
        entity = await provider.resolve_entity(url)
        tracks_meta = await provider.fetch_tracklist(entity)

        # Batch check against database
        # 1. Fetch all track IDs in this user's library
        user_lib_res = await db.execute(
            select(UserLibrary.track_id).where(UserLibrary.user_id == user.id)
        )
        user_track_ids = set(user_lib_res.scalars().all())

        is_album = (entity.entity_type == EntityType.ALBUM)
        preview_tracks: List[TrackPreviewItem] = []
        for t in tracks_meta:
            existing = await _find_existing_track(t.title, t.artist, t.duration, session=db)
            already_in_tg = existing is not None
            in_lib = (existing.id in user_track_ids) if existing else False
            existing_id = existing.id if existing else None

            # Clean album name if not album entity
            clean_album = t.album
            if not is_album and clean_album and (clean_album == entity.title or is_bogus_album_name(clean_album)):
                clean_album = None

            preview_tracks.append(
                TrackPreviewItem(
                    url=t.url,
                    title=t.title,
                    artist=t.artist,
                    album=clean_album,
                    duration=t.duration,
                    cover_url=t.cover_url,
                    genre=t.extra.get("genre") if t.extra else None,
                    tags=t.extra.get("tags") if t.extra else None,
                    in_library=in_lib,
                    already_in_tg=already_in_tg,
                    track_id=existing_id,
                )
            )

        return PreviewResponse(
            provider=entity.provider_name,
            entity_type=entity.entity_type.value,
            url=entity.url,
            title=entity.title,
            author=entity.author,
            cover_url=entity.cover_url,
            track_count=len(preview_tracks) if preview_tracks else entity.track_count,
            tracks=preview_tracks,
            is_album=is_album,
        )
    except Exception as e:
        logger.error(f"Error resolving preview for {url}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not resolve link: {str(e)}",
        )


@router.post("/start", response_model=JobResponse)
async def start_import(
    req: StartImportRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start an asynchronous background import of a track or playlist."""
    url = req.url.strip()
    provider = provider_registry.find_provider(url)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported music source URL",
        )

    await _ensure_user_in_db(user)

    selected_urls = req.selected_urls
    custom_tracks = req.tracks or None

    entity = None
    try:
        entity = await provider.resolve_entity(url)
    except Exception as e:
        if not custom_tracks:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to inspect URL: {str(e)}",
            )
        # Fallback to constructing SourceEntity from custom_tracks if resolve fails
        entity = SourceEntity(
            provider_name=provider.name,
            entity_type=EntityType.PLAYLIST if (req.create_playlist or len(custom_tracks) > 1) else EntityType.TRACK,
            url=url,
            title=req.title or (custom_tracks[0].get("title") if custom_tracks else "Music Import"),
            author=custom_tracks[0].get("artist") if custom_tracks else "Artist",
            cover_url=custom_tracks[0].get("cover_url") if custom_tracks else None,
            track_count=len(custom_tracks),
        )

    total_tracks = len(custom_tracks) if custom_tracks else (len(selected_urls) if selected_urls else entity.track_count)
    job_title = req.title or (f"SoundCloud Likes ({total_tracks})" if (selected_urls and len(selected_urls) > 1 and entity.entity_type == EntityType.TRACK) else entity.title)

    cover_candidate = req.cover_url or entity.cover_url or (custom_tracks[0].get("cover_url") if custom_tracks and custom_tracks[0].get("cover_url") else None)
    playlist_id = None
    if req.create_playlist:
        p_name = req.playlist_name or req.title or entity.title or f"{provider.name.title()} Playlist"
        new_pl = Playlist(
            owner_id=user.id,
            name=p_name,
            description=f"Синхронизировано из {provider.name.title()} ({url})",
            custom_cover_url=cover_candidate,
            is_public=False,
        )
        db.add(new_pl)
        await db.commit()
        await db.refresh(new_pl)
        playlist_id = new_pl.id

    job = await job_manager.create_job(
        user_id=user.id,
        url=url,
        provider_name=provider.name,
        entity_type=entity.entity_type.value,
        title=job_title,
        total_tracks=total_tracks,
        author=entity.author,
        cover_url=cover_candidate,
        selected_urls=selected_urls,
        custom_tracks=custom_tracks,
        raw_data=entity.raw_data,
    )
    if playlist_id:
        job.playlist_id = playlist_id

    bot = _get_active_bot()
    pipeline = IngestionPipeline(bot)

    # Spawn background task
    task = asyncio.create_task(pipeline.execute_job(job))
    job_manager.register_task(job.id, task)

    return JobResponse(**job.to_dict())


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """Check current status and progress of an import job."""
    job = job_manager.get_job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    return JobResponse(**job.to_dict())


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    user: TelegramUser = Depends(get_current_user),
):
    """Cancel an ongoing import job."""
    job = job_manager.get_job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    cancelled = job_manager.cancel_job(job_id)
    return {"success": cancelled, "job_id": job_id, "status": job.status.value}


@router.get("/recent", response_model=List[JobResponse])
async def get_recent_jobs(
    user: TelegramUser = Depends(get_current_user),
):
    """List recent import jobs for the current user."""
    jobs = job_manager.get_user_jobs(user.id, limit=10)
    return [JobResponse(**j.to_dict()) for j in jobs]


@router.get("/search", response_model=List[SearchItemResponse])
async def search_external_tracks(
    q: str = Query(..., min_length=1),
    provider: str = Query("soundcloud"),
    limit: int = Query(30, ge=1, le=100),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search external music platforms (SoundCloud, Spotify, YouTube) with user library status."""
    if provider == "all":
        results = []
        tasks = []
        for p_name in ["spotify", "soundcloud", "youtube"]:
            p = provider_registry.get_provider(p_name)
            if p:
                tasks.append(p.search(q, limit=min(limit, 15)))
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for d in done:
            if isinstance(d, list):
                results.extend(d)
    else:
        prov = provider_registry.get_provider(provider)
        if not prov:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provider '{provider}' not found",
            )
        results = await prov.search(q, limit=limit)

    if not results:
        return []

    # Batch check against database
    user_lib_res = await db.execute(
        select(UserLibrary.track_id).where(UserLibrary.user_id == user.id)
    )
    user_track_ids = set(user_lib_res.scalars().all())

    existing_tracks = await _find_existing_tracks_batch(results, session=db)
    items: List[SearchItemResponse] = []
    for r, existing in zip(results, existing_tracks):
        already_in_tg = existing is not None
        in_lib = (existing.id in user_track_ids) if existing else False
        existing_id = existing.id if existing else None

        items.append(
            SearchItemResponse(
                provider=r.provider_name,
                url=r.url,
                title=r.title,
                artist=r.artist,
                album=r.album,
                duration=r.duration,
                cover_url=r.cover_url,
                external_id=r.external_id,
                genre=r.extra.get("genre") if r.extra else None,
                tags=r.extra.get("tags") if r.extra else None,
                in_library=in_lib,
                already_in_tg=already_in_tg,
                track_id=existing_id,
                is_drm_preview=bool(r.extra.get("is_drm_preview")) if r.extra else False,
            )
        )
    return items


@router.post("/quick-import", response_model=QuickImportResponse)
async def quick_import_track(
    req: QuickImportRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    On-demand download & import of a single track from external search results.
    If already exists in DB, immediately links to library without downloading.
    Returns full TrackResponse so player can play it immediately.
    """
    await _ensure_user_in_db(user)

    # 1. Deduplication check
    existing_full = None
    existing_chunk = None

    if req.title and req.artist:
        # Check if full track already exists
        existing_full = await _find_existing_track(
            req.title, req.artist, req.duration, session=db, allow_chunk=False, url=req.url
        )
        if not existing_full:
            # Check if 30s preview chunk already exists
            existing_chunk = await _find_existing_track(
                req.title, req.artist, req.duration, session=db, allow_chunk=True, url=req.url
            )

    # Case A: If user requested preview_only and we already have full track or chunk in DB -> instant response!
    if req.preview_only and (existing_full or existing_chunk):
        reused_track = existing_full or existing_chunk
        track_obj = await db.scalar(
            select(Track)
            .where(Track.id == reused_track.id)
            .options(
                selectinload(Track.enrichment),
                selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
            )
        )
        lib_entry = await db.scalar(
            select(UserLibrary)
            .where(UserLibrary.user_id == user.id, UserLibrary.track_id == reused_track.id)
        )
        return QuickImportResponse(
            track=track_to_response(track_obj, lib_entry),
            already_existed=True,
        )

    # Case B: If user wants full track (add_to_library=True) and full track already exists in DB
    if not req.preview_only and existing_full:
        if req.add_to_library:
            await track_service.save_track(
                user_id=user.id,
                file_id=existing_full.file_id,
                file_unique_id=existing_full.file_unique_id,
                title=existing_full.title,
                artist=existing_full.artist,
                duration=existing_full.duration,
                library_source=LibrarySource.UPLOADED,
                enrich=False,
                add_to_library=True,
                cover_url=req.cover_url,
                genre=req.genre,
                tags=req.tags,
                source_provider=(
                    "soundcloud" if "soundcloud" in req.url
                    else ("youtube" if any(x in req.url for x in ("youtube", "youtu.be"))
                    else ("spotify" if "spotify" in req.url else None))
                ),
            )

            try:
                from bot.services.channels import get_channel_service
                ch_svc = get_channel_service()
                if ch_svc:
                    await ch_svc.forward_track_to_channel(user.id, existing_full.id)
            except Exception as e:
                logger.debug(f"Quick-import channel forward failed for existing track: {e}")

        track_obj = await db.scalar(
            select(Track)
            .where(Track.id == existing_full.id)
            .options(
                selectinload(Track.enrichment),
                selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
            )
        )
        lib_entry = await db.scalar(
            select(UserLibrary)
            .where(UserLibrary.user_id == user.id, UserLibrary.track_id == existing_full.id)
        )
        return QuickImportResponse(
            track=track_to_response(track_obj, lib_entry),
            already_existed=True,
        )

    # 2. Download via provider (either 30s preview chunk or full track)
    provider = provider_registry.find_provider(req.url)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported track URL",
        )

    extra_data = {}
    if req.genre:
        extra_data["genre"] = req.genre
    if req.tags:
        extra_data["tags"] = req.tags
    if "soundcloud" in req.url:
        extra_data["is_soundcloud"] = True

    track_meta = TrackMetadata(
        provider_name=provider.name,
        url=req.url,
        title=req.title or "Track",
        artist=req.artist or "Artist",
        duration=req.duration,
        cover_url=req.cover_url,
        extra=extra_data,
    )

    is_chunk_download = bool(req.preview_only)

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            downloaded = await provider.download_track(
                track_meta,
                temp_dir,
                chunk_only=is_chunk_download,
                chunk_duration=30,
            )
        except Exception as e:
            raw_err = str(e)
            clean_err = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw_err)
            clean_err = re.sub(r'^ERROR:\s*', '', clean_err).strip()

            if "drm protected" in clean_err.lower() or "защищён drm" in clean_err.lower():
                user_msg = "Этот трек защищён DRM (SoundCloud Go+) и недоступен для бесплатного воспроизведения."
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            else:
                user_msg = f"Не удалось загрузить трек: {clean_err}"
                status_code = status.HTTP_502_BAD_GATEWAY

            logger.error(f"Failed to download audio from {req.url}: {clean_err}")
            raise HTTPException(
                status_code=status_code,
                detail=user_msg,
            )

        if downloaded.file_size > 50 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Track exceeds 50MB Telegram Bot API limit",
            )

        bot = _get_active_bot()
        target_chat = settings.scanner_buffer_chat_id or user.id

        effective_meta = downloaded.metadata or track_meta

        prefix = "[Preview] " if is_chunk_download else ""
        safe_filename = f"{prefix}{effective_meta.artist} - {effective_meta.title}.mp3".replace("/", "-")
        audio_input = FSInputFile(downloaded.audio_path, filename=safe_filename)
        thumb_input = None
        if downloaded.cover_path and os.path.exists(downloaded.cover_path):
            thumb_input = FSInputFile(downloaded.cover_path)

        try:
            sent_msg = await bot.send_audio(
                chat_id=target_chat,
                audio=audio_input,
                title=f"{effective_meta.title}{' (Preview)' if is_chunk_download else ''}",
                performer=effective_meta.artist,
                duration=effective_meta.duration,
                thumbnail=thumb_input,
            )
        except Exception as e:
            clean_upload_err = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(e))
            logger.error(f"Failed to upload audio to Telegram: {clean_upload_err}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Telegram upload failed: {clean_upload_err}",
            )

        if not sent_msg or not sent_msg.audio:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload audio to Telegram",
            )

        # If upgrading an existing chunk track to full, update chunk track directly in DB first
        if not is_chunk_download and existing_chunk and existing_chunk.is_chunk:
            existing_chunk.file_id = sent_msg.audio.file_id
            existing_chunk.file_unique_id = sent_msg.audio.file_unique_id
            existing_chunk.duration = sent_msg.audio.duration or effective_meta.duration
            existing_chunk.file_size = sent_msg.audio.file_size or downloaded.file_size
            existing_chunk.is_chunk = False
            existing_chunk.file_name = safe_filename
            await db.commit()
            logger.info(f"Directly upgraded chunk track ID {existing_chunk.id} in DB to full track")

        save_res = await track_service.save_track(
            user_id=user.id,
            file_id=sent_msg.audio.file_id,
            file_unique_id=sent_msg.audio.file_unique_id,
            title=effective_meta.title,
            artist=effective_meta.artist,
            duration=sent_msg.audio.duration or effective_meta.duration,
            file_size=sent_msg.audio.file_size or downloaded.file_size,
            mime_type=sent_msg.audio.mime_type or "audio/mpeg",
            file_name=safe_filename,
            forward_source_type=ForwardSourceType.BOT,
            forward_source_name=provider.name,
            library_source=LibrarySource.UPLOADED,
            enrich=not is_chunk_download,
            add_to_library=req.add_to_library and not is_chunk_download,
            is_chunk=is_chunk_download,
            source_url=req.url,
            cover_url=effective_meta.cover_url,
            genre=effective_meta.extra.get("genre"),
            tags=effective_meta.extra.get("tags"),
            album_name=effective_meta.album,
            source_provider=provider.name,
        )

        # Auto-forward to user's Telegram backup channel ONLY if add_to_library is True and not chunk
        if req.add_to_library and not is_chunk_download:
            try:
                from bot.services.channels import get_channel_service
                ch_svc = get_channel_service()
                if ch_svc:
                    await ch_svc.forward_track_to_channel(user.id, save_res.track_id)
            except Exception as e:
                logger.debug(f"Quick-import channel forward failed for new track: {e}")

        track_obj = await db.scalar(
            select(Track)
            .where(Track.id == save_res.track_id)
            .options(
                selectinload(Track.enrichment),
                selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
            )
        )
        lib_entry = await db.scalar(
            select(UserLibrary)
            .where(UserLibrary.user_id == user.id, UserLibrary.track_id == save_res.track_id)
        )

        return QuickImportResponse(
            track=track_to_response(track_obj, lib_entry),
            already_existed=False,
        )


# ============== Connected External Accounts (SoundCloud) ==============

@router.get("/account/soundcloud")
async def get_soundcloud_account(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get connected SoundCloud account for current user."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "soundcloud")
    )
    if not account:
        return {"connected": False}

    return _account_to_response(account)


@router.post("/account/soundcloud/connect", response_model=ExternalAccountResponse)
async def connect_soundcloud_account(
    req: ConnectAccountRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Connect a SoundCloud profile to user account."""
    sc_provider = provider_registry.get_provider("soundcloud")
    if not sc_provider:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SoundCloud provider unavailable",
        )

    await _ensure_user_in_db(user)

    try:
        profile = await sc_provider.resolve_user_profile(req.username_or_url, req.auth_token)
    except Exception as e:
        logger.warning(f"Failed to resolve SoundCloud profile '{req.username_or_url}': {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить профиль SoundCloud: {str(e)}",
        )

    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "soundcloud")
    )

    if not account:
        account = UserExternalAccount(
            user_id=user.id,
            provider="soundcloud",
            external_id=profile.get("external_id"),
            username=profile["username"],
            display_name=profile.get("display_name"),
            profile_url=profile.get("profile_url"),
            avatar_url=profile.get("avatar_url"),
            auth_token=req.auth_token,
            likes_count=profile.get("likes_count", 0),
            tracks_count=profile.get("tracks_count", 0),
            last_synced_at=utcnow(),
        )
        db.add(account)
    else:
        account.external_id = profile.get("external_id")
        account.username = profile["username"]
        account.display_name = profile.get("display_name")
        account.profile_url = profile.get("profile_url")
        account.avatar_url = profile.get("avatar_url")
        account.likes_count = profile.get("likes_count", 0)
        account.tracks_count = profile.get("tracks_count", 0)
        if req.auth_token:
            account.auth_token = req.auth_token
        account.last_synced_at = utcnow()

    await db.commit()
    await db.refresh(account)

    return _account_to_response(account)


@router.delete("/account/soundcloud")
async def disconnect_soundcloud_account(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect SoundCloud account."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "soundcloud")
    )
    if account:
        await db.delete(account)
        await db.commit()
    return {"ok": True, "message": "SoundCloud аккаунт отключен"}


@router.get("/account/soundcloud/likes", response_model=UserLikesResponse)
async def get_soundcloud_likes(
    limit: int = Query(40, ge=1, le=100),
    cursor: Optional[str] = None,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch user's liked tracks from SoundCloud with local library & channel backup status."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "soundcloud")
    )
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SoundCloud аккаунт не подключен. Перейдите в настройки для подключения.",
        )

    sc_provider = provider_registry.get_provider("soundcloud")
    if not sc_provider:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SoundCloud provider unavailable",
        )

    try:
        ident = account.external_id or account.username
        tracks_meta, next_cursor = await sc_provider.fetch_user_likes(
            ident, limit=limit, next_href=cursor, auth_token=account.auth_token
        )
    except Exception as e:
        logger.error(f"Failed to fetch SoundCloud likes for {account.username}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить лайки с SoundCloud: {str(e)}",
        )

    # Preload user's library and channel backup status in batch
    user_lib_q = select(UserLibrary.track_id).where(UserLibrary.user_id == user.id)
    user_lib_track_ids = set((await db.scalars(user_lib_q)).all())

    user_ch_q = (
        select(ChannelMessage.track_id)
        .join(UserChannel, ChannelMessage.channel_id == UserChannel.id)
        .where(UserChannel.user_id == user.id, ChannelMessage.status == ChannelMessageStatus.SENT)
    )
    user_channel_track_ids = set((await db.scalars(user_ch_q)).all())

    existing_tracks = await _find_existing_tracks_batch(tracks_meta, session=db)

    items: List[SoundCloudLikeItem] = []
    for t, existing in zip(tracks_meta, existing_tracks):
        already_in_tg = existing is not None
        in_lib = (existing.id in user_lib_track_ids) if existing else False
        in_chan = (existing.id in user_channel_track_ids) if existing else False
        existing_id = existing.id if existing else None

        items.append(
            SoundCloudLikeItem(
                url=t.url,
                title=t.title,
                artist=t.artist,
                duration=t.duration,
                cover_url=t.cover_url,
                genre=t.extra.get("genre") if t.extra else None,
                tags=t.extra.get("tags") if t.extra else None,
                in_library=in_lib,
                in_channel=in_chan,
                already_in_tg=already_in_tg,
                track_id=existing_id,
                liked_at=t.extra.get("liked_at") if t.extra else None,
            )
        )

    account_resp = _account_to_response(account)

    return UserLikesResponse(
        provider="soundcloud",
        account=account_resp,
        total_likes=account.likes_count,
        items=items,
        next_cursor=next_cursor,
    )


@router.get("/account/soundcloud/tracks", response_model=UserTracksResponse)
async def get_soundcloud_tracks(
    limit: int = Query(40, ge=1, le=100),
    cursor: Optional[str] = None,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch user's uploaded tracks from SoundCloud with local library & channel backup status."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "soundcloud")
    )
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SoundCloud аккаунт не подключен. Перейдите в настройки для подключения.",
        )

    sc_provider = provider_registry.get_provider("soundcloud")
    if not sc_provider:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SoundCloud provider unavailable",
        )

    try:
        ident = account.external_id or account.username
        tracks_meta, next_cursor = await sc_provider.fetch_user_tracks(
            ident, limit=limit, next_href=cursor, auth_token=account.auth_token
        )
    except Exception as e:
        logger.error(f"Failed to fetch SoundCloud user tracks for {account.username}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить треки с SoundCloud: {str(e)}",
        )

    # Preload user's library and channel backup status in batch
    user_lib_q = select(UserLibrary.track_id).where(UserLibrary.user_id == user.id)
    user_lib_track_ids = set((await db.scalars(user_lib_q)).all())

    user_ch_q = (
        select(ChannelMessage.track_id)
        .join(UserChannel, ChannelMessage.channel_id == UserChannel.id)
        .where(UserChannel.user_id == user.id, ChannelMessage.status == ChannelMessageStatus.SENT)
    )
    user_channel_track_ids = set((await db.scalars(user_ch_q)).all())

    existing_tracks = await _find_existing_tracks_batch(tracks_meta, session=db)

    items: List[SoundCloudTrackItem] = []
    for t, existing in zip(tracks_meta, existing_tracks):
        already_in_tg = existing is not None
        in_lib = (existing.id in user_lib_track_ids) if existing else False
        in_chan = (existing.id in user_channel_track_ids) if existing else False
        existing_id = existing.id if existing else None

        items.append(
            SoundCloudTrackItem(
                url=t.url,
                title=t.title,
                artist=t.artist,
                duration=t.duration,
                cover_url=t.cover_url,
                genre=t.extra.get("genre") if t.extra else None,
                tags=t.extra.get("tags") if t.extra else None,
                in_library=in_lib,
                in_channel=in_chan,
                already_in_tg=already_in_tg,
                track_id=existing_id,
                created_at=t.extra.get("created_at") if t.extra else None,
            )
        )

    account_resp = _account_to_response(account)

    return UserTracksResponse(
        provider="soundcloud",
        account=account_resp,
        total_tracks=account.tracks_count or len(items),
        items=items,
        next_cursor=next_cursor,
    )


@router.get("/account/soundcloud/playlists", response_model=UserPlaylistsResponse)
async def get_soundcloud_playlists(
    playlist_type: str = Query("all", pattern="^(all|created|liked)$"),
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = None,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch user's playlists (created & liked) from SoundCloud."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "soundcloud")
    )
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SoundCloud аккаунт не подключен. Перейдите в настройки для подключения.",
        )

    sc_provider = provider_registry.get_provider("soundcloud")
    if not sc_provider:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SoundCloud provider unavailable",
        )

    try:
        ident = account.external_id or account.username
        playlists_raw, next_cursor = await sc_provider.fetch_user_playlists(
            ident, limit=limit, next_href=cursor, auth_token=account.auth_token, playlist_type=playlist_type
        )
    except Exception as e:
        logger.error(f"Failed to fetch SoundCloud playlists for {account.username}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить плейлисты с SoundCloud: {str(e)}",
        )

    items = [SoundCloudPlaylistItem(**p) for p in playlists_raw]

    account_resp = _account_to_response(account)

    return UserPlaylistsResponse(
        provider="soundcloud",
        account=account_resp,
        total_playlists=len(items),
        items=items,
        next_cursor=next_cursor,
    )


@router.get("/account/soundcloud/playlists/{playlist_id}/tracks", response_model=PlaylistTracksResponse)
async def get_soundcloud_playlist_tracks(
    playlist_id: str,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch all tracks for a SoundCloud playlist with local library & channel backup status."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "soundcloud")
    )

    sc_provider = provider_registry.get_provider("soundcloud")
    if not sc_provider:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SoundCloud provider unavailable",
        )

    auth_token = account.auth_token if account else None

    try:
        playlist_info, tracks_meta = await sc_provider.fetch_playlist_tracks(
            playlist_id, auth_token=auth_token
        )
    except Exception as e:
        logger.error(f"Failed to fetch SoundCloud playlist {playlist_id} tracks: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить треки плейлиста: {str(e)}",
        )

    user_lib_q = select(UserLibrary.track_id).where(UserLibrary.user_id == user.id)
    user_lib_track_ids = set((await db.scalars(user_lib_q)).all())

    user_ch_q = (
        select(ChannelMessage.track_id)
        .join(UserChannel, ChannelMessage.channel_id == UserChannel.id)
        .where(UserChannel.user_id == user.id, ChannelMessage.status == ChannelMessageStatus.SENT)
    )
    user_channel_track_ids = set((await db.scalars(user_ch_q)).all())

    existing_tracks = await _find_existing_tracks_batch(tracks_meta, session=db)

    items: List[SoundCloudTrackItem] = []
    for t, existing in zip(tracks_meta, existing_tracks):
        already_in_tg = existing is not None
        in_lib = (existing.id in user_lib_track_ids) if existing else False
        in_chan = (existing.id in user_channel_track_ids) if existing else False
        existing_id = existing.id if existing else None

        items.append(
            SoundCloudTrackItem(
                url=t.url,
                title=t.title,
                artist=t.artist,
                duration=t.duration,
                cover_url=t.cover_url,
                genre=t.extra.get("genre") if t.extra else None,
                tags=t.extra.get("tags") if t.extra else None,
                in_library=in_lib,
                in_channel=in_chan,
                already_in_tg=already_in_tg,
                track_id=existing_id,
                track_number=t.track_number,
            )
        )

    return PlaylistTracksResponse(
        provider="soundcloud",
        playlist=SoundCloudPlaylistItem(**playlist_info),
        total_tracks=len(items),
        tracks=items,
    )


# ============== Connected External Accounts (Spotify) ==============

@router.get("/account/spotify")
async def get_spotify_account(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get connected Spotify account for current user."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "spotify")
    )
    if not account:
        return {"connected": False}

    return _account_to_response(account)


@router.post("/account/spotify/connect", response_model=ExternalAccountResponse)
async def connect_spotify_account(
    req: ConnectAccountRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Connect a Spotify profile username or link."""
    sp_provider = provider_registry.get_provider("spotify")
    if not sp_provider:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Spotify provider unavailable",
        )

    await _ensure_user_in_db(user)

    try:
        profile = await sp_provider.resolve_user_profile(req.username_or_url, req.auth_token)
    except Exception as e:
        logger.warning(f"Failed to resolve Spotify profile '{req.username_or_url}': {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить профиль Spotify: {str(e)}",
        )

    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "spotify")
    )

    if not account:
        account = UserExternalAccount(
            user_id=user.id,
            provider="spotify",
            external_id=profile.get("external_id"),
            username=profile["username"],
            display_name=profile.get("display_name"),
            profile_url=profile.get("profile_url"),
            avatar_url=profile.get("avatar_url"),
            auth_token=req.auth_token,
            likes_count=profile.get("likes_count", 0),
            tracks_count=profile.get("tracks_count", 0),
            last_synced_at=utcnow(),
        )
        db.add(account)
    else:
        account.external_id = profile.get("external_id")
        account.username = profile["username"]
        account.display_name = profile.get("display_name")
        account.profile_url = profile.get("profile_url")
        account.avatar_url = profile.get("avatar_url")
        account.likes_count = profile.get("likes_count", 0)
        account.tracks_count = profile.get("tracks_count", 0)
        account.auth_token = req.auth_token
        account.last_synced_at = utcnow()

    await db.commit()
    await db.refresh(account)

    return _account_to_response(account)


@router.patch("/account/{provider}/privacy", response_model=ExternalAccountResponse)
async def update_account_privacy(
    provider: str,
    req: ExternalAccountPrivacyUpdate,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update privacy settings for connected external account (SoundCloud / Spotify)."""
    if provider not in ("soundcloud", "spotify"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported provider")

    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == provider)
    )
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Аккаунт не подключен")

    if req.show_on_profile is not None:
        account.show_on_profile = req.show_on_profile
    if req.show_playlists is not None:
        account.show_playlists = req.show_playlists
    if req.show_tracks is not None:
        account.show_tracks = req.show_tracks

    await db.commit()
    await db.refresh(account)
    return _account_to_response(account)


@router.delete("/account/spotify")
async def disconnect_spotify_account(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Spotify account."""
    account = await db.scalar(
        select(UserExternalAccount)
        .where(UserExternalAccount.user_id == user.id, UserExternalAccount.provider == "spotify")
    )
    if account:
        await db.delete(account)
        await db.commit()
    return {"ok": True, "message": "Spotify аккаунт отключен"}


# ============== Exportify Spotify CSV Import & Deduplication ==============

async def _parse_exportify_csv_content(
    raw_content: str,
    filename: str,
    user_id: int,
    db: AsyncSession,
) -> ExportifyPreviewResponse:
    """Internal helper to parse Exportify CSV text and match tracks against DB/Telegram library."""
    if not raw_content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV файл пуст.",
        )

    reader = csv.DictReader(io.StringIO(raw_content))
    if not reader.fieldnames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось прочитать заголовки CSV файла.",
        )

    field_map = {f.strip().lower(): f for f in reader.fieldnames if f}

    def get_val(row, *candidates):
        for c in candidates:
            k = field_map.get(c.lower())
            if k and row.get(k):
                return row[k].strip()
        return None

    # Preload user's library and channel backup status in batch
    user_lib_q = select(UserLibrary.track_id).where(UserLibrary.user_id == user_id)
    user_lib_track_ids = set((await db.scalars(user_lib_q)).all())

    user_ch_q = (
        select(ChannelMessage.track_id)
        .join(UserChannel, ChannelMessage.channel_id == UserChannel.id)
        .where(UserChannel.user_id == user_id, ChannelMessage.status == ChannelMessageStatus.SENT)
    )
    user_channel_track_ids = set((await db.scalars(user_ch_q)).all())

    items: List[ExportifyTrackItem] = []
    in_lib_cnt = 0
    in_chan_cnt = 0
    in_tg_cnt = 0

    for idx, row in enumerate(reader, start=1):
        title = get_val(row, "Track Name", "Name", "Title")
        artist = get_val(row, "Artist Name(s)", "Artist Name", "Artist", "Artists")
        if not title or not artist:
            continue

        album = get_val(row, "Album Name", "Album")
        cover_url = get_val(row, "Album Image URL", "Cover URL", "Image URL")
        track_uri = get_val(row, "Track URI", "URI", "Spotify URI")
        isrc = get_val(row, "ISRC")
        liked_at = get_val(row, "Added At", "Liked At")

        # Duration (ms -> s)
        dur_str = get_val(row, "Track Duration (ms)", "Duration (ms)", "Duration_ms", "Duration")
        duration = None
        if dur_str:
            try:
                dur_num = float(dur_str.replace(",", "."))
                if dur_num > 1000:
                    duration = int(dur_num / 1000)
                else:
                    duration = int(dur_num)
            except ValueError:
                pass

        if track_uri and "spotify:track:" in track_uri:
            track_id_str = track_uri.split(":")[-1]
            url = f"https://open.spotify.com/track/{track_id_str}"
        else:
            url = f"spotify:exportify:{idx}"

        # Accurate recognition against database
        existing = await _find_existing_track(title, artist, duration, session=db)
        already_in_tg = existing is not None
        in_lib = (existing.id in user_lib_track_ids) if existing else False
        in_chan = (existing.id in user_channel_track_ids) if existing else False
        existing_id = existing.id if existing else None

        if already_in_tg:
            in_tg_cnt += 1
        if in_lib:
            in_lib_cnt += 1
        if in_chan:
            in_chan_cnt += 1

        items.append(
            ExportifyTrackItem(
                url=url,
                title=title,
                artist=artist,
                album=album,
                duration=duration,
                cover_url=cover_url,
                track_uri=track_uri,
                isrc=isrc,
                in_library=in_lib,
                in_channel=in_chan,
                already_in_tg=already_in_tg,
                track_id=existing_id,
                liked_at=liked_at,
            )
        )

    new_cnt = sum(1 for it in items if not it.in_library)

    return ExportifyPreviewResponse(
        filename=filename,
        total_tracks=len(items),
        new_tracks_count=new_cnt,
        in_library_count=in_lib_cnt,
        in_channel_count=in_chan_cnt,
        already_in_tg_count=in_tg_cnt,
        tracks=items,
    )


@router.post("/spotify/exportify/preview", response_model=ExportifyPreviewResponse)
async def preview_exportify_csv(
    file: Optional[UploadFile] = File(None),
    csv_text: Optional[str] = Form(None),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Parse an Exportify CSV file (Liked Songs or any playlist), extract track metadata,
    perform robust recognition against local library, and backup the CSV to user's Telegram channel.
    """
    raw_content = ""
    filename = "exported_tracks.csv"
    content_bytes = b""

    if file:
        filename = file.filename or "exportify.csv"
        content_bytes = await file.read()
        if len(content_bytes) > 5 * 1024 * 1024:  # 5MB limit for CSV
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV файл слишком большой (макс. 5 МБ).",
            )
        try:
            raw_content = content_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw_content = content_bytes.decode("latin-1", errors="replace")
    elif csv_text:
        if len(csv_text) > 5 * 1024 * 1024:  # 5MB limit
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV текст слишком большой (макс. 5 МБ).",
            )
        raw_content = csv_text
        content_bytes = csv_text.encode("utf-8")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо прикрепить файл CSV или передать текст CSV.",
        )

    preview_res = await _parse_exportify_csv_content(raw_content, filename, user.id, db)

    # Backup the CSV document to user's Telegram channel / PM (Stateless Telegram Storage architecture)
    if content_bytes:
        try:
            user_channel = await db.scalar(
                select(UserChannel).where(UserChannel.user_id == user.id, UserChannel.is_active == True)
            )
            target_chat_id = user_channel.channel_id if user_channel else user.id
            bot = _get_active_bot()
            caption = (
                f"📁 <b>Импорт Spotify (Exportify)</b>: <code>{filename}</code>\n"
                f"🎵 Треков в файле: {preview_res.total_tracks}\n"
                f"✨ Новых для медиатеки: {preview_res.new_tracks_count}\n\n"
                f"#spotify #import #exportify"
            )
            sent_msg = await bot.send_document(
                chat_id=target_chat_id,
                document=BufferedInputFile(file=content_bytes, filename=filename),
                caption=caption,
            )
            if sent_msg and sent_msg.document:
                import_file = UserImportFile(
                    user_id=user.id,
                    provider="spotify",
                    filename=filename,
                    file_id=sent_msg.document.file_id,
                    file_size=sent_msg.document.file_size or len(content_bytes),
                    total_tracks=preview_res.total_tracks,
                    channel_id=user_channel.channel_id if user_channel else None,
                    message_id=sent_msg.message_id,
                    summary_json=json.dumps({
                        "total_tracks": preview_res.total_tracks,
                        "new_tracks_count": preview_res.new_tracks_count,
                        "in_library_count": preview_res.in_library_count,
                        "in_channel_count": preview_res.in_channel_count,
                        "already_in_tg_count": preview_res.already_in_tg_count,
                    }),
                )
                db.add(import_file)
                await db.commit()
                await db.refresh(import_file)
                preview_res.file_id = import_file.file_id
                preview_res.import_file_id = import_file.id
                logger.info(f"Saved Exportify CSV '{filename}' to Telegram channel/user {target_chat_id} (file_id: {sent_msg.document.file_id})")
        except Exception as e:
            logger.warning(f"Could not backup Exportify CSV to Telegram: {e}")

    return preview_res


@router.get("/spotify/last-import")
async def get_last_spotify_import(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get metadata for the last saved Spotify import file(s) in Telegram."""
    recent_q = (
        select(UserImportFile)
        .where(UserImportFile.user_id == user.id, UserImportFile.provider == "spotify")
        .order_by(UserImportFile.id.desc())
        .limit(20)
    )
    all_recent = (await db.scalars(recent_q)).all()
    if not all_recent:
        return {"found": False, "recent_files": []}

    import_file = all_recent[0]

    summary = {}
    if import_file.summary_json:
        try:
            summary = json.loads(import_file.summary_json)
        except Exception:
            pass

    def _parse_summary(item):
        if not item.summary_json:
            return {}
        try:
            return json.loads(item.summary_json)
        except Exception:
            return {}

    return {
        "found": True,
        "id": import_file.id,
        "provider": import_file.provider,
        "filename": import_file.filename,
        "file_id": import_file.file_id,
        "file_size": import_file.file_size,
        "total_tracks": import_file.total_tracks,
        "channel_id": import_file.channel_id,
        "message_id": import_file.message_id,
        "created_at": import_file.created_at.isoformat() if import_file.created_at else None,
        "summary": summary,
        "recent_files": [
            {
                "id": f.id,
                "filename": f.filename,
                "file_id": f.file_id,
                "file_size": f.file_size,
                "total_tracks": f.total_tracks,
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "summary": _parse_summary(f),
            }
            for f in all_recent
        ],
    }


@router.get("/spotify/last-import/preview", response_model=ExportifyPreviewResponse)
async def preview_last_spotify_import(
    file_id: Optional[str] = Query(None),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download saved Spotify CSV from Telegram and return fresh track preview."""
    q = (
        select(UserImportFile)
        .where(UserImportFile.user_id == user.id, UserImportFile.provider == "spotify")
    )
    if file_id:
        q = q.where(UserImportFile.file_id == file_id)
    q = q.order_by(UserImportFile.id.desc()).limit(1)

    import_file = await db.scalar(q)
    if not import_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сохранённый файл импорта не найден.")

    bot = _get_active_bot()
    try:
        file_info = await bot.get_file(import_file.file_id)
        file_path = file_info.file_path
        base_url = settings.telegram_api_url.rstrip("/")
        download_url = f"{base_url}/file/bot{settings.bot_token}/{file_path}"
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(download_url) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail="Не удалось скачать файл из Telegram.")
                content_bytes = await resp.read()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch import file from Telegram: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Ошибка загрузки из Telegram: {str(e)}")

    try:
        raw_content = content_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw_content = content_bytes.decode("latin-1", errors="replace")

    preview_res = await _parse_exportify_csv_content(raw_content, import_file.filename, user.id, db)
    preview_res.file_id = import_file.file_id
    preview_res.import_file_id = import_file.id
    return preview_res


@router.delete("/spotify/import-file/{file_id_or_id}")
async def delete_spotify_import_file(
    file_id_or_id: str,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved Spotify import file record."""
    q = select(UserImportFile).where(
        UserImportFile.user_id == user.id,
        UserImportFile.provider == "spotify"
    )
    if file_id_or_id.isdigit():
        q = q.where(
            (UserImportFile.id == int(file_id_or_id)) | (UserImportFile.file_id == file_id_or_id)
        )
    else:
        q = q.where(UserImportFile.file_id == file_id_or_id)

    import_file = await db.scalar(q)
    if not import_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл импорта не найден.")

    # Try deleting message from telegram channel/chat if bot has permissions
    if import_file.channel_id and import_file.message_id:
        try:
            bot = _get_active_bot()
            await bot.delete_message(chat_id=import_file.channel_id, message_id=import_file.message_id)
        except Exception as e:
            logger.warning(f"Could not delete message {import_file.message_id} from channel {import_file.channel_id}: {e}")

    await db.delete(import_file)
    await db.commit()
    return {"ok": True, "message": "Файл импорта успешно удалён."}


@router.post("/spotify/exportify/start", response_model=JobResponse)
async def start_exportify_import(
    req: ExportifyStartRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start background ingestion job for selected Exportify tracks.
    Tracks already stored on Telegram servers are instantly linked without re-downloading.
    Tracks already in the user's channel are not re-sent.
    """
    if not req.tracks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Список треков для импорта пуст.",
        )

    await _ensure_user_in_db(user)

    playlist_id = None
    target_title = req.title or "Spotify Import"

    cover = req.cover_url or (req.tracks[0].cover_url if req.tracks and req.tracks[0].cover_url else None)
    if req.target_playlist_id:
        target_pl = await db.scalar(
            select(Playlist).where(Playlist.id == req.target_playlist_id, Playlist.owner_id == user.id)
        )
        if not target_pl:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Выбранный плейлист не найден.")
        playlist_id = target_pl.id
        target_title = target_pl.name
    elif req.create_playlist:
        p_name = req.playlist_name or req.title or "Spotify Playlist"
        new_pl = Playlist(
            owner_id=user.id,
            name=p_name,
            description="Imported from Spotify via Exportify",
            custom_cover_url=cover,
            is_public=False,
        )
        db.add(new_pl)
        await db.commit()
        await db.refresh(new_pl)
        playlist_id = new_pl.id
        target_title = new_pl.name

    custom_tracks_data = [t.model_dump() for t in req.tracks]

    job = await job_manager.create_job(
        user_id=user.id,
        url="https://exportify.app",
        provider_name="spotify",
        entity_type=EntityType.PLAYLIST.value if playlist_id else EntityType.TRACKS.value,
        title=target_title,
        total_tracks=len(req.tracks),
        cover_url=cover,
        custom_tracks=custom_tracks_data,
    )
    if playlist_id:
        job.playlist_id = playlist_id

    bot = _get_active_bot()
    pipeline = IngestionPipeline(bot)

    # Spawn background task
    task = asyncio.create_task(pipeline.execute_job(job))
    job_manager.register_task(job.id, task)

    return JobResponse(**job.to_dict())


@router.get("/account/spotify/likes")
async def get_spotify_likes_info():
    """Inform user to use the rock-solid Exportify CSV import."""
    return {
        "ok": True,
        "message": "Используйте импорт CSV через Exportify (вкладка Spotify ➔ Импорт CSV или Настройки)",
    }

