"""
TG Player API - Response Helpers & Track Utilities

Shared utility functions used across multiple routers.
Extracted from library.py to eliminate cross-router dependencies.
"""
from typing import Optional, List

from sqlalchemy import and_, or_, func, String, select

from shared.models import Track, TrackEnrichment, TrackTag, UserLibrary, Album, AlbumTrack
from api.schemas.tracks import TrackResponse
from api.schemas.albums import AlbumResponse


# ============== Streaming Format Constants ==============

# MIME types that can be streamed in web player (standard audio player compatible)
STREAMABLE_MIME_TYPES = {
    "audio/mpeg",      # MP3
    "audio/mp3",       # MP3 (alternative)
    "audio/ogg",       # OGG
    "audio/aac",       # AAC
    "audio/mp4",       # M4A
    "audio/x-m4a",     # M4A (alternative)
}

# HD/Lossless MIME types that cannot be streamed (file too large or format unsupported)
HD_MIME_TYPES = {
    "audio/flac",      # FLAC
    "audio/x-flac",    # FLAC (alternative)
    "audio/wav",       # WAV
    "audio/x-wav",     # WAV (alternative)
    "audio/aiff",      # AIFF
    "audio/x-aiff",    # AIFF (alternative)
}

def get_max_streamable_size() -> int:
    from shared.config import get_settings
    s = get_settings()
    if s.telegram_api_url and not s.telegram_api_url.startswith("https://api.telegram.org"):
        return 2000 * 1024 * 1024
    return 20 * 1024 * 1024

MAX_STREAMABLE_SIZE_BYTES = 2000 * 1024 * 1024


# ============== Streaming Format Helpers ==============

def is_streamable(mime_type: Optional[str], file_size: Optional[int] = None) -> bool:
    """Check if MIME type and file size are supported for direct streaming in web browser."""
    if file_size is not None and file_size > get_max_streamable_size():
        return False
    if not mime_type:
        return True  # Assume streamable if mime_type unknown (legacy tracks)
    return mime_type.lower() in STREAMABLE_MIME_TYPES


def is_hd_format(mime_type: Optional[str]) -> bool:
    """Check if track is HD/lossless format"""
    if not mime_type:
        return False
    return mime_type.lower() in HD_MIME_TYPES


def streamable_track_filter():
    """SQLAlchemy filter expression for tracks that are directly streamable via Telegram Bot API."""
    hd_list = list(HD_MIME_TYPES)
    return and_(
        Track.is_unavailable == False,
        or_(
            Track.file_size.is_(None),
            Track.file_size <= MAX_STREAMABLE_SIZE_BYTES,
        ),
        or_(
            Track.mime_type.is_(None),
            Track.mime_type.notin_(hd_list),
        ),
    )


# ============== Track Response Builder ==============

def track_to_response(track: Track, library_entry: Optional[UserLibrary] = None, *, in_library: Optional[bool] = None) -> TrackResponse:
    """Convert Track model to response. Works for both library and global contexts.
    
    Args:
        track: The Track model
        library_entry: Optional UserLibrary entry (for user's library tracks)
        in_library: Override for in_library flag. Auto-detected from library_entry if None.
    """
    enrichment = track.__dict__.get('enrichment')
    
    # Get first album if any (only if already loaded)
    album_info = None
    album_tracks = track.__dict__.get('album_tracks')
    if album_tracks:
        first_album_track = album_tracks[0]
        album = first_album_track.__dict__.get('album') if first_album_track else None
        if album:
            album_info = {
                "id": album.id,
                "name": album.name,
                "artist": album.artist,
                "cover_url": album.cover_url,
            }
    
    # Extract library context (defaults for global tracks)
    source = None
    added_at = track.created_at
    is_liked = False
    liked_at = None
    is_disliked = False
    disliked_at = None
    play_count = 0
    if library_entry:
        source = library_entry.source.value if library_entry.source else None
        added_at = library_entry.added_at
        is_liked = library_entry.is_liked or False
        liked_at = library_entry.liked_at
        is_disliked = getattr(library_entry, 'is_disliked', False) or False
        disliked_at = getattr(library_entry, 'disliked_at', None)
        play_count = library_entry.play_count or 0
    
    track_is_streamable = is_streamable(track.mime_type, track.file_size) and not (track.is_unavailable or False)
    
    # Auto-detect in_library from library_entry if not explicitly set
    if in_library is None:
        in_library = library_entry is not None
    
    # Combine enrichment tags and user track_tags
    tags = None
    if enrichment and enrichment.tags:
        tags = list(enrichment.tags)
    
    track_tags = track.__dict__.get('track_tags')
    if track_tags:
        if tags is None:
            tags = []
        for tt in track_tags:
            if tt.tag and tt.tag not in tags:
                tags.append(tt.tag)
    
    return TrackResponse(
        id=track.id,
        telegram_file_id=track.file_id,
        title=track.title,
        artist=track.artist,
        file_name=track.file_name,
        duration=track.duration,
        file_size=track.file_size,
        mime_type=track.mime_type,
        library_source=source,
        is_streamable=track_is_streamable,
        is_unavailable=track.is_unavailable or False,
        is_chunk=bool(getattr(track, 'is_chunk', False)),
        source_url=getattr(track, 'source_url', None),
        streamable_id=None,
        hd_id=None,
        album=album_info,
        album_name=album_info["name"] if album_info else (enrichment.album_name if enrichment else None),
        cover_url=enrichment.cover_url if enrichment else None,
        genre=enrichment.genre if enrichment else None,
        tags=tags,
        release_date=enrichment.release_date if enrichment else None,
        is_liked=is_liked,
        liked_at=liked_at,
        is_disliked=is_disliked,
        disliked_at=disliked_at,
        play_count=play_count,
        added_at=added_at,
        in_library=in_library,
    )


# ============== Search Filter ==============

def build_track_search_filter(search: str):
    """
    Build SQLAlchemy search filter for tracks.
    Matches:
    - Track title
    - Track artist
    - Track file_name
    - Associated custom user tags (TrackTag)
    - Associated enrichment genre
    - Associated enrichment tags (Last.fm tags)
    """
    if not search:
        return None
        
    search_clean = search.lstrip('#').strip()
    if not search_clean:
        search_clean = search.strip()
    search_term = f"%{search_clean}%"
    
    tag_match = (
        select(TrackTag.id)
        .where(
            TrackTag.track_id == Track.id,
            TrackTag.tag.ilike(search_term)
        )
        .exists()
    )
    
    enrichment_match = (
        select(TrackEnrichment.id)
        .where(
            TrackEnrichment.track_id == Track.id,
            or_(
                TrackEnrichment.genre.ilike(search_term),
                func.cast(TrackEnrichment.tags, String).ilike(search_term),
            )
        )
        .exists()
    )
    
    return or_(
        Track.title.ilike(search_term),
        Track.artist.ilike(search_term),
        Track.file_name.ilike(search_term),
        tag_match,
        enrichment_match,
    )


# ============== Album Response Builder ==============

def album_to_response(album: Album, track_count: Optional[int] = None, tags: Optional[List[str]] = None) -> AlbumResponse:
    """Convert Album model to response"""
    # Get actual track count if not provided
    actual_count = track_count if track_count is not None else len(album.tracks) if album.tracks else 0
    
    return AlbumResponse(
        id=album.id,
        name=album.name,
        artist=album.artist,
        cover_url=album.cover_url,
        release_date=album.release_date,
        track_count=actual_count,
        total_tracks=album.total_tracks,
        deezer_album_id=album.deezer_album_id,
        has_full_tracklist=bool(album.full_tracklist),
        tags=tags,
    )
