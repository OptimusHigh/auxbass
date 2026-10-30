"""
TG Player API v2 - Tracks Router (Backwards Compatibility)

Provides /tracks endpoints for backwards compatibility with webapp.
Delegates to library, artists, albums routers where appropriate.
"""
import logging
from typing import Optional, List
from datetime import datetime, timezone
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import select, func, desc, asc, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import aiohttp
from aiogram.types import BufferedInputFile

from shared.database import get_db
from shared.models import (
    Track, TrackEnrichment, TrackLyrics, Album, AlbumTrack, User, UserLibrary,
    UserChannel, EnrichmentStatus, LibrarySource, utcnow
)
from shared.matching import normalize_artist

# NOTE: Cross-layer dependency — channel_service requires aiogram Bot.
# The API lifespan initializes the bot and channel_service.
# TODO: Extract channel forwarding logic into shared/ layer.
from bot.services.channels import get_channel_service
from bot.services.lyrics import lrclib_client
from bot.services.enrichment.cover_search import search_cover_suggestions
from api.utils.bot_helpers import get_bot as _get_bot, get_http_session
from api.routers.images import _is_safe_url
from shared.images import crop_image_to_square

from api.routers.auth import get_current_user, require_premium, get_optional_user
from api.utils.responses import track_to_response, build_track_search_filter, streamable_track_filter
from api.schemas.tracks import (
    TrackResponse,
    TracksListResponse,
    TrackUpdate,
    CoverSuggestion,
    SetCoverRequest,
    TrackLyricsResponse,
    TrackLyricsUpdate,
    TrackLyricsOffsetUpdate,
)
from api.schemas.library import LibrarySyncStateResponse
from api.schemas.common import TelegramUser
from api.utils import raise_not_found


logger = logging.getLogger(__name__)

# Image magic bytes for cover validation
_COVER_MAGIC_BYTES = {
    b'\xff\xd8\xff': 'jpg',
    b'\x89PNG': 'png',
    b'GIF87a': 'gif',
    b'GIF89a': 'gif',
    b'RIFF': 'webp',
}


router = APIRouter(tags=["Tracks"])


# ============== Track IDs (Lightweight for Shuffle) ==============

@router.get("/ids")
async def get_track_ids(
    search: Optional[str] = None,
    liked_only: bool = False,
    sort_by: str = Query("added_at", pattern="^(added_at|title|artist|duration|random)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all track IDs from user's library.
    
    Lightweight endpoint for shuffle - returns only IDs, not full track data.
    Use this to build a complete shuffle queue, then load tracks on-demand.
    
    Args:
        search: Optional search query to filter tracks
        liked_only: If True, only return liked tracks
        sort_by: Sort field. Use 'random' for pre-shuffled order
        sort_order: asc or desc
    
    Returns:
        List of track IDs in requested order
    """
    query = (
        select(Track.id)
        .join(UserLibrary, UserLibrary.track_id == Track.id)
        .where(UserLibrary.user_id == user.id)
        .where(UserLibrary.is_disliked == False)
        .where(streamable_track_filter())
    )
    
    if liked_only:
        query = query.where(UserLibrary.is_liked == True)
    
    # Search filter (indexes title, artist, file_name, user tags, and enrichment tags)
    if search:
        query = query.where(build_track_search_filter(search))
    
    # Sorting
    if sort_by == "random":
        query = query.order_by(func.random())
    else:
        if sort_by == "added_at":
            sort_column = UserLibrary.added_at
        elif sort_by == "title":
            sort_column = Track.title
        elif sort_by == "artist":
            sort_column = Track.artist
        elif sort_by == "duration":
            sort_column = Track.duration
        else:
            sort_column = UserLibrary.added_at
        
        if sort_order == "desc":
            query = query.order_by(desc(sort_column), desc(UserLibrary.id))
        else:
            query = query.order_by(asc(sort_column), asc(UserLibrary.id))
    
    result = await db.execute(query)
    ids = [row[0] for row in result.all()]
    
    return {"ids": ids, "total": len(ids)}


# ============== Artists from Tracks ==============

@router.get("/artists")
async def get_artists(
    scope: str = Query("library", pattern="^(library|global)$"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get unique artists from tracks with normalization"""
    # Get all artist names
    if scope == "library":
        query = (
            select(Track.artist)
            .join(UserLibrary, UserLibrary.track_id == Track.id)
            .where(UserLibrary.user_id == user.id)
            .where(Track.artist.isnot(None))
            .where(Track.artist != "")
        )
    else:
        query = (
            select(Track.artist)
            .join(User, User.id == Track.uploader_id)
            .where(Track.is_public == True)
            .where(User.hide_profile == False)
            .where(Track.artist.isnot(None))
            .where(Track.artist != "")
        )
    
    result = await db.execute(query)
    artists_raw = [row[0] for row in result.all()]
    
    # Group by normalized artist name
    # Key: normalized_name -> {display_name, count, display_priority}
    artist_groups = defaultdict(lambda: {"display_name": None, "count": 0, "priority": 0})
    
    for artist in artists_raw:
        normalized = normalize_artist(artist)
        if not normalized:
            continue
            
        group = artist_groups[normalized]
        group["count"] += 1
        
        # Choose best display name:
        # 1. Prefer title case (starts with uppercase)
        # 2. Prefer shorter names (without feat, etc.)
        # 3. First encountered as fallback
        is_title_case = artist[0].isupper() if artist else False
        has_collab = any(sep in artist.lower() for sep in [' & ', ' + ', ' x ', ', ', ' feat', ' ft.'])
        
        priority = 0
        if is_title_case:
            priority += 2
        if not has_collab:
            priority += 1
            
        if group["display_name"] is None or priority > group["priority"]:
            group["display_name"] = artist
            group["priority"] = priority
    
    # Convert to list and sort by count
    artists_list = [
        {
            "artist": data["display_name"],
            "name": data["display_name"],
            "track_count": data["count"],
            "normalized": normalized
        }
        for normalized, data in artist_groups.items()
    ]
    
    # Sort by track count descending
    artists_list.sort(key=lambda x: x["track_count"], reverse=True)
    
    return artists_list


@router.get("/artist-image/{artist_name}")
async def get_artist_image(
    artist_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get artist image (from album cover, matched by normalized artist name)"""
    normalized_search = normalize_artist(artist_name)
    
    # Get all albums with covers
    result = await db.execute(
        select(Album)
        .where(Album.cover_url.isnot(None))
        .where(Album.artist.isnot(None))
    )
    albums = result.scalars().all()
    
    # Find matching album by normalized artist
    for album in albums:
        if normalize_artist(album.artist) == normalized_search:
            return {"image_url": album.cover_url}
    
    # Try from track enrichment
    result = await db.execute(
        select(TrackEnrichment.cover_url, Track.artist)
        .join(Track, Track.id == TrackEnrichment.track_id)
        .where(TrackEnrichment.cover_url.isnot(None))
        .where(Track.artist.isnot(None))
    )
    
    for cover_url, track_artist in result.all():
        if normalize_artist(track_artist) == normalized_search:
            return {"image_url": cover_url}
    
    return {"image_url": None}


@router.get("/artist/{artist_name}")
async def get_artist_detail(
    artist_name: str,
    scope: str = Query("library", pattern="^(library|global)$"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get artist details with their tracks (matches by normalized artist name)"""
    # Normalize the search artist name
    normalized_search = normalize_artist(artist_name)
    
    if scope == "library":
        query = (
            select(Track, UserLibrary)
            .join(UserLibrary, UserLibrary.track_id == Track.id)
            .where(UserLibrary.user_id == user.id)
            .where(Track.artist.isnot(None))
            .options(
                selectinload(Track.enrichment),
                selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
            )
            .order_by(desc(UserLibrary.added_at))
        )
    else:
        query = (
            select(Track)
            .join(User, User.id == Track.uploader_id)
            .where(Track.is_public == True)
            .where(User.hide_profile == False)
            .where(Track.artist.isnot(None))
            .options(
                selectinload(Track.enrichment),
                selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
            )
            .order_by(desc(Track.play_count), desc(Track.created_at))
        )
    
    result = await db.execute(query)
    
    # Filter tracks by normalized artist name in Python
    if scope == "library":
        rows = result.unique().all()
        tracks = []
        for track, lib in rows:
            if normalize_artist(track.artist) == normalized_search:
                tracks.append(track_to_response(track, lib))
    else:
        rows = result.unique().scalars().all()
        tracks = []
        for track in rows:
            if normalize_artist(track.artist) == normalized_search:
                tracks.append(track_to_response(track))
    
    # Get albums for this artist (also by normalized name)
    albums_result = await db.execute(
        select(Album)
        .where(Album.artist.isnot(None))
        .order_by(desc(Album.release_date))
    )
    all_albums = albums_result.scalars().all()
    albums = [a for a in all_albums if normalize_artist(a.artist) == normalized_search]
    
    return {
        "name": artist_name,
        "track_count": len(tracks),
        "album_count": len(albums),
        "tracks": tracks,
        "albums": [
            {
                "id": a.id,
                "name": a.name,
                "cover_url": a.cover_url,
                "release_date": a.release_date,
            }
            for a in albums
        ]
    }


@router.get("/artist/{artist_name}/ids")
async def get_artist_track_ids(
    artist_name: str,
    sort_by: str = Query("added_at", pattern="^(added_at|title|duration|random)$"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all track IDs for an artist.
    
    Lightweight endpoint for shuffle by artist.
    """
    normalized_search = normalize_artist(artist_name)
    
    query = (
        select(Track.id, Track.artist, UserLibrary.added_at)
        .join(UserLibrary, UserLibrary.track_id == Track.id)
        .where(UserLibrary.user_id == user.id)
        .where(UserLibrary.is_disliked == False)
        .where(Track.artist.isnot(None))
        .where(streamable_track_filter())
    )
    
    result = await db.execute(query)
    rows = result.all()
    
    # Filter by normalized artist name
    matching_ids = [
        (row[0], row[2])  # (track_id, added_at)
        for row in rows
        if normalize_artist(row[1]) == normalized_search
    ]
    
    # Sort
    if sort_by == "random":
        import random
        random.shuffle(matching_ids)
        ids = [item[0] for item in matching_ids]
    else:
        # Sort by added_at desc
        matching_ids.sort(key=lambda x: x[1] or datetime.min, reverse=True)
        ids = [item[0] for item in matching_ids]
    
    return {"ids": ids, "total": len(ids)}


# ============== Genres ==============

@router.get("/genres")
async def get_genres(
    scope: str = Query("library", pattern="^(library|global)$"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get unique genres from tracks with representative cover_url"""
    if scope == "library":
        query = (
            select(
                TrackEnrichment.genre, 
                func.count(TrackEnrichment.id).label("count"),
                func.max(TrackEnrichment.cover_url).label("cover_url")
            )
            .join(Track, Track.id == TrackEnrichment.track_id)
            .join(UserLibrary, UserLibrary.track_id == Track.id)
            .where(UserLibrary.user_id == user.id)
            .where(TrackEnrichment.genre.isnot(None))
            .where(TrackEnrichment.genre != "")
            .group_by(TrackEnrichment.genre)
            .order_by(desc("count"))
        )
    else:
        query = (
            select(
                TrackEnrichment.genre, 
                func.count(TrackEnrichment.id).label("count"),
                func.max(TrackEnrichment.cover_url).label("cover_url")
            )
            .join(Track, Track.id == TrackEnrichment.track_id)
            .where(Track.is_public == True)
            .where(TrackEnrichment.genre.isnot(None))
            .where(TrackEnrichment.genre != "")
            .group_by(TrackEnrichment.genre)
            .order_by(desc("count"))
        )
    
    result = await db.execute(query)
    
    return [
        {"name": genre, "track_count": count, "cover_url": cover_url}
        for genre, count, cover_url in result.all()
    ]


# ============== Tags (Last.fm + User) ==============

@router.get("/tags")
async def get_tags(
    scope: str = Query("library", pattern="^(library|global)$"),
    limit: int = Query(50, ge=1, le=200),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get unique tags from tracks with counts and representative cover_url.
    
    Reads from normalized track_tags table (includes both
    enrichment tags from Last.fm and user-generated tags).
    """
    from shared.models import TrackTag, TrackTagVote
    
    if scope == "library":
        query = (
            select(
                TrackTag.tag,
                func.count(func.distinct(TrackTag.track_id)).label("track_count"),
                func.count(TrackTagVote.id).label("total_votes"),
                func.max(TrackEnrichment.cover_url).label("cover_url"),
            )
            .outerjoin(TrackTagVote, TrackTagVote.track_tag_id == TrackTag.id)
            .join(Track, Track.id == TrackTag.track_id)
            .outerjoin(TrackEnrichment, TrackEnrichment.track_id == Track.id)
            .join(UserLibrary, UserLibrary.track_id == Track.id)
            .where(UserLibrary.user_id == user.id)
            .group_by(TrackTag.tag)
            .order_by(desc("track_count"))
            .limit(limit)
        )
    else:
        query = (
            select(
                TrackTag.tag,
                func.count(func.distinct(TrackTag.track_id)).label("track_count"),
                func.count(TrackTagVote.id).label("total_votes"),
                func.max(TrackEnrichment.cover_url).label("cover_url"),
            )
            .outerjoin(TrackTagVote, TrackTagVote.track_tag_id == TrackTag.id)
            .join(Track, Track.id == TrackTag.track_id)
            .outerjoin(TrackEnrichment, TrackEnrichment.track_id == Track.id)
            .where(Track.is_public == True)
            .group_by(TrackTag.tag)
            .order_by(desc("track_count"))
            .limit(limit)
        )
    
    result = await db.execute(query)
    
    return [
        {"name": tag, "track_count": track_count, "total_votes": total_votes, "cover_url": cover_url}
        for tag, track_count, total_votes, cover_url in result.all()
    ]


# ============== Play History ==============

@router.get("/history")
async def get_play_history(
    limit: int = Query(50, ge=1, le=200),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user's recently played tracks"""
    query = (
        select(Track, UserLibrary)
        .join(UserLibrary, UserLibrary.track_id == Track.id)
        .where(UserLibrary.user_id == user.id)
        .where(UserLibrary.last_played_at.isnot(None))
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
        .order_by(desc(UserLibrary.last_played_at))
        .limit(limit)
    )
    
    result = await db.execute(query)
    rows = result.unique().all()
    
    return {
        "items": [track_to_response(track, lib) for track, lib in rows],
        "total": len(rows)
    }


# ============== Liked Tracks ==============

@router.get("/liked")
async def get_liked_tracks(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user's liked tracks"""
    query = (
        select(Track, UserLibrary)
        .join(UserLibrary, UserLibrary.track_id == Track.id)
        .where(UserLibrary.user_id == user.id)
        .where(UserLibrary.is_liked == True)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
        .order_by(desc(UserLibrary.liked_at))
    )
    
    result = await db.execute(query)
    rows = result.unique().all()
    
    return {
        "items": [track_to_response(track, lib) for track, lib in rows],
        "total": len(rows)
    }


# ============== Unavailable Tracks ==============

@router.get("/unavailable/list")
async def get_unavailable_tracks(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user's unavailable tracks"""
    query = (
        select(Track, UserLibrary)
        .join(UserLibrary, UserLibrary.track_id == Track.id)
        .where(UserLibrary.user_id == user.id)
        .where(Track.is_unavailable == True)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
    )
    
    result = await db.execute(query)
    rows = result.unique().all()
    
    return {
        "items": [track_to_response(track, lib) for track, lib in rows],
        "total": len(rows)
    }


@router.delete("/unavailable/all")
async def delete_all_unavailable(
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove all unavailable tracks from user's library and channel"""
    # Get unavailable tracks in user's library
    result = await db.execute(
        select(UserLibrary)
        .join(Track, Track.id == UserLibrary.track_id)
        .where(UserLibrary.user_id == user.id)
        .where(Track.is_unavailable == True)
    )
    entries = result.scalars().all()
    
    track_ids = [entry.track_id for entry in entries]
    count = len(entries)
    for entry in entries:
        await db.delete(entry)
    
    await db.commit()
    
    # Channel = mirror of library: delete from channel too
    if track_ids:
        try:
            channel_service = get_channel_service()
            for tid in track_ids:
                await channel_service.delete_track_from_channel(user.id, tid)
        except Exception as e:
            logger.warning(f"Failed to delete unavailable tracks from channel: {e}")
    
    return {"removed": count}


# ============== Global Library ==============

@router.get("/global")
async def get_global_tracks(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[TelegramUser] = Depends(get_optional_user),
):
    """Get public tracks from global library"""
    query = (
        select(Track)
        .where(Track.is_public == True)
        .where(Track.is_unavailable == False)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.track_tags),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
    )
    
    if search:
        search_filter = build_track_search_filter(search)
        query = query.where(search_filter)
    
    count_query = select(func.count(Track.id)).where(
        Track.is_public == True,
        Track.is_unavailable == False
    )
    if search:
        search_filter = build_track_search_filter(search)
        count_query = count_query.where(search_filter)
    
    total = await db.scalar(count_query) or 0
    
    offset = (page - 1) * per_page
    query = query.order_by(desc(Track.created_at)).offset(offset).limit(per_page)
    
    result = await db.execute(query)
    tracks = result.unique().scalars().all()
    
    # Check which tracks are in user's library
    user_track_ids = set()
    if current_user:
        lib_result = await db.execute(
            select(UserLibrary.track_id).where(UserLibrary.user_id == current_user.id)
        )
        user_track_ids = set(lib_result.scalars().all())
    
    return TracksListResponse(
        items=[track_to_response(t, in_library=(t.id in user_track_ids)) for t in tracks],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/global/recent")
async def get_recent_uploads(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get recently uploaded public tracks"""
    query = (
        select(Track)
        .where(Track.is_public == True)
        .where(Track.is_unavailable == False)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
        .order_by(desc(Track.created_at))
        .limit(limit)
    )
    
    result = await db.execute(query)
    tracks = result.unique().scalars().all()
    
    return {
        "items": [track_to_response(t) for t in tracks],
        "total": len(tracks)
    }


@router.get("/global/popular")
async def get_popular_tracks(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get most played public tracks"""
    query = (
        select(Track)
        .where(Track.is_public == True)
        .where(Track.is_unavailable == False)
        .where(Track.play_count > 0)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
        .order_by(desc(Track.play_count))
        .limit(limit)
    )
    
    result = await db.execute(query)
    tracks = result.unique().scalars().all()
    
    return {
        "items": [track_to_response(t) for t in tracks],
        "total": len(tracks)
    }


@router.get("/global/stats")
async def get_global_stats(
    db: AsyncSession = Depends(get_db),
):
    """Get global library statistics"""
    total_tracks = await db.scalar(
        select(func.count(Track.id)).where(Track.is_public == True)
    ) or 0
    
    total_users = await db.scalar(select(func.count(User.id))) or 0
    
    total_albums = await db.scalar(select(func.count(Album.id))) or 0
    
    total_plays = await db.scalar(
        select(func.sum(Track.play_count)).where(Track.is_public == True)
    ) or 0
    
    return {
        "total_tracks": total_tracks,
        "total_users": total_users,
        "total_albums": total_albums,
        "total_plays": total_plays,
    }


@router.get("/global/users")
async def get_top_users(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get top uploaders"""
    query = (
        select(User, func.count(Track.id).label("upload_count"))
        .join(Track, Track.uploader_id == User.id)
        .where(Track.is_public == True)
        .group_by(User.id)
        .order_by(desc("upload_count"))
        .limit(limit)
    )
    
    result = await db.execute(query)
    rows = result.all()
    
    return [
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "upload_count": count,
        }
        for user, count in rows
    ]


@router.get("/global/users/{user_id}/tracks")
async def get_user_tracks(
    user_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get public tracks from a specific user"""
    query = (
        select(Track)
        .where(Track.uploader_id == user_id)
        .where(Track.is_public == True)
        .where(Track.is_unavailable == False)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
        .order_by(desc(Track.created_at))
        .limit(limit)
    )
    
    result = await db.execute(query)
    tracks = result.unique().scalars().all()
    
    return {
        "items": [track_to_response(t) for t in tracks],
        "total": len(tracks)
    }


# ============== Track Operations ==============

@router.get("/{track_id}")
async def get_track(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get single track"""
    result = await db.execute(
        select(Track, UserLibrary)
        .outerjoin(
            UserLibrary,
            (UserLibrary.track_id == Track.id) & (UserLibrary.user_id == user.id)
        )
        .where(Track.id == track_id)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
    )
    row = result.unique().first()
    
    if not row:
        raise_not_found("Track not found")
    
    track, lib_entry = row
    return track_to_response(track, lib_entry)


async def _get_track_for_user(track_id: int, user_id: int, db: AsyncSession):
    """Retrieve track and verify that user is uploader OR has track in their UserLibrary."""
    result = await db.execute(
        select(Track)
        .where(Track.id == track_id)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
    )
    track = result.scalar_one_or_none()
    if not track:
        raise_not_found("Track not found")

    lib_result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.user_id == user_id, UserLibrary.track_id == track_id)
    )
    lib_entry = lib_result.scalar_one_or_none()

    if track.uploader_id != user_id and not lib_entry:
        raise HTTPException(status_code=403, detail="Track not found in your library")

    return track, lib_entry


async def _upload_cover_bytes_to_telegram(
    user_id: int,
    content: bytes,
    filename_prefix: str,
    caption: str,
    db: AsyncSession,
) -> str:
    """Upload cover bytes to user's Telegram channel or PM fallback."""
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Размер изображения превышает 10 МБ")

    if len(content) < 12:
        raise HTTPException(status_code=400, detail="Файл слишком мал для изображения")

    header = content[:12]
    detected_ext = None
    for magic, ext in _COVER_MAGIC_BYTES.items():
        if header.startswith(magic):
            if magic == b'RIFF' and header[8:12] != b'WEBP':
                continue
            detected_ext = ext
            break
    if not detected_ext:
        raise HTTPException(status_code=400, detail="Неверный формат изображения. Разрешены JPG, PNG, GIF, WebP.")

    # Crop to 1:1 square with centering and dimension normalization
    content, detected_ext = crop_image_to_square(content)

    safe_filename = f"{filename_prefix}.{detected_ext}"
    bot = _get_bot()

    user_channel = await db.scalar(
        select(UserChannel).where(UserChannel.user_id == user_id, UserChannel.is_active == True)
    )
    target_chat_id = user_channel.channel_id if user_channel else user_id

    try:
        sent_msg = await bot.send_photo(
            chat_id=target_chat_id,
            photo=BufferedInputFile(file=content, filename=safe_filename),
            caption=caption,
        )
    except Exception as e:
        logger.warning(f"Failed to upload track cover to target {target_chat_id}: {e}")
        if user_channel and target_chat_id != user_id:
            try:
                sent_msg = await bot.send_photo(
                    chat_id=user_id,
                    photo=BufferedInputFile(file=content, filename=safe_filename),
                    caption=caption,
                )
            except Exception as err:
                logger.error(f"Failed to upload track cover to Telegram PM fallback: {err}")
                raise HTTPException(status_code=502, detail="Не удалось загрузить обложку в Telegram")
        else:
            raise HTTPException(status_code=502, detail="Не удалось загрузить обложку в Telegram")

    photos = sent_msg.photo or []
    if not photos:
        raise HTTPException(status_code=502, detail="Telegram не вернул файл обложки")

    return f"/api/images/{photos[-1].file_id}"


@router.put("/{track_id}")
async def update_track(
    track_id: int,
    data: TrackUpdate,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update track metadata (uploader or any user who has the track in their library)"""
    track, lib_entry = await _get_track_for_user(track_id, user.id, db)
    
    # Validate and update metadata
    changed = False
    if data.title is not None and data.title.strip():
        new_title = data.title.strip()
        if new_title != track.title:
            track.title = new_title
            changed = True
    
    if data.artist is not None and data.artist.strip():
        new_artist = data.artist.strip()
        if new_artist != track.artist:
            track.artist = new_artist
            track.normalized_artist = normalize_artist(new_artist)
            changed = True
    
    # Update genre if provided
    if data.genre is not None:
        new_genre = data.genre.strip() or None
        if not track.enrichment:
            track.enrichment = TrackEnrichment(track_id=track.id)
            db.add(track.enrichment)
        if track.enrichment.genre != new_genre:
            track.enrichment.genre = new_genre
            changed = True

    # Update album if provided
    if data.album is not None:
        new_album = data.album.strip()
        if not track.enrichment:
            track.enrichment = TrackEnrichment(track_id=track.id)
            db.add(track.enrichment)
        
        if track.enrichment.album_name != (new_album or None):
            track.enrichment.album_name = new_album or None
            changed = True
        
        if new_album:
            try:
                from bot.services.albums import album_service
                album_id = await album_service.find_or_create_album(
                    album_name=new_album,
                    artist_name=track.artist or "Unknown Artist",
                )
                await album_service.assign_track_to_album(track.id, album_id)
                changed = True
            except Exception as e:
                logger.warning(f"Failed to assign album '{new_album}' to track {track.id}: {e}")
        else:
            await db.execute(
                delete(AlbumTrack).where(AlbumTrack.track_id == track.id)
            )
            changed = True

    # Update cover URL if provided
    if data.cover_url is not None:
        new_cover = data.cover_url.strip() or None
        if not track.enrichment:
            track.enrichment = TrackEnrichment(track_id=track.id)
            db.add(track.enrichment)
        if track.enrichment.cover_url != new_cover:
            track.enrichment.cover_url = new_cover
            changed = True
    
    if changed:
        track.updated_at = utcnow()
        # Mark as completed so background worker doesn't overwrite manual edits on restart
        track.enrichment_status = EnrichmentStatus.COMPLETED
    
    await db.commit()
    
    if changed:
        try:
            from bot.services.channels import get_channel_service
            ch_svc = get_channel_service()
            await ch_svc.update_channel_message(track_id)
        except Exception as e:
            logger.debug(f"Failed to update channel message for track {track_id}: {e}")

    # Re-fetch track with all relations to return fresh data
    track, lib_entry = await _get_track_for_user(track_id, user.id, db)
    return track_to_response(track, lib_entry)


@router.get("/{track_id}/cover-suggestions", response_model=List[CoverSuggestion])
async def get_track_cover_suggestions(
    track_id: int,
    query: Optional[str] = None,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get auto-matched high-res cover suggestions from Deezer and Apple Music."""
    track = await db.get(Track, track_id)
    if not track:
        raise_not_found("Track not found")

    search_query = query.strip() if query and query.strip() else f"{track.artist or ''} {track.title or ''}".strip()
    if not search_query:
        return []

    raw_suggestions = await search_cover_suggestions(search_query, limit_per_source=8)
    return [CoverSuggestion(**item) for item in raw_suggestions]


@router.post("/{track_id}/set-cover", response_model=TrackResponse)
async def set_track_cover(
    track_id: int,
    data: SetCoverRequest,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set track cover from external URL or existing proxy URL, uploading to Telegram storage."""
    track, lib_entry = await _get_track_for_user(track_id, user.id, db)

    cover_url = data.cover_url.strip() if data.cover_url else ""
    if not cover_url:
        raise HTTPException(status_code=400, detail="cover_url не может быть пустым")

    if cover_url.startswith("/api/images/"):
        final_cover_url = cover_url
    elif cover_url.startswith("http://") or cover_url.startswith("https://"):
        if not _is_safe_url(cover_url):
            raise HTTPException(status_code=400, detail="Недопустимый URL обложки")

        http_session = await get_http_session()
        try:
            async with http_session.get(
                cover_url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "TGPlayer/2.0"}
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail=f"Не удалось скачать обложку: статус {resp.status}")
                content = await resp.read()
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch external cover {cover_url}: {e}")
            raise HTTPException(status_code=502, detail="Ошибка загрузки изображения по ссылке")

        caption = f"🖼 <b>Обложка трека</b>: {track.artist or 'Неизвестен'} — {track.title or 'Без названия'}\n\n#track_{track.id} #cover"
        final_cover_url = await _upload_cover_bytes_to_telegram(
            user_id=user.id,
            content=content,
            filename_prefix=f"track_cover_{track.id}",
            caption=caption,
            db=db,
        )
    else:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат URL обложки")

    if not track.enrichment:
        track.enrichment = TrackEnrichment(track_id=track.id)
        db.add(track.enrichment)

    track.enrichment.cover_url = final_cover_url
    track.enrichment_status = EnrichmentStatus.COMPLETED
    track.updated_at = utcnow()

    await db.commit()

    try:
        from bot.services.channels import get_channel_service
        ch_svc = get_channel_service()
        await ch_svc.update_channel_message(track_id)
    except Exception as e:
        logger.debug(f"Failed to update channel message for track {track_id}: {e}")

    track, lib_entry = await _get_track_for_user(track_id, user.id, db)
    return track_to_response(track, lib_entry)


@router.post("/{track_id}/cover", response_model=TrackResponse)
async def upload_track_cover(
    track_id: int,
    file: UploadFile = File(...),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload custom image file as track cover, saved directly to Telegram storage."""
    track, lib_entry = await _get_track_for_user(track_id, user.id, db)

    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Разрешены только файлы изображений")

    content = await file.read()
    caption = f"🖼 <b>Обложка трека</b>: {track.artist or 'Неизвестен'} — {track.title or 'Без названия'}\n\n#track_{track.id} #cover"
    final_cover_url = await _upload_cover_bytes_to_telegram(
        user_id=user.id,
        content=content,
        filename_prefix=f"track_cover_{track.id}",
        caption=caption,
        db=db,
    )

    if not track.enrichment:
        track.enrichment = TrackEnrichment(track_id=track.id)
        db.add(track.enrichment)

    track.enrichment.cover_url = final_cover_url
    track.enrichment_status = EnrichmentStatus.COMPLETED
    track.updated_at = utcnow()

    await db.commit()

    try:
        from bot.services.channels import get_channel_service
        ch_svc = get_channel_service()
        await ch_svc.update_channel_message(track_id)
    except Exception as e:
        logger.debug(f"Failed to update channel message for track {track_id}: {e}")

    track, lib_entry = await _get_track_for_user(track_id, user.id, db)
    return track_to_response(track, lib_entry)


@router.delete("/{track_id}/cover", response_model=TrackResponse)
async def delete_track_cover(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete custom cover of a track."""
    track, lib_entry = await _get_track_for_user(track_id, user.id, db)

    if track.enrichment and track.enrichment.cover_url:
        track.enrichment.cover_url = None
        track.enrichment_status = EnrichmentStatus.COMPLETED
        track.updated_at = utcnow()
        await db.commit()

        try:
            from bot.services.channels import get_channel_service
            ch_svc = get_channel_service()
            await ch_svc.update_channel_message(track_id)
        except Exception as e:
            logger.debug(f"Failed to update channel message for track {track_id}: {e}")

    track, lib_entry = await _get_track_for_user(track_id, user.id, db)
    return track_to_response(track, lib_entry)



@router.delete("/{track_id}")
async def delete_track(
    track_id: int,
    force_purge: bool = Query(False, description="Purge track completely from global DB and all playlists"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete track from library and channel (channel = mirror of library).
    If no other user has this track, or if force_purge=True, deletes track completely from global DB.
    """
    track = await db.get(Track, track_id)
    if not track:
        raise_not_found("Track not found")

    result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id)
        .where(UserLibrary.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    
    if not entry and not force_purge and track.uploader_id != user.id:
        raise_not_found("Track not found in your library")
    
    if entry:
        await db.delete(entry)
        await db.commit()
    
    # Channel = mirror of library: delete from channel too
    deleted_from_channel = False
    try:
        channel_service = get_channel_service()
        deleted_from_channel = await channel_service.delete_track_from_channel(user.id, track_id)
    except Exception as e:
        logger.warning(f"Failed to delete track {track_id} from channel: {e}")
    
    # Check remaining users who have this track in their library
    other_users_count = await db.scalar(
        select(func.count(UserLibrary.id))
        .where(UserLibrary.track_id == track_id)
    )
    
    purged_from_global = False
    if other_users_count == 0 or force_purge:
        # No users have this track left (orphaned) OR force purge requested
        await db.delete(track)
        await db.commit()
        purged_from_global = True
        logger.info(
            f"Track {track_id} ('{track.artist} - {track.title}') purged from global tracks table "
            f"(force_purge={force_purge}, remaining_users={other_users_count})"
        )

    return {
        "status": "deleted",
        "track_id": track_id,
        "deleted_from_channel": deleted_from_channel,
        "purged_from_global": purged_from_global,
    }


@router.post("/{track_id}/re-source", response_model=TrackResponse)
async def re_source_track_audio(
    track_id: int,
    custom_url: Optional[str] = Query(None, description="Optional specific YouTube or SoundCloud URL to source audio from"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Re-download and replace audio stream for an existing track.
    Useful when a track has bad audio, live rip, or corrupted stream.
    Replaces Telegram file_id, duration, and file_size in-place without breaking playlists or likes.
    """
    query = (
        select(Track)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.lyrics),
        )
        .where(Track.id == track_id)
    )
    res = await db.execute(query)
    track = res.scalar_one_or_none()
    if not track:
        raise_not_found("Track not found")

    lib_res = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id, UserLibrary.user_id == user.id)
    )
    lib_entry = lib_res.scalar_one_or_none()
    if not lib_entry and track.uploader_id != user.id and not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="You can only re-source tracks from your library")

    import os
    import tempfile
    from aiogram.types import FSInputFile
    from bot.services.ingestion.audio_resolver import audio_resolver
    from bot.services.ingestion.base import TrackMetadata
    from bot.services.ingestion.registry import provider_registry
    from api.routers.ingestion import _get_active_bot

    with tempfile.TemporaryDirectory() as temp_dir:
        if custom_url:
            prov = provider_registry.find_provider(custom_url)
            if not prov:
                raise HTTPException(status_code=400, detail=f"No provider found for URL: {custom_url}")
            meta = TrackMetadata(
                provider_name=prov.name,
                url=custom_url,
                title=track.title or "Track",
                artist=track.artist or "Artist",
                duration=track.duration,
                cover_url=track.cover_url,
            )
            downloaded = await prov.download_track(meta, temp_dir)
        else:
            meta = TrackMetadata(
                provider_name="system",
                url="",
                title=track.title or "Track",
                artist=track.artist or "Artist",
                duration=track.duration,
                cover_url=track.cover_url,
            )
            downloaded = await audio_resolver.resolve_and_download(meta, temp_dir=temp_dir)

        bot = _get_active_bot()
        target_chat = user.id
        try:
            channel_service = get_channel_service()
            user_ch = await channel_service.get_user_channel(user.id)
            if user_ch and user_ch.is_active:
                target_chat = user_ch.channel_id
        except Exception:
            pass

        safe_filename = f"{track.artist} - {track.title}.mp3".replace("/", "-")
        audio_input = FSInputFile(downloaded.audio_path, filename=safe_filename)
        thumb_input = None
        if downloaded.cover_path and os.path.exists(downloaded.cover_path):
            thumb_input = FSInputFile(downloaded.cover_path)

        try:
            sent_msg = await bot.send_audio(
                chat_id=target_chat,
                audio=audio_input,
                title=track.title,
                performer=track.artist,
                duration=downloaded.metadata.duration if downloaded.metadata else track.duration,
                thumbnail=thumb_input,
            )
        except Exception as upload_err:
            logger.error(f"Telegram upload failed during re-source for track {track_id}: {upload_err}")
            raise HTTPException(status_code=502, detail=f"Telegram upload failed: {upload_err}")

        if not sent_msg or not sent_msg.audio:
            raise HTTPException(status_code=500, detail="Failed to upload re-sourced audio to Telegram")

        track.file_id = sent_msg.audio.file_id
        track.file_unique_id = sent_msg.audio.file_unique_id
        track.duration = sent_msg.audio.duration or track.duration
        track.file_size = sent_msg.audio.file_size or downloaded.file_size
        track.is_unavailable = False
        track.updated_at = utcnow()
        await db.commit()
        await db.refresh(track)

    logger.info(f"Successfully re-sourced audio for track {track_id} ('{track.artist} - {track.title}')")
    return track_to_response(track, lib_entry)


@router.post("/{track_id}/like")
async def like_track(
    track_id: int,
    user: TelegramUser = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """Like a track - auto-adds to library if not already there. Requires connected channel."""
    result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id)
        .where(UserLibrary.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    
    added_to_library = False
    forwarded = False
    
    if not entry:
        # Track not in library - check if track exists and is public, then auto-add
        track = await db.scalar(
            select(Track).where(Track.id == track_id, Track.is_public == True)
        )
        if not track:
            raise_not_found("Track not found")
        
        # Auto-add to library
        entry = UserLibrary(
            user_id=user.id,
            track_id=track_id,
            source=LibrarySource.ADDED,
            is_liked=True,
            liked_at=utcnow(),
            is_disliked=False,
            disliked_at=None,
        )
        db.add(entry)
        added_to_library = True
        logger.info(f"Track {track_id} auto-added to library and liked by user {user.id}")
        
        # Forward to user's channel (if auto_forward is enabled)
        await db.commit()  # Commit first so track is in library
        try:
            channel_service = get_channel_service()
            forwarded = await channel_service.forward_track_to_channel(user.id, track_id)
            if forwarded:
                logger.info(f"Track {track_id} forwarded to channel for user {user.id}")
        except Exception as e:
            logger.warning(f"Failed to forward track {track_id} to channel: {e}")
    else:
        entry.is_liked = True
        entry.liked_at = utcnow()
        entry.is_disliked = False
        entry.disliked_at = None
        await db.commit()
    
    # Pin track message in user's channel
    pinned = False
    try:
        channel_svc = get_channel_service()
        pinned = await channel_svc.pin_track_in_channel(user.id, track_id)
    except Exception as e:
        logger.warning(f"Failed to pin track {track_id} in channel: {e}")
    
    return {"status": "liked", "track_id": track_id, "added_to_library": added_to_library, "forwarded": forwarded, "pinned": pinned}


@router.delete("/{track_id}/like")
async def unlike_track(
    track_id: int,
    user: TelegramUser = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """Unlike a track. Requires connected channel."""
    result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id)
        .where(UserLibrary.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    
    if not entry:
        raise_not_found("Track not found in your library")
    
    entry.is_liked = False
    entry.liked_at = None
    await db.commit()
    
    # Unpin track message in user's channel
    try:
        channel_svc = get_channel_service()
        await channel_svc.unpin_track_in_channel(user.id, track_id)
    except Exception as e:
        logger.warning(f"Failed to unpin track {track_id} in channel: {e}")
    
    return {"status": "unliked", "track_id": track_id}


@router.post("/{track_id}/dislike")
async def dislike_track(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dislike a track.
    
    Disliked tracks are excluded from personal library, shuffle, recommendations,
    and automatic queue advance. If the track was liked, like is removed.
    """
    result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id)
        .where(UserLibrary.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    
    if not entry:
        track = await db.get(Track, track_id)
        if not track:
            raise_not_found("Track not found")
        
        entry = UserLibrary(
            user_id=user.id,
            track_id=track_id,
            source=LibrarySource.ADDED,
            is_liked=False,
            liked_at=None,
            is_disliked=True,
            disliked_at=utcnow(),
        )
        db.add(entry)
    else:
        entry.is_disliked = True
        entry.disliked_at = utcnow()
        entry.is_liked = False
        entry.liked_at = None
    
    await db.commit()
    
    return {
        "status": "disliked",
        "track_id": track_id,
        "is_disliked": True,
        "is_liked": False,
    }


@router.delete("/{track_id}/dislike")
async def undislike_track(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove dislike from a track."""
    result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id)
        .where(UserLibrary.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    
    if not entry:
        raise_not_found("Track not found in your library")
    
    entry.is_disliked = False
    entry.disliked_at = None
    await db.commit()
    
    return {
        "status": "undisliked",
        "track_id": track_id,
        "is_disliked": False,
    }


@router.post("/{track_id}/normalize-metadata", response_model=TrackResponse)
async def normalize_track_metadata(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Normalize track metadata by stripping promotional ads, bitrates, channel tags,
    and verifying against Deezer / Last.fm for canonical title and artist.
    """
    track = await db.get(Track, track_id)
    if not track:
        raise_not_found("Track not found")
        
    from shared.matching import clean_track_metadata, normalize_artist
    from bot.services.enrichment.processor import enrichment_processor
    
    # 1. Clean using regex heuristics
    clean_title, clean_artist = clean_track_metadata(track.title, track.artist, track.file_name)
    track.title = clean_title
    track.artist = clean_artist
    track.normalized_artist = normalize_artist(clean_artist)
    
    # 2. Try enrichment to find canonical titles
    try:
        res = await enrichment_processor.enrich_track(clean_title, clean_artist, track.duration)
        if res.success and res.confidence >= 65:
            if res.canonical_title:
                track.title = res.canonical_title
            if res.canonical_artist and clean_artist in ("Unknown Artist", "", "неизвестен"):
                track.artist = res.canonical_artist
                track.normalized_artist = normalize_artist(res.canonical_artist)
    except Exception as e:
        logger.warning(f"Enrichment normalization failed for track {track_id}: {e}")
        
    await db.commit()
    
    # Return updated track response
    lib_entry = await db.scalar(
        select(UserLibrary).where(UserLibrary.track_id == track_id, UserLibrary.user_id == user.id)
    )
    return track_to_response(track, lib_entry)


@router.post("/{track_id}/mark-unavailable")
async def mark_unavailable(
    track_id: int,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark track as unavailable.
    
    Any authenticated user can report a track as unavailable - this allows
    users browsing global library to report broken tracks they encounter.
    The track's file_id may have become invalid on Telegram's side.
    
    When the original uploader (or anyone) sends the file to the bot again,
    the file_id will be updated and is_unavailable will be cleared.
    """
    result = await db.execute(
        select(Track).where(Track.id == track_id)
    )
    track = result.scalar_one_or_none()
    
    if not track:
        raise_not_found("Track not found")
    
    # Do not mark tracks > 20MB as unavailable — they are valid files that cannot
    # be streamed via standard Telegram Bot API getFile, but are available for download.
    if track.file_size and track.file_size > 20 * 1024 * 1024:
        logger.info(f"Ignoring mark_unavailable for large track {track_id} ({track.file_size} bytes)")
        return {"status": "ignored_large_file", "track_id": track_id}
    
    # Try to auto-heal from channel message before declaring dead
    from api.routers.player import refresh_file_id_from_channel
    new_file_id, err_code, _ = await refresh_file_id_from_channel(track_id, db)
    if new_file_id:
        logger.info(f"mark_unavailable auto-healed track {track_id} from channel")
        return {"status": "auto_healed", "track_id": track_id}

    if err_code in ("network_error", "timeout", "flood_wait"):
        logger.warning(f"mark_unavailable: ignoring request for track {track_id} due to transient error {err_code}")
        return {"status": "ignored_transient_error", "track_id": track_id}

    # Only mark if not already unavailable
    if not track.is_unavailable:
        track.is_unavailable = True
        await db.commit()
        logger.info(f"Track {track_id} marked as unavailable by user {user.id}")
    
    return {"status": "marked_unavailable", "track_id": track_id}


@router.post("/{track_id}/add-to-library")
async def add_to_library(
    track_id: int,
    user: TelegramUser = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """Add a global track to user's library and forward to user's channel."""
    # Check track exists and is public
    result = await db.execute(
        select(Track).where(Track.id == track_id).where(Track.is_public == True)
    )
    track = result.scalar_one_or_none()
    
    if not track:
        raise_not_found("Track not found or not public")
    
    # Check if already in library
    result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id)
        .where(UserLibrary.user_id == user.id)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Track already in library")
    
    # Add to library
    entry = UserLibrary(
        user_id=user.id,
        track_id=track_id,
        source=LibrarySource.ADDED,
    )
    db.add(entry)
    await db.commit()
    
    # Forward to user's channel (if auto_forward is enabled) - uses queue
    queued = False
    try:
        channel_service = get_channel_service()
        queued = await channel_service.forward_track_to_channel(user.id, track_id)
        if queued:
            logger.info(f"Track {track_id} queued for channel forward for user {user.id}")
    except Exception as e:
        logger.warning(f"Failed to queue track {track_id} for channel: {e}")
    
    return {"status": "added", "track_id": track_id, "queued_for_channel": queued}


@router.delete("/{track_id}/remove-from-library")
async def remove_from_library(
    track_id: int,
    user: TelegramUser = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """Remove a track from user's library and channel (channel = mirror of library).
    If no other users have this track left in their library, delete track completely from global DB.
    """
    track = await db.get(Track, track_id)
    if not track:
        raise_not_found("Track not found")

    result = await db.execute(
        select(UserLibrary)
        .where(UserLibrary.track_id == track_id)
        .where(UserLibrary.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    
    if not entry:
        raise_not_found("Track not found in your library")
    
    await db.delete(entry)
    await db.commit()
    
    # Channel = mirror of library: always delete from channel
    deleted_from_channel = False
    try:
        channel_service = get_channel_service()
        deleted_from_channel = await channel_service.delete_track_from_channel(user.id, track_id)
    except Exception as e:
        logger.warning(f"Failed to delete track {track_id} from channel: {e}")
    
    # Check remaining users who have this track in their library
    other_users_count = await db.scalar(
        select(func.count(UserLibrary.id))
        .where(UserLibrary.track_id == track_id)
    )
    
    purged_from_global = False
    if other_users_count == 0:
        # No users have this track left (orphaned) -> delete track entirely to free the slot
        await db.delete(track)
        await db.commit()
        purged_from_global = True
        logger.info(
            f"Track {track_id} ('{track.artist} - {track.title}') purged from global tracks table "
            f"(last listener removed it from library)"
        )
    
    return {
        "status": "removed",
        "track_id": track_id,
        "deleted_from_channel": deleted_from_channel,
        "purged_from_global": purged_from_global,
    }


# ============== Get all tracks (alias for backwards compat) ==============

@router.get("")
async def get_all_tracks(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    page: Optional[int] = Query(None, ge=1),
    per_page: Optional[int] = Query(None, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: str = Query("added_at", pattern="^(added_at|title|artist|duration)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user's tracks (alias for /library)"""
    effective_limit = per_page or limit
    effective_offset = (page - 1) * effective_limit if page is not None else offset
    effective_page = page or ((effective_offset // effective_limit) + 1)

    query = (
        select(Track, UserLibrary)
        .join(UserLibrary, UserLibrary.track_id == Track.id)
        .where(UserLibrary.user_id == user.id)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.track_tags),
            selectinload(Track.album_tracks).selectinload(AlbumTrack.album),
        )
    )
    
    if search:
        search_filter = build_track_search_filter(search)
        query = query.where(search_filter)
    
    # Count
    count_query = (
        select(func.count(UserLibrary.id))
        .where(UserLibrary.user_id == user.id)
    )
    if search:
        search_filter = build_track_search_filter(search)
        count_query = count_query.join(Track, Track.id == UserLibrary.track_id).where(search_filter)
    
    total = await db.scalar(count_query) or 0
    
    # Sorting
    order_col = {
        "added_at": UserLibrary.added_at,
        "title": Track.title,
        "artist": Track.artist,
        "duration": Track.duration,
    }.get(sort_by, UserLibrary.added_at)
    
    if sort_order == "desc":
        query = query.order_by(desc(order_col), desc(UserLibrary.id))
    else:
        query = query.order_by(asc(order_col), asc(UserLibrary.id))
    
    # Pagination with offset/limit
    query = query.offset(effective_offset).limit(effective_limit)
    
    result = await db.execute(query)
    rows = result.unique().all()
    
    return TracksListResponse(
        items=[track_to_response(track, lib) for track, lib in rows],
        total=total,
        offset=effective_offset,
        limit=effective_limit,
        page=effective_page,
        per_page=effective_limit,
    )


@router.get("/sync-state", response_model=LibrarySyncStateResponse)
async def get_tracks_sync_state(
    since: Optional[datetime] = Query(None, description="ISO timestamp of last sync"),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Alias for /api/library/sync-state under /api/tracks/sync-state"""
    from api.routers.library import get_library_sync_state
    return await get_library_sync_state(since=since, user=user, db=db)


# ============== Lyrics Endpoints ==============

@router.get("/{track_id}/lyrics", response_model=TrackLyricsResponse)
async def get_track_lyrics(
    track_id: int,
    force_refresh: bool = Query(False),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get lyrics for a track (on-demand).
    
    Checks DB cache first; if not present or force_refresh=True,
    queries LRCLIB API, saves to DB and returns result.
    """
    result = await db.execute(
        select(Track)
        .options(
            selectinload(Track.enrichment),
            selectinload(Track.lyrics),
        )
        .where(Track.id == track_id)
    )
    track = result.scalar_one_or_none()
    if not track:
        raise_not_found("Track not found")
    
    # If cached and not forcing refresh, return cached
    if track.lyrics and not force_refresh:
        return track.lyrics
    
    # Fetch from LRCLIB
    lyrics_data = await lrclib_client.get_lyrics(
        title=track.display_title,
        artist=track.artist,
        album=track.album,
        duration=track.duration,
    )
    
    if lyrics_data:
        if track.lyrics:
            track.lyrics.plain_lyrics = lyrics_data.get("plain_lyrics")
            track.lyrics.synced_lyrics = lyrics_data.get("synced_lyrics")
            track.lyrics.is_synced = lyrics_data.get("is_synced", False)
            track.lyrics.is_instrumental = lyrics_data.get("is_instrumental", False)
            track.lyrics.source = lyrics_data.get("source", "lrclib")
            track.lyrics.updated_at = utcnow()
        else:
            track.lyrics = TrackLyrics(
                track_id=track.id,
                plain_lyrics=lyrics_data.get("plain_lyrics"),
                synced_lyrics=lyrics_data.get("synced_lyrics"),
                is_synced=lyrics_data.get("is_synced", False),
                is_instrumental=lyrics_data.get("is_instrumental", False),
                source=lyrics_data.get("source", "lrclib"),
                offset_ms=0,
            )
            db.add(track.lyrics)
        
        await db.commit()
        await db.refresh(track.lyrics)
        return track.lyrics
    
    # If nothing found online, return existing DB record if any, or empty lyrics response
    if track.lyrics:
        return track.lyrics
    
    return TrackLyricsResponse(
        track_id=track.id,
        plain_lyrics=None,
        synced_lyrics=None,
        is_synced=False,
        is_instrumental=False,
        source=None,
        offset_ms=0,
        updated_at=None,
    )


@router.put("/{track_id}/lyrics", response_model=TrackLyricsResponse)
async def update_track_lyrics(
    track_id: int,
    update_data: TrackLyricsUpdate,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save custom or edited lyrics for a track."""
    result = await db.execute(
        select(Track).options(selectinload(Track.lyrics)).where(Track.id == track_id)
    )
    track = result.scalar_one_or_none()
    if not track:
        raise_not_found("Track not found")
    
    synced = update_data.synced_lyrics
    plain = update_data.plain_lyrics
    is_synced = bool(synced and len(synced.strip()) > 0)
    is_inst = update_data.is_instrumental if update_data.is_instrumental is not None else False
    offset = update_data.offset_ms if update_data.offset_ms is not None else 0
    
    if track.lyrics:
        if update_data.plain_lyrics is not None:
            track.lyrics.plain_lyrics = plain
        if update_data.synced_lyrics is not None:
            track.lyrics.synced_lyrics = synced
            track.lyrics.is_synced = is_synced
        if update_data.is_instrumental is not None:
            track.lyrics.is_instrumental = is_inst
        if update_data.offset_ms is not None:
            track.lyrics.offset_ms = offset
        track.lyrics.source = "user_custom"
        track.lyrics.updated_at = utcnow()
    else:
        track.lyrics = TrackLyrics(
            track_id=track.id,
            plain_lyrics=plain,
            synced_lyrics=synced,
            is_synced=is_synced,
            is_instrumental=is_inst,
            source="user_custom",
            offset_ms=offset,
        )
        db.add(track.lyrics)
    
    await db.commit()
    await db.refresh(track.lyrics)
    return track.lyrics


@router.post("/{track_id}/lyrics/offset", response_model=TrackLyricsResponse)
async def update_lyrics_offset(
    track_id: int,
    offset_data: TrackLyricsOffsetUpdate,
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update timing offset in ms for synced lyrics."""
    result = await db.execute(
        select(Track).options(selectinload(Track.lyrics)).where(Track.id == track_id)
    )
    track = result.scalar_one_or_none()
    if not track:
        raise_not_found("Track not found")
    
    if not track.lyrics:
        track.lyrics = TrackLyrics(
            track_id=track.id,
            offset_ms=offset_data.offset_ms,
            source="user_custom",
        )
        db.add(track.lyrics)
    else:
        track.lyrics.offset_ms = offset_data.offset_ms
        track.lyrics.updated_at = utcnow()
    
    await db.commit()
    await db.refresh(track.lyrics)
    return track.lyrics


@router.post("/{track_id}/lyrics/search")
async def search_lyrics(
    track_id: int,
    query: str = Query(..., min_length=1),
    user: TelegramUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search LRCLIB for lyrics matching custom query."""
    results = await lrclib_client.search(query)
    return results

