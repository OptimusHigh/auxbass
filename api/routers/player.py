"""
TG Player API - Player Router
Handles audio streaming with secure proxy (token never exposed to browser)
"""
from typing import Optional
import logging
import re
import time
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Header, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import aiohttp
import asyncio

from shared.config import get_settings, get_proxy_url
from shared.database import get_db
from shared.models import Track, UserLibrary, ChannelMessage, ChannelMessageStatus, UserChannel, utcnow
from shared.matching import normalize_title, normalize_artist

from .auth import get_current_user
from api.utils.responses import is_streamable, HD_MIME_TYPES
from api.utils.bot_helpers import get_http_session, close_http_session, get_session_stats
from api.schemas.player import StreamUrlResponse, DownloadPlaylistRequest
from api.schemas.common import TelegramUser


logger = logging.getLogger("uvicorn.error")

router = APIRouter()
settings = get_settings()

# get_http_session and close_http_session are now imported from api.utils.bot_helpers
# (see imports above). Kept as module-level names for backwards compat.



# Cache for Telegram file paths (not full URLs!)
_file_path_cache: dict[str, tuple[str, float]] = {}
FILE_PATH_CACHE_TTL = 3000  # 50 minutes

# Secure token cache: maps temporary token -> (track_id, user_id, file_path, expires)
# file_path is cached to avoid second Telegram API call when streaming
_stream_tokens: dict[str, tuple[int, int, str, float]] = {}
STREAM_TOKEN_TTL = 3600  # 1 hour for token validity (was 5 min - too short for long tracks/pauses)


def generate_stream_token(track_id: int, user_id: int, file_path: str) -> str:
    """Generate a secure temporary token for streaming with cached file_path"""
    token = secrets.token_urlsafe(32)
    expires = time.time() + STREAM_TOKEN_TTL
    _stream_tokens[token] = (track_id, user_id, file_path, expires)
    
    # Cleanup old tokens periodically (limit cleanup to avoid O(n) on every call)
    now = time.time()
    if len(_stream_tokens) > 500:
        expired = [k for k, v in _stream_tokens.items() if v[3] < now]
        for k in expired:
            del _stream_tokens[k]
    
    return token


def validate_stream_token(token: str) -> Optional[tuple[int, int, str]]:
    """Validate token and return (track_id, user_id, file_path) or None"""
    if token not in _stream_tokens:
        return None
    
    track_id, user_id, file_path, expires = _stream_tokens[token]
    if time.time() > expires:
        del _stream_tokens[token]
        return None
    
    return (track_id, user_id, file_path)


async def find_streamable_alternative(track: Track, db: AsyncSession) -> Optional[Track]:
    """
    Find a streamable (MP3/AAC/OGG/M4A) alternative for an HD or large track.
    Matches by normalized title and artist, with additional validation:
    - Duration difference <= 15 seconds (if durations present)
    - File size <= 20 MB (streamable via standard Telegram Bot API)
    - Deezer track ID match (if available)
    """
    if not track.title:
        return None
    
    # Normalize for matching
    norm_title = normalize_title(track.title).lower().strip()
    norm_artist = normalize_artist(track.artist or "").lower().strip() if track.artist else None
    
    # Get HD track enrichment data for Deezer ID matching
    hd_deezer_id = None
    if track.enrichment:
        hd_deezer_id = track.enrichment.deezer_track_id
    
    # Find non-HD tracks with file size <= 20MB
    hd_mime_list = list(HD_MIME_TYPES)
    max_size_bytes = 20 * 1024 * 1024
    
    query = (
        select(Track)
        .where(Track.id != track.id)
        .where(Track.is_unavailable == False)
        .where(
            or_(
                Track.mime_type.is_(None),
                Track.mime_type.notin_(hd_mime_list)
            )
        )
        .where(
            or_(
                Track.file_size.is_(None),
                Track.file_size <= max_size_bytes
            )
        )
        .options(selectinload(Track.enrichment))
    )
    
    result = await db.execute(query)
    candidates = result.scalars().all()
    
    # Find best match with scoring
    best_match = None
    best_score = 0
    
    for candidate in candidates:
        if not candidate.title:
            continue
        
        cand_norm_title = normalize_title(candidate.title).lower().strip()
        cand_norm_artist = normalize_artist(candidate.artist or "").lower().strip() if candidate.artist else None
        
        # Check Deezer ID match (definitive match)
        has_deezer_match = False
        if hd_deezer_id and candidate.enrichment and candidate.enrichment.deezer_track_id == hd_deezer_id:
            has_deezer_match = True
        
        # Match title (must be exact or very close high-ratio substring >= 80%)
        title_score = 0
        if norm_title == cand_norm_title:
            title_score = 50
        elif len(norm_title) >= 5 and len(cand_norm_title) >= 5:
            min_len = min(len(norm_title), len(cand_norm_title))
            max_len = max(len(norm_title), len(cand_norm_title))
            if (cand_norm_title in norm_title or norm_title in cand_norm_title) and (min_len / max_len >= 0.75):
                title_score = 30
        
        if title_score == 0 and not has_deezer_match:
            continue
        
        score = title_score
        
        # Match artist
        if norm_artist and cand_norm_artist:
            if norm_artist == cand_norm_artist:
                score += 40
            elif norm_artist in cand_norm_artist or cand_norm_artist in norm_artist:
                score += 20
            elif not has_deezer_match:
                # Different artist and no Deezer match -> skip false positive
                continue
        elif (not norm_artist and not cand_norm_artist) or has_deezer_match:
            score += 15
        else:
            # One has artist, other doesn't -> only allow if title is exact match >= 6 chars
            if len(norm_title) >= 6 and norm_title == cand_norm_title:
                score += 10
            elif not has_deezer_match:
                continue
        
        if has_deezer_match:
            score += 100
        
        # Check duration similarity
        if track.duration and candidate.duration:
            duration_diff = abs(track.duration - candidate.duration)
            if duration_diff <= 3:
                score += 30
            elif duration_diff <= 8:
                score += 15
            elif not has_deezer_match:
                # Duration too different -> not the same audio (e.g. extended mix vs radio edit)
                continue
        elif track.duration and track.duration > 600 and (not candidate.duration or candidate.duration < 300):
            # Long track (>10min) should not match short unknown candidate
            continue
        
        # File size preference
        if candidate.file_size and candidate.file_size <= max_size_bytes:
            score += 10
        
        # Update best match
        if score > best_score:
            best_score = score
            best_match = candidate
    
    # Require minimum score to avoid false matches (requires title + artist match or Deezer match)
    if best_match and best_score >= 70:
        return best_match
    
    return None


async def find_hd_alternative(track: Track, db: AsyncSession) -> Optional[Track]:
    """
    Find an HD (FLAC/WAV) alternative for an MP3 track.
    Useful to show user that HD version is available.
    Matches by normalized title and artist, with additional validation:
    - Duration difference < 5 seconds
    - File size larger than original (HD should be larger than MP3)
    - Deezer track ID match (if available)
    """
    if not track.title:
        return None
    
    # Normalize for matching
    norm_title = normalize_title(track.title).lower()
    norm_artist = normalize_artist(track.artist or "").lower() if track.artist else None
    
    # Get MP3 track enrichment data for Deezer ID matching
    mp3_deezer_id = None
    if track.enrichment:
        mp3_deezer_id = track.enrichment.deezer_track_id
    
    # Find HD tracks with similar title/artist
    hd_mime_list = list(HD_MIME_TYPES)
    query = (
        select(Track)
        .where(Track.id != track.id)
        .where(Track.is_unavailable == False)
        .where(Track.mime_type.in_(hd_mime_list))
        .options(selectinload(Track.enrichment))  # Load enrichment for deezer_id check
    )
    
    result = await db.execute(query)
    candidates = result.scalars().all()
    
    # Find best match with scoring
    best_match = None
    best_score = 0
    
    for candidate in candidates:
        if not candidate.title:
            continue
        
        cand_norm_title = normalize_title(candidate.title).lower()
        cand_norm_artist = normalize_artist(candidate.artist or "").lower() if candidate.artist else None
        
        # Match title (must be similar)
        if norm_title != cand_norm_title:
            continue
        
        # Match artist if both have artist
        if norm_artist and cand_norm_artist:
            if norm_artist != cand_norm_artist:
                continue
        
        # Calculate match score
        score = 0
        
        # Check Deezer ID match (highest priority)
        if mp3_deezer_id and candidate.enrichment:
            if candidate.enrichment.deezer_track_id == mp3_deezer_id:
                score += 100  # Perfect match via Deezer
        
        # Check duration similarity (±5 seconds)
        if track.duration and candidate.duration:
            duration_diff = abs(track.duration - candidate.duration)
            if duration_diff <= 5:
                score += 50  # Good duration match
            elif duration_diff <= 10:
                score += 20  # Acceptable duration match
            else:
                continue  # Duration too different, skip
        
        # Check file size (HD should be larger than MP3)
        if track.file_size and candidate.file_size:
            if candidate.file_size > track.file_size:
                # HD is larger than MP3 - good sign
                size_ratio = candidate.file_size / track.file_size
                if 2 <= size_ratio <= 10:  # Typical HD/MP3 ratio
                    score += 30
                elif size_ratio > 1.5:  # At least somewhat larger
                    score += 10
        
        # Update best match
        if score > best_score:
            best_score = score
            best_match = candidate
    
    # Require minimum score to avoid false matches
    if best_match and best_score >= 20:  # At least title+artist match + some validation
        return best_match
    
    return None


async def get_telegram_file_path(file_id: str) -> Optional[str]:
    """
    Get file path from Telegram (not full URL).
    Uses global session pool for better performance.
    """
    # Fast exit for empty or lazy placeholders (resolved on-demand from channel)
    if not file_id or file_id.startswith("lazy:") or file_id.startswith("pending:"):
        return None

    # Check cache
    if file_id in _file_path_cache:
        file_path, expires = _file_path_cache[file_id]
        if time.time() < expires:
            logger.debug(f"File path cache hit for {file_id[:20]}...")
            return file_path
    
    # Call Telegram API using pooled session
    # Use configurable API URL (supports local Telegram Bot API server for >20MB files)
    base_url = settings.telegram_api_url.rstrip('/')
    api_url = f"{base_url}/bot{settings.bot_token}/getFile"
    
    session = await get_http_session()
    try:
        async with session.get(api_url, params={"file_id": file_id}) as resp:
            if resp.status != 200:
                logger.debug(f"Telegram getFile returned status={resp.status} for file_id={file_id[:20]}... (will refresh from channel)")
                return None
            
            data = await resp.json()
            
            if not data.get("ok"):
                error_desc = data.get("description", "Unknown error")
                logger.debug(f"Telegram getFile error: {error_desc}, file_id={file_id[:20]}...")
                return None
            
            file_path = data.get("result", {}).get("file_path")
            if not file_path:
                # If Telegram returned ok=True but no file_path, the file may have just been uploaded
                # and is still propagating to Telegram CDN. Try a quick retry if size <= 20MB.
                file_size = data.get("result", {}).get("file_size", 0)
                if file_size <= 20 * 1024 * 1024:
                    await asyncio.sleep(0.8)
                    try:
                        async with session.get(api_url, params={"file_id": file_id}) as retry_resp:
                            if retry_resp.status == 200:
                                retry_data = await retry_resp.json()
                                file_path = retry_data.get("result", {}).get("file_path")
                                if file_path:
                                    logger.info(f"Got file path on retry for {file_id[:20]}...: {file_path}")
                    except Exception as retry_err:
                        logger.debug(f"Retry getFile failed: {retry_err}")

            if not file_path:
                logger.error(f"No file_path in response for file_id={file_id[:20]}...")
                return None
            
            # Cache the file path
            _file_path_cache[file_id] = (file_path, time.time() + FILE_PATH_CACHE_TTL)
            
            # Periodic cleanup of file path cache
            if len(_file_path_cache) > 5000:
                now = time.time()
                expired = [k for k, (_, exp) in _file_path_cache.items() if exp < now]
                for k in expired:
                    del _file_path_cache[k]
            
            logger.info(f"Got file path: {file_path}")
            
            return file_path
    except aiohttp.ClientError as e:
        logger.error(f"HTTP error getting file path: {e}")
        return None


async def refresh_file_id_from_channel(
    track_id: int, db: AsyncSession
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Try to get fresh file_id from user's channel message.
    
    Returns:
        (new_file_id, error_code, channel_title)
        error_code can be:
          - None: success or no channel message
          - "channel_access_denied": bot has no access to channel (kicked, demoted, inactive)
          - "forward_failed": other telegram error
    """
    # Find channel message for this track
    result = await db.execute(
        select(ChannelMessage, UserChannel)
        .join(UserChannel, ChannelMessage.channel_id == UserChannel.id)
        .where(ChannelMessage.track_id == track_id)
        .where(ChannelMessage.status == ChannelMessageStatus.SENT)
        .limit(1)
    )
    row = result.first()
    
    if not row:
        logger.debug(f"[Refresh FileID] No channel message found for track {track_id}")
        return None, None, None
    
    channel_msg, user_channel = row
    
    if not user_channel.is_active:
        logger.info(f"[Refresh FileID] Channel {user_channel.channel_id} is inactive for track {track_id}")
        return None, "channel_access_denied", user_channel.channel_title
    
    base_url = settings.telegram_api_url.rstrip('/')
    session = await get_http_session()
    
    # Try forwardMessage which directly returns the full message with audio
    forward_url = f"{base_url}/bot{settings.bot_token}/forwardMessage"
    delete_url = f"{base_url}/bot{settings.bot_token}/deleteMessage"
    target_chat = settings.scanner_buffer_chat_id or user_channel.channel_id
    
    try:
        async with session.post(forward_url, json={
            "chat_id": target_chat,
            "from_chat_id": user_channel.channel_id, 
            "message_id": channel_msg.message_id,
            "disable_notification": True,
        }) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = {}
            
            desc = str(data.get("description", "")).lower()
            
            # Check if bot was kicked, demoted, or channel not found
            if resp.status in (401, 403) or (
                resp.status == 400 and (
                    "chat not found" in desc or 
                    "rights" in desc or 
                    "not a member" in desc or 
                    "kicked" in desc or
                    "bot was blocked" in desc or
                    "administrator" in desc
                )
            ):
                logger.warning(
                    f"[Refresh FileID] Bot lacks channel access for user {user_channel.user_id}: "
                    f"status={resp.status}, desc={desc}"
                )
                user_channel.is_active = False
                user_channel.updated_at = utcnow()
                await db.commit()
                return None, "channel_access_denied", user_channel.channel_title
            
            if resp.status != 200 or not data.get("ok"):
                logger.warning(f"[Refresh FileID] forwardMessage failed: status={resp.status}, desc={desc}")
                return None, "forward_failed", user_channel.channel_title
            
            result_msg = data.get("result", {})
            audio = result_msg.get("audio") or result_msg.get("document")
            new_message_id = result_msg.get("message_id")
            
            # Clean up the forwarded message
            if new_message_id:
                try:
                    async with session.post(delete_url, json={
                        "chat_id": target_chat,
                        "message_id": new_message_id,
                    }) as _:
                        pass
                except Exception:
                    pass
            
            if not audio or not audio.get("file_id"):
                logger.warning(f"[Refresh FileID] No audio/document in forwarded message")
                return None, "no_audio", user_channel.channel_title
            
            new_file_id = audio["file_id"]

            # Update track record in DB with new file_id and backfilled metadata
            track = await db.get(Track, track_id)
            if track:
                track.file_id = new_file_id
                track.is_unavailable = False
                if audio.get("file_size") and not track.file_size:
                    track.file_size = audio.get("file_size")
                if audio.get("duration") and not track.duration:
                    track.duration = audio.get("duration")
                if audio.get("mime_type") and not track.mime_type:
                    track.mime_type = audio.get("mime_type")
                await db.commit()

            logger.info(f"[Refresh FileID] Got fresh file_id for track {track_id}")
            return new_file_id, None, user_channel.channel_title
            
    except aiohttp.ClientError as e:
        logger.error(f"[Refresh FileID] HTTP error in forwardMessage: {e}")
        return None, "network_error", user_channel.channel_title


@router.get("/stream/{track_id}", response_model=StreamUrlResponse)
async def get_stream_url(
    track_id: int,
    request: Request,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get secure proxy URL for streaming a track.
    Returns a temporary token-based URL that proxies through our server.
    Bot token is NEVER exposed to the client.
    
    For HD tracks (FLAC, WAV), automatically finds MP3 alternative if available.
    Returns info about HD version so user knows high quality is available.
    
    Users can stream:
    - Any public track (from global library)
    - Their own private tracks
    """
    # Get track (any track, we'll check permissions) - load enrichment for better matching
    original_track = await db.scalar(
        select(Track)
        .where(Track.id == track_id)
        .options(selectinload(Track.enrichment))
    )
    
    if not original_track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Check access: public tracks are accessible to everyone, private only to uploader
    if not original_track.is_public and original_track.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="This track is private")
    
    # HD track handling: find MP3 alternative
    # Check both mime_type AND file size (large files are often HD even without proper mime_type)
    track = original_track
    hd_track_info = None
    
    file_size_mb = (original_track.file_size or 0) / (1024 * 1024)
    is_track_hd = not is_streamable(original_track.mime_type)
    is_track_too_large = file_size_mb > 20
    
    if is_track_hd or is_track_too_large:
        # This is an HD track or too large - try to find MP3 alternative
        reason = f"HD format ({original_track.mime_type})" if is_track_hd else f"too large ({file_size_mb:.1f} MB)"
        logger.info(f"[Stream Request] Track {track_id} is {reason}, searching for MP3 alternative...")
        
        mp3_alt = await find_streamable_alternative(original_track, db)
        
        if mp3_alt:
            # Found MP3 alternative - use it for streaming, save HD info
            mp3_size_mb = (mp3_alt.file_size or 0) / (1024 * 1024)
            logger.info(
                f"[Stream Request] ✅ Auto-substitution: HD track {track_id} ({file_size_mb:.1f}MB, {original_track.mime_type}) "
                f"-> MP3 track {mp3_alt.id} ({mp3_size_mb:.1f}MB)"
            )
            track = mp3_alt
            hd_track_info = {
                "id": original_track.id,
                "title": original_track.title,
            }
        else:
            # No MP3 alternative - can't stream
            logger.warning(f"[Stream Request] ❌ Track {track_id} ({reason}) has no MP3 alternative")
            if is_track_hd:
                raise HTTPException(
                    status_code=503,
                    detail=f"Трек в формате высокого качества ({original_track.mime_type or 'HD'}, {file_size_mb:.1f} MB) недоступен для стриминга. MP3 версия не найдена. Используйте кнопку 'Скачать' в боте для загрузки HD версии."
                )
            else:
                raise HTTPException(
                    status_code=503,
                    detail=f"Файл слишком большой ({file_size_mb:.1f} MB). MP3 версия не найдена. Используйте кнопку 'Скачать' в боте."
                )
    
    # Verify file is accessible (pre-cache the path)
    file_path = await get_telegram_file_path(track.file_id)
    
    if not file_path:
        # Check if file is too large (>20MB limit for standard Bot API)
        file_size_mb = (track.file_size or 0) / (1024 * 1024)
        if file_size_mb > 20:
            logger.warning(f"[Stream Request] Track {track.id} too large: {file_size_mb:.1f} MB")
            raise HTTPException(
                status_code=503,
                detail=f"Файл слишком большой ({file_size_mb:.1f} MB). Telegram Bot API поддерживает скачивание только файлов до 20 MB. Используйте кнопку 'Скачать' в боте."
            )
        
        # Try to refresh file_id from channel message
        logger.info(f"[Stream Request] Track {track_id} file_id stale, attempting refresh from channel...")
        new_file_id, err_code, ch_title = await refresh_file_id_from_channel(track_id, db)
        
        if new_file_id:
            # Update track with new file_id
            track.file_id = new_file_id
            track.is_unavailable = False  # Clear unavailable flag
            await db.commit()
            logger.info(f"[Stream Request] Track {track_id} file_id refreshed successfully!")
            
            # Try again with new file_id
            file_path = await get_telegram_file_path(new_file_id)
        
        if not file_path:
            # If current track file_id is dead, let's see if another copy exists in DB
            alt_track = await find_streamable_alternative(original_track, db)
            if alt_track and alt_track.id != track.id:
                alt_path = await get_telegram_file_path(alt_track.file_id)
                if alt_path:
                    logger.info(
                        f"[Stream Request] 🔄 Auto-substitution for stale track {track_id} -> track {alt_track.id}"
                    )
                    track = alt_track
                    file_path = alt_path

        if not file_path:
            # If bot lacks access to the channel:
            # ONLY complain about missing channel if this is the CURRENT USER's channel/track
            if err_code == "channel_access_denied":
                if original_track.uploader_id == user.id:
                    bot_user = settings.bot_username or "TG Player"
                    logger.warning(
                        f"[Stream Request] Track {track_id} cannot be refreshed: "
                        f"bot lacks access to owner's channel '{ch_title}'"
                    )
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "message": f"Бот не имеет доступа к вашему каналу «{ch_title or 'Резервный'}». Добавьте @{bot_user} администратором в канал, чтобы слушать треки.",
                            "code": "channel_bot_missing",
                            "channel_title": ch_title or "Резервный канал",
                            "bot_username": settings.bot_username or "",
                        }
                    )
                else:
                    logger.warning(
                        f"[Stream Request] Track {track_id} channel '{ch_title}' is inaccessible (uploaded by other user {original_track.uploader_id})"
                    )

            # Still no luck - mark track as unavailable
            if not track.is_unavailable:
                track.is_unavailable = True
                await db.commit()
                logger.warning(f"[Stream Request] Track {track_id} marked as unavailable (Telegram file_id invalid)")
            else:
                logger.warning(f"[Stream Request] Track {track_id} file unavailable from Telegram (already marked)")
            
            detail_msg = "Файл недоступен у автора трека." if original_track.uploader_id != user.id else "Файл недоступен. Отправьте этот трек боту повторно, чтобы обновить ссылку."
            raise HTTPException(
                status_code=503,
                detail=detail_msg
            )
    
    # Generate secure temporary token with cached file_path
    token = generate_stream_token(track.id, user.id, file_path)
    
    # Return relative URL to avoid Mixed Content issues (HTTP vs HTTPS)
    # The frontend will resolve this against its own origin
    proxy_url = f"/api/player/audio/{token}"
    
    expires_at = int(time.time()) + STREAM_TOKEN_TTL
    
    # Log with info about whether we're using alternative
    if hd_track_info:
        logger.info(f"[Stream Request] Streaming MP3 track {track.id} (for HD {original_track.id}), user_id={user.id}")
    else:
        logger.info(f"[Stream Request] Generated token for track_id={track.id}, user_id={user.id}, file_size={track.file_size or 0} bytes")
    
    # Check if original track is MP3 and HD alternative exists
    hd_alt_info = None
    if hd_track_info is None and is_streamable(original_track.mime_type):
        # This is MP3 - check if HD version exists
        hd_alt = await find_hd_alternative(original_track, db)
        if hd_alt:
            hd_alt_info = {
                "id": hd_alt.id,
                "title": hd_alt.title,
            }
            logger.info(f"[Stream Request] HD alternative found for track {original_track.id}: HD track {hd_alt.id}")
    
    return StreamUrlResponse(
        url=proxy_url,
        expires_at=expires_at,
        track_id=track.id,
        # HD info: when playing MP3 alternative, tell about original HD
        # OR when playing MP3 and HD alternative exists
        hd_track_id=hd_track_info["id"] if hd_track_info else (hd_alt_info["id"] if hd_alt_info else None),
        hd_track_title=hd_track_info["title"] if hd_track_info else (hd_alt_info["title"] if hd_alt_info else None),
        is_hd_available=hd_track_info is not None or hd_alt_info is not None,
    )


@router.get("/audio/{token}")
async def stream_audio(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: Optional[str] = Header(None),
):
    """
    Secure audio proxy endpoint with Range request support.
    Streams audio through our server - bot token never exposed to client.
    Supports HTTP 206 Partial Content for fast seeking.
    """
    # Validate token - now includes cached file_path (saves ~300ms Telegram API call)
    token_data = validate_stream_token(token)
    if not token_data:
        logger.warning(f"[Audio Stream] Invalid/expired token: {token[:20]}...")
        raise HTTPException(status_code=401, detail="Invalid or expired stream token")
    
    track_id, user_id, file_path = token_data
    logger.debug(f"[Audio Stream] Token validated: track_id={track_id}, user_id={user_id}")
    
    # Get track for metadata (file_path already validated and cached in token)
    # Note: user_id in token is just for logging, access was validated when token was created
    track = await db.scalar(
        select(Track).where(Track.id == track_id)
    )
    
    if not track:
        logger.warning(f"[Audio Stream] Track not found: track_id={track_id}")
        raise HTTPException(status_code=404, detail="Track not found")
    
    # file_path is already in token - no need for second Telegram API call!
    if not file_path:
        logger.error(f"[Audio Stream] Empty file_path for track_id={track_id}")
        raise HTTPException(status_code=503, detail="File unavailable")
    
    # Stream from Telegram through our proxy
    # Use configurable API URL (supports local Telegram Bot API server for >20MB files)
    base_url = settings.telegram_api_url.rstrip('/')
    telegram_url = f"{base_url}/file/bot{settings.bot_token}/{file_path}"
    
    # Determine content type and filename
    content_type = track.mime_type or "audio/mpeg"
    safe_title = re.sub(r'[^\w\s.-]', '', track.title or 'audio')
    safe_title = safe_title.encode('ascii', 'ignore').decode('ascii') or 'audio'
    
    # Get file size from track or fetch from Telegram
    file_size = track.file_size
    
    # Parse Range header
    start_byte = 0
    end_byte = None
    
    if range and range.startswith("bytes="):
        try:
            range_spec = range[6:]  # Remove "bytes="
            if range_spec.startswith("-"):
                # Last N bytes: bytes=-500
                if file_size:
                    start_byte = max(0, file_size - int(range_spec[1:]))
            elif "-" in range_spec:
                parts = range_spec.split("-")
                start_byte = int(parts[0]) if parts[0] else 0
                if parts[1]:
                    end_byte = int(parts[1])
        except (ValueError, IndexError):
            pass
    
    # Build headers for Telegram request
    telegram_headers = {}
    if start_byte > 0 or end_byte:
        range_value = f"bytes={start_byte}-"
        if end_byte:
            range_value = f"bytes={start_byte}-{end_byte}"
        telegram_headers["Range"] = range_value
    
    # Make request to Telegram FIRST to validate and get actual content info
    session = await get_http_session()
    try:
        telegram_response = await session.get(telegram_url, headers=telegram_headers)
    except aiohttp.ClientError as e:
        logger.error(f"Failed to connect to Telegram for streaming: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch audio from Telegram")
    
    # Check if Telegram returned an error
    if telegram_response.status >= 400:
        await telegram_response.release()
        logger.error(f"Telegram returned error {telegram_response.status} for file {file_path}")
        if telegram_response.status == 404:
            raise HTTPException(status_code=404, detail="File no longer available on Telegram")
        raise HTTPException(status_code=503, detail=f"Telegram error: {telegram_response.status}")
    
    # Get ACTUAL content length from Telegram response (not from our DB!)
    actual_content_length = telegram_response.headers.get("Content-Length")
    telegram_content_range = telegram_response.headers.get("Content-Range")
    
    # Diagnostic: log stream start
    stream_start_time = time.time()
    expected_bytes = int(actual_content_length) if actual_content_length else 0
    logger.info(f"[Stream Start] track_id={track_id}, expected_bytes={expected_bytes}, range={range or 'full'}")
    
    async def stream_generator():
        """Stream audio from already-opened Telegram response with diagnostic logging"""
        bytes_sent = 0
        chunk_count = 0
        try:
            async for chunk in telegram_response.content.iter_chunked(64 * 1024):
                bytes_sent += len(chunk)
                chunk_count += 1
                yield chunk
            
            # Stream completed successfully
            elapsed = time.time() - stream_start_time
            speed_kbps = (bytes_sent / 1024) / elapsed if elapsed > 0 else 0
            logger.info(f"[Stream Complete] track_id={track_id}, bytes_sent={bytes_sent}, chunks={chunk_count}, elapsed={elapsed:.2f}s, speed={speed_kbps:.1f}KB/s")
            
        except asyncio.CancelledError:
            # Client disconnected (normal - seek, track change, etc.)
            elapsed = time.time() - stream_start_time
            logger.info(f"[Stream Cancelled] track_id={track_id}, bytes_sent={bytes_sent}/{expected_bytes}, elapsed={elapsed:.2f}s (client disconnected)")
            raise
            
        except aiohttp.ClientPayloadError as e:
            # Telegram closed connection unexpectedly
            elapsed = time.time() - stream_start_time
            logger.error(f"[Stream Error] track_id={track_id}, bytes_sent={bytes_sent}/{expected_bytes}, elapsed={elapsed:.2f}s, error=ClientPayloadError: {e}")
            raise
            
        except Exception as e:
            # Any other error
            elapsed = time.time() - stream_start_time
            logger.error(f"[Stream Error] track_id={track_id}, bytes_sent={bytes_sent}/{expected_bytes}, elapsed={elapsed:.2f}s, error={type(e).__name__}: {e}")
            raise
            
        finally:
            await telegram_response.release()
    
    # Build response headers (CORS handled by middleware — no manual override)
    response_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{safe_title}.mp3"',
        "Cache-Control": "private, max-age=3600",  # Cache 1 hour
    }
    
    # If Telegram returned 206 Partial Content, pass it through
    if telegram_response.status == 206 and telegram_content_range:
        response_headers["Content-Range"] = telegram_content_range
        if actual_content_length:
            response_headers["Content-Length"] = actual_content_length
        
        return StreamingResponse(
            stream_generator(),
            status_code=206,
            media_type=content_type,
            headers=response_headers,
        )
    
    # Regular 200 response - use actual content length from Telegram
    if actual_content_length:
        response_headers["Content-Length"] = actual_content_length
    
    return StreamingResponse(
        stream_generator(),
        media_type=content_type,
        headers=response_headers,
    )


@router.post("/stream/batch")
async def get_batch_stream_urls(
    track_ids: list[int],
    request: Request,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get secure proxy URLs for multiple tracks at once.
    Pre-fetches Telegram file paths in parallel for faster playback.
    """
    import asyncio
    
    if len(track_ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 tracks per request")
    
    # Get tracks (allow public tracks or tracks in user's library)
    from sqlalchemy import or_, exists
    
    # Subquery to check if user has track in their library
    user_has_track = (
        select(UserLibrary.id)
        .where(
            UserLibrary.track_id == Track.id,
            UserLibrary.user_id == user.id
        )
        .exists()
    )
    
    result = await db.execute(
        select(Track).where(
            Track.id.in_(track_ids),
            or_(
                Track.is_public == True,
                Track.uploader_id == user.id,
                user_has_track
            )
        )
    )
    tracks = {t.id: t for t in result.scalars().all()}
    
    # Pre-fetch all file paths in parallel (key optimization!)
    # Also try to refresh stale file_ids from channel messages and support HD alternatives
    async def get_file_path_for_track(track):
        try:
            file_size_mb = (track.file_size or 0) / (1024 * 1024)
            is_track_hd = not is_streamable(track.mime_type)
            is_too_large = file_size_mb > 20

            # If HD or >20MB, try to find streamable alternative first
            if is_track_hd or is_too_large:
                alt = await find_streamable_alternative(track, db)
                if alt:
                    alt_path = await get_telegram_file_path(alt.file_id)
                    if alt_path:
                        logger.info(f"[Batch] Using MP3 alternative {alt.id} for HD/large track {track.id}")
                        return alt_path, None, True, None  # (file_path, new_file_id, is_hd_or_large, err_code)
                # HD/large with no alternative or path: not streamable, but NOT unavailable!
                return None, None, True, None

            # Normal streamable track: try direct get_telegram_file_path
            file_path = await get_telegram_file_path(track.file_id)
            if file_path:
                return file_path, None, False, None  # (file_path, new_file_id, is_hd_or_large, err_code)
            
            # File path is None - try to refresh file_id from channel
            logger.info(f"[Batch] Track {track.id} file_id stale, attempting refresh from channel...")
            new_file_id, err_code, _ = await refresh_file_id_from_channel(track.id, db)
            
            if new_file_id:
                # Try again with new file_id
                file_path = await get_telegram_file_path(new_file_id)
                if file_path:
                    logger.info(f"[Batch] Track {track.id} file_id refreshed successfully!")
                return file_path, new_file_id, False, None
            
            return None, None, False, err_code
        except Exception as e:
            logger.debug(f"Failed to get file path for track {track.id}: {e}")
            return None, None, False, None
    
    # Fetch all file paths concurrently
    file_path_tasks = []
    track_order = []
    for track_id in track_ids:
        track = tracks.get(track_id)
        if track:
            file_path_tasks.append(get_file_path_for_track(track))
            track_order.append(track_id)
    
    results = await asyncio.gather(*file_path_tasks, return_exceptions=True)
    file_path_map = {}
    
    # Process results and update tracks with new file_ids
    tracks_to_update = []
    tracks_to_mark_unavailable = []
    
    for track_id, result in zip(track_order, results):
        track = tracks.get(track_id)
        if isinstance(result, Exception) or result is None:
            file_path_map[track_id] = None
            continue
        
        file_path, new_file_id, is_hd_or_large, err_code = (result if len(result) == 4 else (*result, None))
        file_path_map[track_id] = file_path
        
        if new_file_id and track:
            # Update track with new file_id
            track.file_id = new_file_id
            track.is_unavailable = False
            tracks_to_update.append(track_id)
        elif not file_path and track and not track.is_unavailable and not is_hd_or_large:
            # Do NOT mark tracks created within the last 10 minutes as unavailable
            # (they may be actively importing or propagating to Telegram CDN)
            now = utcnow()
            created = track.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            is_recent = bool(created and (now - created).total_seconds() < 600)
            
            if not is_recent:
                # Mark as unavailable for tracks whose telegram path is truly gone or channel is dead
                track.is_unavailable = True
                tracks_to_mark_unavailable.append(track_id)
            else:
                logger.debug(f"[Batch] Skipping mark_unavailable for recently created track {track_id}")
    
    # Commit updates in bulk
    if tracks_to_update or tracks_to_mark_unavailable:
        try:
            await db.commit()
            if tracks_to_update:
                logger.info(f"[Batch] Refreshed file_ids for tracks: {tracks_to_update}")
            if tracks_to_mark_unavailable:
                logger.warning(f"[Batch] Marked tracks as unavailable: {tracks_to_mark_unavailable}")
        except Exception as e:
            logger.error(f"[Batch] Failed to update tracks: {e}")
            await db.rollback()
    
    urls = []
    expires_at = int(time.time()) + STREAM_TOKEN_TTL
    
    for track_id in track_ids:
        track = tracks.get(track_id)
        if not track:
            urls.append({
                "track_id": track_id,
                "url": None,
                "error": "Track not found"
            })
            continue
        
        file_path = file_path_map.get(track_id)
        if isinstance(file_path, Exception) or not file_path:
            # Check if file is too large
            file_size_mb = (track.file_size or 0) / (1024 * 1024)
            if file_size_mb > 20:
                error_msg = f"File too large ({file_size_mb:.1f}MB)"
            elif track.is_unavailable:
                error_msg = "Track unavailable - re-upload required"
            else:
                error_msg = "Could not get file path"
            urls.append({
                "track_id": track_id,
                "url": None,
                "error": error_msg
            })
            continue
        
        # Generate token WITH cached file_path (saves ~300ms on stream start)
        token = generate_stream_token(track_id, user.id, file_path)
        urls.append({
            "track_id": track_id,
            "url": f"/api/player/audio/{token}",  # Relative URL
            "expires_at": expires_at,
            "error": None
        })
    
    return {"urls": urls}


@router.post("/prefetch")
async def prefetch_file_paths(
    track_ids: list[int],
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Pre-cache Telegram file paths for a list of tracks.
    Call this when loading track list to speed up first play.
    Runs in background - returns immediately.
    """
    if len(track_ids) > 20:
        track_ids = track_ids[:20]  # Limit to 20 tracks
    
    # Get tracks (allow public tracks or tracks in user's library)
    from sqlalchemy import or_, exists
    
    # Subquery to check if user has track in their library
    user_has_track = (
        select(UserLibrary.id)
        .where(
            UserLibrary.track_id == Track.id,
            UserLibrary.user_id == user.id
        )
        .exists()
    )
    
    result = await db.execute(
        select(Track).where(
            Track.id.in_(track_ids),
            or_(
                Track.is_public == True,
                Track.uploader_id == user.id,
                user_has_track
            )
        )
    )
    tracks = result.scalars().all()
    
    # Prefetch file paths in parallel (fire and forget style, but we await for better caching)
    import asyncio
    
    async def prefetch_one(track):
        try:
            await get_telegram_file_path(track.file_id)
        except Exception as e:
            logger.debug(f"Prefetch failed for track {track.id}: {e}")
    
    # Run all prefetches concurrently
    await asyncio.gather(*[prefetch_one(t) for t in tracks], return_exceptions=True)
    
    return {
        "success": True,
        "prefetched": len(tracks),
        "message": f"Prefetched {len(tracks)} file paths"
    }


@router.post("/play/{track_id}")
async def record_play(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Record that a track was fully played.
    Increments global play_count and user's personal play_count.
    """
    # Get track (any public track or own)
    track = await db.scalar(
        select(Track).where(Track.id == track_id)
    )
    
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    if not track.is_public and track.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="Track is private")
    
    # Increment global play count
    track.play_count = (track.play_count or 0) + 1
    track.last_played_at = utcnow()
    
    # Update user's library entry if exists
    lib_entry = await db.scalar(
        select(UserLibrary).where(
            UserLibrary.user_id == user.id,
            UserLibrary.track_id == track_id
        )
    )
    
    if lib_entry:
        lib_entry.play_count = (lib_entry.play_count or 0) + 1
        lib_entry.last_played_at = utcnow()
    
    await db.commit()
    
    return {
        "success": True,
        "track_id": track_id,
        "play_count": track.play_count,
        "user_play_count": lib_entry.play_count if lib_entry else 0
    }


@router.post("/download/{track_id}")
async def download_track(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send track to user via Telegram bot (download).
    Uses sendAudio to forward the file from bot to user.
    Works for any public track.
    """
    # Get track (any public track or own)
    track = await db.scalar(
        select(Track).where(Track.id == track_id)
    )
    
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    if not track.is_public and track.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="Track is private")
    
    # Send audio via Bot API (sendAudio works for any file size, unlike getFile)
    base_url = settings.telegram_api_url.rstrip('/')
    api_url = f"{base_url}/bot{settings.bot_token}/sendAudio"
    
    caption = f"🎧 {track.artist or 'Неизвестен'} - {track.title or 'Без названия'}"
    
    payload = {
        "chat_id": user.id,
        "audio": track.file_id,
        "caption": caption,
        "performer": track.artist,
        "title": track.title,
    }
    if track.duration:
        payload["duration"] = track.duration
    
    session = await get_http_session()
    try:
        async with session.post(api_url, json=payload) as resp:
            data = await resp.json()
            
            if not data.get("ok"):
                error_desc = data.get("description", "Unknown error")
                logger.error(f"Telegram sendAudio error: {error_desc}")
                raise HTTPException(status_code=503, detail=f"Failed to send: {error_desc}")
            
            return {"success": True, "track_id": track_id}
    except aiohttp.ClientError as e:
        logger.error(f"HTTP error sending audio: {e}")
        raise HTTPException(status_code=503, detail="Failed to send audio")


@router.post("/download-playlist")
async def download_playlist(
    request: DownloadPlaylistRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send multiple tracks as media group via Telegram bot.
    Tracks are sent in batches of 10 (Telegram limit).
    Header message is sent first, then audio batches.
    """
    if not request.track_ids:
        raise HTTPException(status_code=400, detail="No tracks provided")
    
    # Get tracks (allow public tracks or tracks in user's library)
    from sqlalchemy import or_, exists
    
    # Subquery to check if user has track in their library
    user_has_track = (
        select(UserLibrary.id)
        .where(
            UserLibrary.track_id == Track.id,
            UserLibrary.user_id == user.id
        )
        .exists()
    )
    
    result = await db.execute(
        select(Track).where(
            Track.id.in_(request.track_ids),
            or_(
                Track.is_public == True,
                Track.uploader_id == user.id,
                user_has_track
            )
        )
    )
    tracks_map = {t.id: t for t in result.scalars().all()}
    
    # Maintain order from request
    tracks = [tracks_map[tid] for tid in request.track_ids if tid in tracks_map]
    
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found")
    
    session = await get_http_session()
    
    # Send header message first
    total_batches = (len(tracks) + 9) // 10
    header_text = f"📁 <b>Плейлист: {request.playlist_name}</b>\n🎵 {len(tracks)} треков"
    if total_batches > 1:
        header_text += f"\n📦 Будет отправлено {total_batches} сообщениями"
    
    header_url = f"{settings.telegram_api_url}/bot{settings.bot_token}/sendMessage"
    header_payload = {
        "chat_id": user.id,
        "text": header_text,
        "parse_mode": "HTML"
    }
    
    try:
        async with session.post(header_url, json=header_payload) as resp:
            pass  # Header sent
    except Exception:
        pass  # Continue even if header fails
    
    api_url = f"{settings.telegram_api_url}/bot{settings.bot_token}/sendMediaGroup"
    
    batch_size = 10
    total_sent = 0
    
    for i in range(0, len(tracks), batch_size):
        batch = tracks[i:i + batch_size]
        
        # Build media group (no captions - header already sent)
        media = []
        for track in batch:
            item = {
                "type": "audio",
                "media": track.file_id,
                "performer": track.artist or "",
                "title": track.title or "Без названия",
            }
            if track.duration:
                item["duration"] = track.duration
            media.append(item)
        
        payload = {
            "chat_id": user.id,
            "media": media,
        }
        
        try:
            async with session.post(api_url, json=payload) as resp:
                data = await resp.json()
                
                if data.get("ok"):
                    total_sent += len(batch)
                else:
                    # If media group fails, try sending individually
                    for track in batch:
                        try:
                            single_url = f"{settings.telegram_api_url}/bot{settings.bot_token}/sendAudio"
                            single_payload = {
                                "chat_id": user.id,
                                "audio": track.file_id,
                                "performer": track.artist,
                                "title": track.title,
                            }
                            async with session.post(single_url, json=single_payload) as single_resp:
                                if (await single_resp.json()).get("ok"):
                                    total_sent += 1
                        except Exception:
                            continue
            
            # Delay between batches to avoid flood limits
            if i + batch_size < len(tracks):
                import asyncio
                await asyncio.sleep(1.0)
                
        except aiohttp.ClientError as e:
            logger.error(f"HTTP error sending media group: {e}")
    
    return {"success": True, "sent": total_sent, "total": len(tracks)}


@router.get("/diagnostics")
async def get_player_diagnostics():
    """
    Get diagnostic information about player streaming system.
    Useful for debugging streaming issues.
    """
    global _file_path_cache, _stream_tokens
    
    now = time.time()
    
    # Count active/expired tokens
    active_tokens = sum(1 for v in _stream_tokens.values() if v[3] > now)
    expired_tokens = len(_stream_tokens) - active_tokens
    
    # Count cached file paths
    active_paths = sum(1 for v in _file_path_cache.values() if v[1] > now)
    expired_paths = len(_file_path_cache) - active_paths
    
    # HTTP session stats
    session_stats = get_session_stats()
    
    # Test Telegram API connectivity
    telegram_status = "unknown"
    telegram_latency_ms = None
    try:
        session = await get_http_session()
        base_url = settings.telegram_api_url.rstrip('/')
        api_url = f"{base_url}/bot{settings.bot_token}/getMe"
        
        start = time.time()
        proxy = get_proxy_url()
        async with session.get(api_url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            telegram_latency_ms = round((time.time() - start) * 1000, 1)
            if resp.status == 200:
                data = await resp.json()
                if data.get("ok"):
                    telegram_status = "ok"
                else:
                    telegram_status = f"error: {data.get('description', 'unknown')}"
            else:
                telegram_status = f"http_{resp.status}"
    except asyncio.TimeoutError:
        telegram_status = "timeout"
    except Exception as e:
        telegram_status = f"error: {type(e).__name__}"
    
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stream_tokens": {
            "active": active_tokens,
            "expired": expired_tokens,
            "ttl_seconds": STREAM_TOKEN_TTL,
        },
        "file_path_cache": {
            "active": active_paths,
            "expired": expired_paths,
            "ttl_seconds": FILE_PATH_CACHE_TTL,
        },
        "http_session": session_stats,
        "telegram_api": {
            "status": telegram_status,
            "latency_ms": telegram_latency_ms,
            "base_url": settings.telegram_api_url,
        }
    }
