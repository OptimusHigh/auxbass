"""
TG Player - Shared Utilities

This module re-exports functions from matching.py for backward compatibility
and provides formatting utilities.
"""
import re
from typing import Optional, List, Any

# Re-export from matching module for backward compatibility
from .matching import (
    # Normalization
    normalize_unicode,
    normalize_artist,
    normalize_title,
    normalize_album,
    normalize_genre,
    clean_for_search,
    remove_parenthetical,
    remove_featuring,
    
    # Matching
    fuzzy_match_title,
    fuzzy_match_artist,
    artists_match,
    titles_match,
    jaccard_similarity,
    extract_version_info,
    versions_conflict,

    
    # Sanitization
    sanitize_input,
    sanitize_search_query,
    
    # Hashtags
    generate_hashtags,
    format_hashtags,
    
    # Constants
    ARTIST_MATCH_THRESHOLD,
    TITLE_MATCH_THRESHOLD,
    ALBUM_MATCH_THRESHOLD,
    GENRE_MAPPINGS,
)


# ============== Formatting ==============

def format_duration(seconds: Optional[int]) -> str:
    """Format duration in seconds to MM:SS or HH:MM:SS"""
    if not seconds:
        return "0:00"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_duration_long(seconds: Optional[int]) -> str:
    """Format duration in human-readable form (e.g., '1 ч 23 мин')"""
    if not seconds:
        return "0 мин"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def format_file_size(bytes_size: Optional[int]) -> str:
    """Format file size in human-readable form"""
    if not bytes_size:
        return "0 B"
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    
    return f"{bytes_size:.1f} TB"


def format_track_info(
    title: Optional[str] = None,
    artist: Optional[str] = None,
    duration: Optional[int] = None,
    include_duration: bool = True
) -> str:
    """
    Format track info for display.
    
    Examples:
        "Track Name - Artist (3:45)"
        "Track Name - Unknown Artist"
    """
    parts = []
    
    title_str = title or "Без названия"
    artist_str = artist or "Неизвестный исполнитель"
    
    parts.append(f"{title_str} — {artist_str}")
    
    if include_duration and duration:
        parts.append(f"({format_duration(duration)})")
    
    return " ".join(parts)


# ============== Artist Splitting ==============

# Regex pattern to split multiple artists
ARTIST_SPLIT_PATTERN = re.compile(
    r'\s*[,&+]\s*'  # Symbols: comma, ampersand, plus
    r'|'
    r'\s+(?:feat\.?|ft\.?|featuring)\s+'  # Features
    r'|'
    r'\s+(?:x|vs\.?)\s+'  # Collaborations
    r'|'
    r'\s+(?:and|with)\s+'  # Conjunctions
    r'|'
    r'\s+(?:prod\.?|produced\s+by)\s+'  # Producers
    , flags=re.IGNORECASE
)


def split_artists(artist_string: str) -> List[str]:
    """
    Split a string containing multiple artists into a list.
    Handles: commas, ampersands, feat., ft., x, vs., and, with, prod.
    
    Example: "Artist1, Artist2 & Artist3 feat. Artist4" 
             -> ["Artist1", "Artist2", "Artist3", "Artist4"]
    """
    if not artist_string:
        return []
    
    artists = ARTIST_SPLIT_PATTERN.split(artist_string)
    return [a.strip() for a in artists if a.strip()]


def get_primary_artist(artist_string: str) -> str:
    """
    Get the primary (first) artist from a collaboration string.
    
    Example: "Drake, Future" -> "Drake"
    """
    artists = split_artists(artist_string)
    return artists[0] if artists else artist_string or ""


# ============== Telegram Helpers ==============

def escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram messages."""
    if not text:
        return ""
    return (
        text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate text to max length, adding suffix if truncated."""
    if not text or len(text) <= max_length:
        return text or ""
    
    return text[:max_length - len(suffix)] + suffix


# ============== Streaming Format Constants & Helpers ==============

STREAMABLE_MIME_TYPES = {
    "audio/mpeg",      # MP3
    "audio/mp3",       # MP3 (alternative)
    "audio/ogg",       # OGG
    "audio/aac",       # AAC
    "audio/mp4",       # M4A
    "audio/x-m4a",     # M4A (alternative)
}

HD_MIME_TYPES = {
    "audio/flac",      # FLAC
    "audio/x-flac",    # FLAC (alternative)
    "audio/wav",       # WAV
    "audio/x-wav",     # WAV (alternative)
    "audio/aiff",      # AIFF
    "audio/x-aiff",    # AIFF (alternative)
    "audio/alac",      # ALAC
    "audio/x-alac",    # ALAC (alternative)
}

def get_max_streamable_size() -> int:
    """Return max streamable file size in bytes based on Telegram Bot API mode (20MB cloud, 2GB local)."""
    try:
        from shared.config import get_settings
        s = get_settings()
        if s.telegram_api_url and not s.telegram_api_url.startswith("https://api.telegram.org"):
            return 2000 * 1024 * 1024
    except Exception:
        pass
    return 20 * 1024 * 1024

def is_hd_format(mime_type: Optional[str]) -> bool:
    """Check if track MIME type is HD/lossless format (FLAC, WAV, AIFF, ALAC)."""
    if not mime_type:
        return False
    return mime_type.lower() in HD_MIME_TYPES

def is_streamable_format(mime_type: Optional[str], file_size: Optional[int] = None) -> bool:
    """Check if MIME type and file size are supported for direct streaming in web browser."""
    max_size = get_max_streamable_size()
    if file_size is not None and file_size > max_size:
        return False
    if not mime_type:
        return True  # Assume streamable if mime_type unknown (legacy tracks)
    return mime_type.lower() in STREAMABLE_MIME_TYPES

def is_track_streamable(track: Any) -> bool:
    """Check if Track object is directly streamable in web browser."""
    if not track:
        return False
    if getattr(track, "is_unavailable", False):
        return False
    mime = getattr(track, "mime_type", None)
    if is_hd_format(mime):
        return False
    file_size = getattr(track, "file_size", None)
    if file_size and file_size > get_max_streamable_size():
        return False
    if mime and mime.lower() not in STREAMABLE_MIME_TYPES:
        return False
    return True

