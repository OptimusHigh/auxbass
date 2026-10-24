"""
TG Player API v2 - Playlists Router

User playlist management.
"""
import logging
import re
from typing import Optional, List
from collections import defaultdict
from datetime import datetime
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import select, func, delete, update, union_all, asc, desc, or_, String, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from aiogram.types import BufferedInputFile

from shared.database import get_db
from shared.models import (
    Playlist, PlaylistTrack, Track, UserLibrary, AlbumTrack, User,
    PlaylistSubscription, TrackEnrichment, UserChannel, TrackTag, utcnow
)
from shared.config import get_settings
from shared.images import crop_image_to_square
from api.utils.bot_helpers import get_bot as _get_bot

logger = logging.getLogger(__name__)

COVERS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "covers"

# Image magic bytes for validation
_COVER_MAGIC_BYTES = {
    b'\xff\xd8\xff': 'jpg',
    b'\x89PNG': 'png',
    b'GIF87a': 'gif',
    b'GIF89a': 'gif',
    b'RIFF': 'webp',
}


def _safe_cover_path(url: str) -> 'Optional[Path]':
    """Safely resolve cover file path, preventing path traversal."""
    import os
    relative = url.replace("/api/covers/", "")
    safe_name = os.path.basename(relative)
    if not safe_name or safe_name in ('.', '..'):
        return None
    resolved = (COVERS_DIR / safe_name).resolve()
    if not str(resolved).startswith(str(COVERS_DIR.resolve())):
        return None
    return resolved


from api.routers.auth import get_current_user, require_premium
from api.utils.responses import track_to_response, streamable_track_filter
from api.schemas.common import TelegramUser, PaginatedResponse
from api.schemas.tracks import TrackResponse
from api.schemas.playlists import (
    PlaylistCreate,
    PlaylistUpdate,
    PlaylistResponse,
    PlaylistDetailResponse,
    PlaylistsListResponse,
    AddTrackRequest,
    ReorderRequest,
)


router = APIRouter(tags=["Playlists"])


async def get_playlists_tags(
    db: AsyncSession,
    playlist_ids: list[int],
    limit_per_playlist: int = 4,
) -> dict[int, list[str]]:
    """
    Get top tags for a list of playlist IDs.
    Aggregates from TrackTag (user & Last.fm) and TrackEnrichment.genre of playlist tracks,
    as well as any #hashtags in playlist description.
    Returns dict mapping playlist_id -> list of top tags.
    """
    if not playlist_ids:
        return {}

    tags_by_playlist: dict[int, list[str]] = defaultdict(list)
    seen_by_playlist: dict[int, set[str]] = defaultdict(set)

    # 1. First, check playlist descriptions for explicit #hashtags
    desc_query = (
        select(Playlist.id, Playlist.description)
        .where(Playlist.id.in_(playlist_ids))
        .where(Playlist.description.is_not(None))
    )
    desc_result = await db.execute(desc_query)
    for pl_id, description in desc_result.all():
        if description:
            hashtags = re.findall(r'#([a-zA-Zа-яА-ЯёЁ0-9_-]+)', description)
            for ht in hashtags:
                ht_clean = ht.lower().strip()
                if ht_clean and ht_clean not in seen_by_playlist[pl_id]:
                    if len(tags_by_playlist[pl_id]) < limit_per_playlist:
                        seen_by_playlist[pl_id].add(ht_clean)
                        tags_by_playlist[pl_id].append(ht_clean)

    # 2. Fetch top tags from TrackTag via PlaylistTrack
    query = (
        select(PlaylistTrack.playlist_id, TrackTag.tag, func.count(TrackTag.id).label("cnt"))
        .join(TrackTag, TrackTag.track_id == PlaylistTrack.track_id)
        .where(PlaylistTrack.playlist_id.in_(playlist_ids))
        .group_by(PlaylistTrack.playlist_id, TrackTag.tag)
        .order_by(desc("cnt"))
    )
    result = await db.execute(query)
    for pl_id, tag, _ in result.all():
        if not pl_id or not tag:
            continue
        tag_lower = tag.lower().strip()
        if tag_lower and tag_lower not in seen_by_playlist[pl_id]:
            if len(tags_by_playlist[pl_id]) < limit_per_playlist:
                seen_by_playlist[pl_id].add(tag_lower)
                tags_by_playlist[pl_id].append(tag)

    # 3. Check TrackEnrichment.genre for playlists that still have room
    missing = [pid for pid in playlist_ids if len(tags_by_playlist[pid]) < limit_per_playlist]
    if missing:
        genre_query = (
            select(PlaylistTrack.playlist_id, TrackEnrichment.genre, func.count(TrackEnrichment.id).label("cnt"))
            .join(TrackEnrichment, TrackEnrichment.track_id == PlaylistTrack.track_id)
            .where(PlaylistTrack.playlist_id.in_(missing))
            .where(TrackEnrichment.genre.is_not(None))
            .where(TrackEnrichment.genre != "")
            .group_by(PlaylistTrack.playlist_id, TrackEnrichment.genre)
            .order_by(desc("cnt"))
        )
        genre_result = await db.execute(genre_query)
        for pl_id, genre, _ in genre_result.all():
            if not pl_id or not genre:
                continue
            g_lower = genre.lower().strip()
            if g_lower and g_lower not in seen_by_playlist[pl_id]:
                if len(tags_by_playlist[pl_id]) < limit_per_playlist:
                    seen_by_playlist[pl_id].add(g_lower)
                    tags_by_playlist[pl_id].append(genre.lower())

    return dict(tags_by_playlist)


async def get_playlist_info(
    db: AsyncSession, 
    playlist_id: int,
    custom_cover_url: Optional[str] = None,
) -> tuple[int, int, int, Optional[str], List[str]]:
    """
    Get track count, total duration, unavailable track count, cover, and covers array for a playlist.
    Returns: (track_count, total_duration, unavailable_track_count, cover_url, covers)
    If custom_cover_url is set, it overrides the track collage.
    Otherwise, cover is built from track covers (collage of up to 4 track covers).
    """
    # Count, duration, and unavailable count
    result = await db.execute(
        select(
            func.count(PlaylistTrack.id),
            func.coalesce(func.sum(Track.duration), 0),
            func.coalesce(func.sum(case((Track.is_unavailable == True, 1), else_=0)), 0)
        )
        .join(Track, Track.id == PlaylistTrack.track_id)
        .where(PlaylistTrack.playlist_id == playlist_id)
    )
    row = result.one()
    track_count = row[0] or 0
    total_duration = row[1] or 0
    unavailable_count = row[2] or 0
    
    if custom_cover_url:
        return track_count, total_duration, unavailable_count, custom_cover_url, [custom_cover_url]
    
    p_cover = await db.scalar(
        select(Playlist.custom_cover_url).where(Playlist.id == playlist_id)
    )
    if p_cover:
        return track_count, total_duration, unavailable_count, p_cover, [p_cover]
    
    # Get track covers for collage (up to 4)
    covers_result = await db.execute(
        select(Track)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .join(TrackEnrichment, TrackEnrichment.track_id == Track.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .where(TrackEnrichment.cover_url.is_not(None))
        .where(TrackEnrichment.cover_url != "")
        .options(selectinload(Track.enrichment))
        .order_by(PlaylistTrack.position.asc(), PlaylistTrack.id.asc())
        .limit(4)
    )
    track_covers = []
    for track in covers_result.scalars().all():
        if track.enrichment and track.enrichment.cover_url:
            track_covers.append(track.enrichment.cover_url)
    
    # Build covers array from track covers (up to 4 for collage)
    covers = track_covers[:4]
    cover_url = track_covers[0] if track_covers else None
    
    return track_count, total_duration, unavailable_count, cover_url, covers


@router.get("", response_model=PlaylistsListResponse)
async def get_my_playlists(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: str = Query("created_at", pattern="^(name|track_count|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    include_subscribed: bool = Query(True, description="Include subscribed playlists"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user's public playlists (owned and subscribed). Supports search, sort and offset/limit pagination."""
    # Build set of user's visible playlist IDs (owned all + subscribed public)
    owned_ids_q = (
        select(Playlist.id)
        .where(Playlist.owner_id == user.id)
    )
    
    if include_subscribed:
        subscribed_ids_q = (
            select(Playlist.id)
            .join(PlaylistSubscription, PlaylistSubscription.playlist_id == Playlist.id)
            .where(PlaylistSubscription.user_id == user.id)
            .where(Playlist.is_public == True)
        )
        all_ids_subq = union_all(owned_ids_q, subscribed_ids_q).subquery()
        base_filter = Playlist.id.in_(select(all_ids_subq.c.id))
    else:
        # Owner sees ALL their playlists (public and private)
        base_filter = (Playlist.owner_id == user.id)
    
    # Track count subquery (for display and sort)
    track_count_subq = (
        select(
            PlaylistTrack.playlist_id,
            func.count(PlaylistTrack.id).label('track_count')
        )
        .group_by(PlaylistTrack.playlist_id)
        .subquery()
    )
    
    # Base query
    query = (
        select(Playlist, User, func.coalesce(track_count_subq.c.track_count, 0).label('tc'))
        .join(User, User.id == Playlist.owner_id)
        .outerjoin(track_count_subq, track_count_subq.c.playlist_id == Playlist.id)
        .where(base_filter)
    )
    count_query = (
        select(func.count(Playlist.id))
        .where(base_filter)
    )
    
    # Apply search
    if search:
        search_clean = search.lstrip('#').strip()
        search_term = f"%{search_clean}%"

        tag_playlist_ids = (
            select(PlaylistTrack.playlist_id)
            .join(TrackTag, TrackTag.track_id == PlaylistTrack.track_id)
            .where(TrackTag.tag.ilike(search_term))
        )
        genre_playlist_ids = (
            select(PlaylistTrack.playlist_id)
            .join(TrackEnrichment, TrackEnrichment.track_id == PlaylistTrack.track_id)
            .where(or_(
                TrackEnrichment.genre.ilike(search_term),
                func.cast(TrackEnrichment.tags, String).ilike(search_term)
            ))
        )

        search_filter = or_(
            Playlist.name.ilike(search_term),
            Playlist.description.ilike(search_term),
            Playlist.id.in_(tag_playlist_ids),
            Playlist.id.in_(genre_playlist_ids),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    
    # Count total
    total = await db.scalar(count_query) or 0
    
    # Sorting
    if sort_by == "name":
        sort_column = Playlist.name
    elif sort_by == "track_count":
        sort_column = func.coalesce(track_count_subq.c.track_count, 0)
    else:
        sort_column = Playlist.created_at
    
    if sort_order == "desc":
        query = query.order_by(desc(sort_column).nullslast(), desc(Playlist.id))
    else:
        query = query.order_by(asc(sort_column).nullsfirst(), asc(Playlist.id))
    
    # Pagination
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    rows = result.all()
    
    playlist_ids = [playlist.id for playlist, _, _ in rows]
    playlist_tags_map = await get_playlists_tags(db, playlist_ids)

    items = []
    for playlist, owner, tc in rows:
        track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist.id, playlist.custom_cover_url)
        
        is_owner = playlist.owner_id == user.id
        is_subscribed = False
        if not is_owner:
            subscription = await db.scalar(
                select(PlaylistSubscription)
                .where(
                    PlaylistSubscription.user_id == user.id,
                    PlaylistSubscription.playlist_id == playlist.id
                )
            )
            is_subscribed = subscription is not None
        
        items.append(PlaylistResponse(
            id=playlist.id,
            name=playlist.name,
            description=playlist.description,
            track_count=track_count,
            unavailable_track_count=unavailable_count,
            total_duration=total_duration,
            cover_url=cover_url,
            custom_cover_url=playlist.custom_cover_url,
            covers=covers,
            tags=playlist_tags_map.get(playlist.id),
            is_public=playlist.is_public,
            owner_id=owner.id,
            owner_name=owner.display_name,
            is_owner=is_owner,
            is_subscribed=is_subscribed,
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        ))
    
    return PlaylistsListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/manage/all", response_model=PlaylistsListResponse)
async def get_all_my_playlists(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get ALL user's playlists including private ones (for management/settings)"""
    # Count all owned playlists
    total = await db.scalar(
        select(func.count(Playlist.id))
        .where(Playlist.owner_id == user.id)
    ) or 0
    
    # Get all playlists
    offset = (page - 1) * per_page
    result = await db.execute(
        select(Playlist, User)
        .join(User, User.id == Playlist.owner_id)
        .where(Playlist.owner_id == user.id)
        .order_by(Playlist.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    rows = result.all()
    
    items = []
    for playlist, owner in rows:
        track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist.id, playlist.custom_cover_url)
        items.append(PlaylistResponse(
            id=playlist.id,
            name=playlist.name,
            description=playlist.description,
            track_count=track_count,
            unavailable_track_count=unavailable_count,
            total_duration=total_duration,
            cover_url=cover_url,
            custom_cover_url=playlist.custom_cover_url,
            covers=covers,
            is_public=playlist.is_public,
            owner_id=owner.id,
            owner_name=owner.display_name,
            is_owner=True,
            is_subscribed=False,
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        ))
    
    return PlaylistsListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


# ============== Global Playlists ==============

@router.get("/global", response_model=PlaylistsListResponse)
async def get_global_playlists(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: str = Query("created_at", pattern="^(name|track_count|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all public playlists from global library.
    
    Shows all public playlists with pagination, search and sort.
    """
    # Track count subqueries: total tracks & available tracks
    track_count_subq = (
        select(
            PlaylistTrack.playlist_id,
            func.count(PlaylistTrack.id).label('track_count'),
            func.coalesce(func.sum(case((Track.is_unavailable == False, 1), else_=0)), 0).label('available_count')
        )
        .join(Track, Track.id == PlaylistTrack.track_id)
        .group_by(PlaylistTrack.playlist_id)
        .subquery()
    )
    
    # Base query: owner profile not hidden, playlist is public, and has >= 1 available track
    query = (
        select(Playlist, User, func.coalesce(track_count_subq.c.track_count, 0).label('tc'))
        .join(User, User.id == Playlist.owner_id)
        .join(track_count_subq, track_count_subq.c.playlist_id == Playlist.id)
        .where(Playlist.is_public == True)
        .where(User.hide_profile == False)
        .where(track_count_subq.c.available_count > 0)
    )
    count_query = (
        select(func.count(Playlist.id))
        .join(User, User.id == Playlist.owner_id)
        .join(track_count_subq, track_count_subq.c.playlist_id == Playlist.id)
        .where(Playlist.is_public == True)
        .where(User.hide_profile == False)
        .where(track_count_subq.c.available_count > 0)
    )
    
    # Apply search
    if search:
        search_clean = search.lstrip('#').strip()
        search_term = f"%{search_clean}%"
        user_name_match = or_(
            User.username.ilike(search_term),
            User.first_name.ilike(search_term),
            User.last_name.ilike(search_term),
            (func.coalesce(User.first_name, '') + ' ' + func.coalesce(User.last_name, '')).ilike(search_term),
        )
        tag_playlist_ids = (
            select(PlaylistTrack.playlist_id)
            .join(TrackTag, TrackTag.track_id == PlaylistTrack.track_id)
            .where(TrackTag.tag.ilike(search_term))
        )
        genre_playlist_ids = (
            select(PlaylistTrack.playlist_id)
            .join(TrackEnrichment, TrackEnrichment.track_id == PlaylistTrack.track_id)
            .where(or_(
                TrackEnrichment.genre.ilike(search_term),
                func.cast(TrackEnrichment.tags, String).ilike(search_term)
            ))
        )
        search_filter = or_(
            Playlist.name.ilike(search_term),
            Playlist.description.ilike(search_term),
            user_name_match,
            Playlist.id.in_(tag_playlist_ids),
            Playlist.id.in_(genre_playlist_ids),
        )
        query = query.where(search_filter)
        count_query = (
            select(func.count(Playlist.id))
            .join(User, User.id == Playlist.owner_id)
            .where(Playlist.is_public == True)
            .where(search_filter)
        )
    
    # Count total
    total = await db.scalar(count_query) or 0
    
    # Sorting
    if sort_by == "name":
        sort_column = Playlist.name
    elif sort_by == "track_count":
        sort_column = func.coalesce(track_count_subq.c.track_count, 0)
    else:
        sort_column = Playlist.created_at
    
    if sort_order == "desc":
        query = query.order_by(desc(sort_column).nullslast(), desc(Playlist.id))
    else:
        query = query.order_by(asc(sort_column).nullsfirst(), asc(Playlist.id))
    
    # Pagination
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    rows = result.all()
    
    playlist_ids = [playlist.id for playlist, _, _ in rows]
    playlist_tags_map = await get_playlists_tags(db, playlist_ids)

    items = []
    for playlist, owner, tc in rows:
        track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist.id, playlist.custom_cover_url)
        
        is_owner = playlist.owner_id == user.id
        is_subscribed = False
        if not is_owner:
            subscription = await db.scalar(
                select(PlaylistSubscription)
                .where(
                    PlaylistSubscription.user_id == user.id,
                    PlaylistSubscription.playlist_id == playlist.id
                )
            )
            is_subscribed = subscription is not None
        
        items.append(PlaylistResponse(
            id=playlist.id,
            name=playlist.name,
            description=playlist.description,
            track_count=track_count,
            unavailable_track_count=unavailable_count,
            total_duration=total_duration,
            cover_url=cover_url,
            custom_cover_url=playlist.custom_cover_url,
            covers=covers,
            tags=playlist_tags_map.get(playlist.id),
            is_public=playlist.is_public,
            owner_id=owner.id,
            owner_name=owner.display_name,
            is_owner=is_owner,
            is_subscribed=is_subscribed,
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        ))
    
    return PlaylistsListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=PlaylistResponse)
async def create_playlist(
    data: PlaylistCreate,
    user: TelegramUser = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """Create a new playlist. Requires connected channel."""
    playlist = Playlist(
        owner_id=user.id,
        name=data.name,
        description=data.description,
        is_public=data.is_public,
    )
    db.add(playlist)
    await db.flush()
    await db.commit()
    
    # Get user info for response
    owner = await db.get(User, user.id)
    
    return PlaylistResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        track_count=0,
        total_duration=0,
        cover_url=None,
        custom_cover_url=None,
        covers=[],
        is_public=playlist.is_public,
        owner_id=owner.id,
        owner_name=owner.display_name,
        is_owner=True,
        is_subscribed=False,
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
    )


@router.get("/{playlist_id}", response_model=PlaylistDetailResponse)
async def get_playlist(
    playlist_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get playlist with tracks"""
    result = await db.execute(
        select(Playlist, User)
        .join(User, User.id == Playlist.owner_id)
        .where(Playlist.id == playlist_id)
    )
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    playlist, owner = row
    
    # Allow access if owner or if playlist is public
    if playlist.owner_id != user.id and not playlist.is_public:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Get tracks with library entries for response
    result = await db.execute(
        select(Track, UserLibrary)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .outerjoin(UserLibrary, (UserLibrary.track_id == Track.id) & (UserLibrary.user_id == user.id))
        .where(PlaylistTrack.playlist_id == playlist_id)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
        .order_by(PlaylistTrack.position.asc(), PlaylistTrack.id.asc())
    )
    rows = result.unique().all()
    
    tracks_response = [track_to_response(track, lib_entry) for track, lib_entry in rows]
    
    track_count = len(tracks_response)
    unavailable_count = sum(1 for row in rows if row[0].is_unavailable)
    total_duration = sum(t.duration or 0 for t in [row[0] for row in rows])
    
    # Build covers array from custom cover or track covers (up to 4 for collage)
    if playlist.custom_cover_url:
        cover_url = playlist.custom_cover_url
        covers = [playlist.custom_cover_url]
    else:
        track_covers = []
        for track, _ in rows:
            if track.enrichment and track.enrichment.cover_url and len(track_covers) < 4:
                track_covers.append(track.enrichment.cover_url)
        cover_url = track_covers[0] if track_covers else None
        covers = track_covers
    
    # Check if user is subscribed to this playlist
    is_subscribed = False
    if playlist.owner_id != user.id:
        subscription = await db.scalar(
            select(PlaylistSubscription)
            .where(
                PlaylistSubscription.user_id == user.id,
                PlaylistSubscription.playlist_id == playlist_id
            )
        )
        is_subscribed = subscription is not None
    
    # Collect tags from playlist tracks (aggregate unique tags)
    playlist_tags = None
    try:
        seen_tags = set()
        collected_tags = []
        for track, _ in rows:
            if track.enrichment and track.enrichment.tags:
                for tag in track.enrichment.tags:
                    tag_lower = tag.lower()
                    if tag_lower not in seen_tags:
                        seen_tags.add(tag_lower)
                        collected_tags.append(tag)
                        if len(collected_tags) >= 5:
                            break
            if len(collected_tags) >= 5:
                break
        if collected_tags:
            playlist_tags = collected_tags
    except Exception:
        playlist_tags = None

    if not playlist_tags:
        db_tags = await get_playlists_tags(db, [playlist.id], limit_per_playlist=5)
        playlist_tags = db_tags.get(playlist.id) or None
    
    return PlaylistDetailResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        track_count=track_count,
        unavailable_track_count=unavailable_count,
        total_duration=total_duration,
        cover_url=cover_url,
        custom_cover_url=playlist.custom_cover_url,
        covers=covers,
        tags=playlist_tags,
        is_public=playlist.is_public,
        owner_id=owner.id,
        owner_name=owner.display_name,
        is_owner=playlist.owner_id == user.id,
        is_subscribed=is_subscribed,
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
        tracks=tracks_response,
    )


@router.get("/{playlist_id}/ids")
async def get_playlist_track_ids(
    playlist_id: int,
    shuffle: bool = False,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all track IDs for a playlist.
    
    Lightweight endpoint for shuffle - returns only IDs.
    Allows access to public playlists even if user is not the owner.
    """
    playlist = await db.get(Playlist, playlist_id)
    
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Allow access if owner or if playlist is public
    if playlist.owner_id != user.id and not playlist.is_public:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    query = (
        select(Track.id)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .where(streamable_track_filter())
    )
    
    if shuffle:
        query = query.order_by(func.random())
    else:
        query = query.order_by(PlaylistTrack.position.asc())
    
    result = await db.execute(query)
    track_ids = result.scalars().all()
    
    return {"ids": track_ids, "total": len(track_ids)}


@router.put("/{playlist_id}", response_model=PlaylistResponse)
async def update_playlist(
    playlist_id: int,
    data: PlaylistUpdate,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update playlist name, description, visibility, and custom cover."""
    playlist = await db.get(Playlist, playlist_id)
    
    if not playlist or playlist.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    if getattr(playlist, "is_system", False) and data.name and data.name != playlist.name:
        raise HTTPException(status_code=403, detail="Cannot rename system playlist")
    if data.name:
        playlist.name = data.name
    if data.description is not None:
        playlist.description = data.description
    if data.is_public is not None:
        playlist.is_public = data.is_public
    if data.custom_cover_url is not None:
        playlist.custom_cover_url = data.custom_cover_url.strip() if data.custom_cover_url.strip() else None
    
    await db.commit()
    
    track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist.id, playlist.custom_cover_url)
    
    owner = await db.get(User, user.id)
    return PlaylistResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        track_count=track_count,
        unavailable_track_count=unavailable_count,
        total_duration=total_duration,
        cover_url=cover_url,
        custom_cover_url=playlist.custom_cover_url,
        covers=covers,
        is_public=playlist.is_public,
        owner_id=owner.id if owner else user.id,
        owner_name=owner.display_name if owner else (user.username or user.first_name),
        is_owner=True,
        is_subscribed=False,
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
    )


@router.post("/{playlist_id}/cover")
async def upload_playlist_cover(
    playlist_id: int,
    file: UploadFile = File(...),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload custom cover image for a playlist directly to Telegram storage (channel or PM)."""
    playlist = await db.get(Playlist, playlist_id)
    if not playlist or playlist.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")
    
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="Image size exceeds 10MB limit")
    
    # Validate actual image content by magic bytes
    if len(content) < 12:
        raise HTTPException(status_code=400, detail="File too small to be a valid image")
    header = content[:12]
    detected_ext = None
    for magic, ext in _COVER_MAGIC_BYTES.items():
        if header.startswith(magic):
            if magic == b'RIFF' and header[8:12] != b'WEBP':
                continue
            detected_ext = ext
            break
    if not detected_ext:
        raise HTTPException(status_code=400, detail="Invalid image file. Only JPEG, PNG, GIF, WebP are allowed.")
    
    # Crop to 1:1 square with centering and dimension normalization
    content, detected_ext = crop_image_to_square(content)

    safe_filename = f"cover_{playlist_id}.{detected_ext}"
    
    bot = _get_bot()
    caption = f"🖼 <b>Обложка плейлиста</b>: {playlist.name}\n\n#playlist_{playlist.id} #cover"
    
    # Target user channel if available, else user PM
    user_channel = await db.scalar(
        select(UserChannel).where(UserChannel.user_id == user.id, UserChannel.is_active == True)
    )
    target_chat_id = user_channel.channel_id if user_channel else user.id

    try:
        sent_msg = await bot.send_photo(
            chat_id=target_chat_id,
            photo=BufferedInputFile(file=content, filename=safe_filename),
            caption=caption,
        )
    except Exception as e:
        logger.warning(f"Failed to upload cover to target {target_chat_id}: {e}")
        # Fallback to user PM if channel post failed
        if user_channel and target_chat_id != user.id:
            try:
                sent_msg = await bot.send_photo(
                    chat_id=user.id,
                    photo=BufferedInputFile(file=content, filename=safe_filename),
                    caption=caption,
                )
            except Exception as err:
                logger.error(f"Failed to upload cover to Telegram PM fallback: {err}")
                raise HTTPException(status_code=502, detail="Не удалось загрузить обложку в Telegram")
        else:
            raise HTTPException(status_code=502, detail="Не удалось загрузить обложку в Telegram")

    photos = sent_msg.photo or []
    if not photos:
        raise HTTPException(status_code=502, detail="Telegram не вернул файл обложки")

    file_id = photos[-1].file_id

    # Clean up old local custom cover file if one existed
    if playlist.custom_cover_url and playlist.custom_cover_url.startswith("/api/covers/"):
        old_path = _safe_cover_path(playlist.custom_cover_url)
        if old_path and old_path.exists() and old_path.is_file():
            try:
                old_path.unlink()
            except OSError:
                pass

    playlist.custom_cover_url = f"/api/images/{file_id}"
    await db.commit()

    track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist_id, playlist.custom_cover_url)
    return {
        "status": "success",
        "custom_cover_url": playlist.custom_cover_url,
        "cover_url": cover_url,
        "covers": covers,
    }


@router.delete("/{playlist_id}/cover")
async def delete_playlist_cover(
    playlist_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete custom cover and revert to track collage."""
    playlist = await db.get(Playlist, playlist_id)
    if not playlist or playlist.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if getattr(playlist, "is_system", False):
        raise HTTPException(status_code=403, detail="Cannot delete system playlist")
        
    if playlist.custom_cover_url and playlist.custom_cover_url.startswith("/api/covers/"):
        old_path = _safe_cover_path(playlist.custom_cover_url)
        if old_path and old_path.exists() and old_path.is_file():
            try:
                old_path.unlink()
            except OSError:
                pass
                
    playlist.custom_cover_url = None
    await db.commit()
    
    track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist_id)
    return {
        "status": "deleted",
        "custom_cover_url": None,
        "cover_url": cover_url,
        "covers": covers,
    }



@router.delete("/{playlist_id}")
async def delete_playlist(
    playlist_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete playlist."""
    playlist = await db.get(Playlist, playlist_id)
    
    if not playlist or playlist.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Delete track associations first
    await db.execute(
        delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id)
    )
    
    await db.delete(playlist)
    await db.commit()
    
    return {"status": "deleted", "playlist_id": playlist_id}


@router.post("/{playlist_id}/tracks")
async def add_track_to_playlist(
    playlist_id: int,
    data: AddTrackRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add track to playlist."""
    playlist = await db.get(Playlist, playlist_id)
    
    if not playlist or playlist.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Check track exists - allow both library tracks and global tracks
    track = await db.get(Track, data.track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Check if already in playlist
    existing = await db.scalar(
        select(PlaylistTrack)
        .where(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.track_id == data.track_id
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="Track already in playlist")

    # --- Minecraft track limit guard ---
    if getattr(playlist, "is_system", False):
        owner = await db.get(User, playlist.owner_id)
        max_tracks = getattr(owner, "mc_max_tracks", 5) if owner else 5
        count_res = await db.execute(
            select(func.count()).where(PlaylistTrack.playlist_id == playlist_id)
        )
        current_count = count_res.scalar() or 0
        if current_count >= max_tracks:
            rank_id = getattr(owner, "mc_rank", "noob") if owner else "noob"
            rank_names = {
                "noob": "Новичок", "player": "Игрок", "veteran": "Ветеран",
                "pro": "Про", "elite": "Элита", "legend": "Легенда",
            }
            rank_name = rank_names.get(rank_id, rank_id.capitalize())
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Лимит треков для ранга «{rank_name}» ({max_tracks} шт.) исчерпан. "
                    f"Повысьте ранг на сервере GanjaCraft!"
                ),
            )

    # Get next position
    max_pos = await db.scalar(
        select(func.coalesce(func.max(PlaylistTrack.position), 0))
        .where(PlaylistTrack.playlist_id == playlist_id)
    ) or 0
    
    pt = PlaylistTrack(
        playlist_id=playlist_id,
        track_id=data.track_id,
        position=max_pos + 1,
    )
    playlist.updated_at = utcnow()
    db.add(pt)
    await db.commit()

    # Background: pre-build micro-pack for system playlist so sha1 is ready for Minecraft
    try:
        is_system = bool(getattr(playlist, "is_system", False))
    except Exception:
        is_system = False
    if is_system:
        try:
            from api.services.minecraft_pack import ensure_pack_for_track, get_cached_sha1
            # Only trigger if not already cached
            if not get_cached_sha1(track.id):
                # Use BackgroundTasks if available via request context, else asyncio.create_task
                # We create a fire-and-forget task
                import asyncio
                file_id = track.file_id
                tid = track.id
                title = track.title or ""
                async def _build_pack_bg(tid=tid, file_id=file_id, title=title):
                    try:
                        await ensure_pack_for_track(tid, file_id, title=title)
                    except Exception as e:
                        logger.warning(f"[minecraft] background pack build failed tid={tid}: {e}")
                try:
                    asyncio.create_task(_build_pack_bg())
                except RuntimeError:
                    # No running loop (should not happen in async context)
                    pass
                logger.info(f"[minecraft] queued background pack build for track {tid} (system playlist {playlist_id})")
        except Exception as e:
            logger.warning(f"[minecraft] failed to queue pack build: {e}")

    return {"status": "added", "position": max_pos + 1}


@router.delete("/{playlist_id}/tracks/{track_id}")
async def remove_track_from_playlist(
    playlist_id: int,
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove track from playlist."""
    playlist = await db.get(Playlist, playlist_id)
    
    if not playlist or playlist.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    pt = await db.scalar(
        select(PlaylistTrack)
        .where(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.track_id == track_id
        )
    )
    
    if not pt:
        raise HTTPException(status_code=404, detail="Track not in playlist")
    
    playlist.updated_at = utcnow()
    await db.delete(pt)
    await db.commit()
    
    return {"status": "removed"}


@router.put("/{playlist_id}/reorder")
async def reorder_playlist(
    playlist_id: int,
    data: ReorderRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder tracks in playlist."""
    playlist = await db.get(Playlist, playlist_id)
    
    if not playlist or playlist.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Update positions
    for position, track_id in enumerate(data.track_ids):
        await db.execute(
            update(PlaylistTrack)
            .where(
                PlaylistTrack.playlist_id == playlist_id,
                PlaylistTrack.track_id == track_id
            )
            .values(position=position)
        )
    
    playlist.updated_at = utcnow()
    await db.commit()
    
    return {"status": "reordered"}


# ============== Public Playlists ==============

@router.get("/public/explore", response_model=PlaylistsListResponse)
async def get_public_playlists(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all public playlists from all users"""
    # Count public playlists
    total = await db.scalar(
        select(func.count(Playlist.id))
        .where(Playlist.is_public == True)
    ) or 0
    
    # Get playlists with owner info
    offset = (page - 1) * per_page
    result = await db.execute(
        select(Playlist, User)
        .join(User, User.id == Playlist.owner_id)
        .where(Playlist.is_public == True)
        .order_by(Playlist.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    rows = result.all()
    
    playlist_ids = [playlist.id for playlist, _ in rows]
    playlist_tags_map = await get_playlists_tags(db, playlist_ids)

    items = []
    for playlist, owner in rows:
        track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist.id, playlist.custom_cover_url)
        
        # Check if current user is subscribed or is owner
        is_owner = playlist.owner_id == user.id
        is_subscribed = False
        if not is_owner:
            subscription = await db.scalar(
                select(PlaylistSubscription)
                .where(
                    PlaylistSubscription.user_id == user.id,
                    PlaylistSubscription.playlist_id == playlist.id
                )
            )
            is_subscribed = subscription is not None
        
        items.append(PlaylistResponse(
            id=playlist.id,
            name=playlist.name,
            description=playlist.description,
            track_count=track_count,
            unavailable_track_count=unavailable_count,
            total_duration=total_duration,
            cover_url=cover_url,
            custom_cover_url=playlist.custom_cover_url,
            covers=covers,
            tags=playlist_tags_map.get(playlist.id),
            is_public=playlist.is_public,
            owner_id=owner.id,
            owner_name=owner.display_name,
            is_owner=is_owner,
            is_subscribed=is_subscribed,
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        ))
    
    return PlaylistsListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/user/{user_id}", response_model=PlaylistsListResponse)
async def get_user_public_playlists(
    user_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get public playlists of a specific user"""
    # If viewing own playlists, show all
    is_own = user_id == user.id
    
    query = select(Playlist).where(Playlist.owner_id == user_id)
    if not is_own:
        query = query.where(Playlist.is_public == True)
    
    # Count
    total = await db.scalar(
        select(func.count(Playlist.id))
        .where(Playlist.owner_id == user_id)
        .where(True if is_own else Playlist.is_public == True)
    ) or 0
    
    # Get owner info
    owner = await db.get(User, user_id)
    if not owner:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get playlists
    offset = (page - 1) * per_page
    result = await db.execute(
        query.order_by(Playlist.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    playlists = result.scalars().all()
    
    playlist_ids = [playlist.id for playlist in playlists]
    playlist_tags_map = await get_playlists_tags(db, playlist_ids)

    items = []
    for playlist in playlists:
        track_count, total_duration, unavailable_count, cover_url, covers = await get_playlist_info(db, playlist.id, playlist.custom_cover_url)
        # Check if current user is subscribed
        is_subscribed = False
        if not is_own:
            subscription = await db.scalar(
                select(PlaylistSubscription)
                .where(
                    PlaylistSubscription.user_id == user.id,
                    PlaylistSubscription.playlist_id == playlist.id
                )
            )
            is_subscribed = subscription is not None
        
        items.append(PlaylistResponse(
            id=playlist.id,
            name=playlist.name,
            description=playlist.description,
            track_count=track_count,
            unavailable_track_count=unavailable_count,
            total_duration=total_duration,
            cover_url=cover_url,
            custom_cover_url=playlist.custom_cover_url,
            covers=covers,
            tags=playlist_tags_map.get(playlist.id),
            is_public=playlist.is_public,
            owner_id=owner.id,
            owner_name=owner.display_name,
            is_owner=is_own,
            is_subscribed=is_subscribed,
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        ))
    
    return PlaylistsListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


# ============== Playlist Subscription ==============

@router.post("/{playlist_id}/subscribe")
async def subscribe_to_playlist(
    playlist_id: int,
    user: TelegramUser = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Subscribe to a public playlist.
    Adds the playlist to user's library with auto-updates.
    """
    # Check playlist exists and is public
    result = await db.execute(
        select(Playlist, User)
        .join(User, User.id == Playlist.owner_id)
        .where(Playlist.id == playlist_id)
    )
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    playlist, owner = row
    
    if not playlist.is_public:
        raise HTTPException(status_code=400, detail="Playlist is not public")
    
    if playlist.owner_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot subscribe to your own playlist")
    
    # Check if already subscribed (idempotent)
    existing = await db.scalar(
        select(PlaylistSubscription)
        .where(
            PlaylistSubscription.user_id == user.id,
            PlaylistSubscription.playlist_id == playlist_id
        )
    )
    if existing:
        return {
            "status": "already_subscribed",
            "playlist_id": playlist_id,
            "playlist_name": playlist.name,
            "owner_name": owner.display_name,
        }
    
    # Create subscription
    subscription = PlaylistSubscription(
        user_id=user.id,
        playlist_id=playlist_id,
    )
    db.add(subscription)
    await db.commit()
    
    return {
        "status": "subscribed",
        "playlist_id": playlist_id,
        "playlist_name": playlist.name,
        "owner_name": owner.display_name,
    }


@router.delete("/{playlist_id}/subscribe")
async def unsubscribe_from_playlist(
    playlist_id: int,
    user: TelegramUser = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """Unsubscribe from a playlist."""
    result = await db.execute(
        delete(PlaylistSubscription)
        .where(
            PlaylistSubscription.user_id == user.id,
            PlaylistSubscription.playlist_id == playlist_id
        )
    )
    
    if result.rowcount == 0:
        # Already unsubscribed / not subscribed - idempotent success
        return {"status": "not_subscribed", "playlist_id": playlist_id}
    
    await db.commit()
    
    return {"status": "unsubscribed", "playlist_id": playlist_id}


@router.get("/{playlist_id}/subscribers")
async def get_playlist_subscribers(
    playlist_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get list of users subscribed to a playlist (owner only)."""
    playlist = await db.get(Playlist, playlist_id)
    
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    if playlist.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only playlist owner can view subscribers")
    
    # Count
    total = await db.scalar(
        select(func.count(PlaylistSubscription.id))
        .where(PlaylistSubscription.playlist_id == playlist_id)
    ) or 0
    
    # Get subscribers
    offset = (page - 1) * per_page
    result = await db.execute(
        select(User)
        .join(PlaylistSubscription, PlaylistSubscription.user_id == User.id)
        .where(PlaylistSubscription.playlist_id == playlist_id)
        .order_by(PlaylistSubscription.subscribed_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    users = result.scalars().all()
    
    items = [
        {
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name,
        }
        for u in users
    ]
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }

