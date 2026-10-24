"""
TG Player - Database Models v2.0
Clean separation of concerns with proper entity types

Changes from v1:
- Album is a separate entity (not a Playlist with is_auto_album flag)
- SourceCollection for auto-playlists by forward source
- UserChannel for user's backup channel functionality
- Cleaner Track model without denormalized fields
- EnrichmentData as separate entity for external API data
"""
from datetime import datetime, timezone
from typing import Optional, List
from enum import Enum


def utcnow() -> datetime:
    """Return current UTC time as naive datetime (no tzinfo).
    
    PostgreSQL TIMESTAMP WITHOUT TIME ZONE columns + asyncpg reject
    timezone-aware Python datetimes. This helper keeps all stored
    timestamps as naive-UTC which matches the DB schema.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

from sqlalchemy import (
    BigInteger, Integer, String, Text, Boolean, 
    DateTime, ForeignKey, UniqueConstraint, Index,
    Enum as SQLEnum, JSON
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models"""
    pass


# ============== Enums ==============

class EnrichmentStatus(str, Enum):
    """Track enrichment status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class LibrarySource(str, Enum):
    """How user got the track in their library"""
    UPLOADED = "uploaded"      # User uploaded directly
    ADDED = "added"            # Added from global library
    SHARED = "shared"          # Shared by another user


class ForwardSourceType(str, Enum):
    """Type of forward source"""
    USER = "user"
    BOT = "bot"
    CHANNEL = "channel"
    SUPERGROUP = "supergroup"
    HIDDEN = "hidden"


class ChannelMessageStatus(str, Enum):
    """Status of a channel message record.
    
    Write-ahead pattern: record is created BEFORE sending to Telegram,
    then updated to SENT on success or FAILED on error.
    This ensures the DB always knows about pending operations.
    """
    PENDING = "pending"      # Record created, message not yet sent to Telegram
    SENT = "sent"            # Message successfully sent, message_id is valid
    FAILED = "failed"        # Send attempt failed, no message in channel
    DELETED = "deleted"      # Message was confirmed deleted from channel


# Helper function to create enum columns that store lowercase values
def enum_column(enum_class, **kwargs):
    """
    Create SQLAlchemy Enum column that stores lowercase string values.
    This fixes mismatch between Python enum names (PENDING) and DB values (pending).
    """
    return mapped_column(
        SQLEnum(
            enum_class,
            values_callable=lambda obj: [e.value for e in obj],
            native_enum=False,
            length=30
        ),
        **kwargs
    )


# ============== User ==============

class User(Base):
    """Telegram user"""
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user_id
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Profile customization
    photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    custom_nickname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    custom_avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Privacy settings
    hide_from_search: Mapped[bool] = mapped_column(Boolean, default=False)  # Hide from user search, keep library visible
    hide_profile: Mapped[bool] = mapped_column(Boolean, default=False)  # Hide library and albums from others
    hide_telegram_id: Mapped[bool] = mapped_column(Boolean, default=False)  # Hide Telegram username & ID from other users
    
    # Notification settings
    notify_subscription: Mapped[bool] = mapped_column(Boolean, default=True)  # Notify when subscription event occurs

    # Minecraft rank integration
    mc_rank: Mapped[str] = mapped_column(String(20), nullable=False, default="noob", server_default="noob")
    mc_max_tracks: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationships
    uploaded_tracks: Mapped[List["Track"]] = relationship(
        back_populates="uploader", 
        cascade="all, delete-orphan",
        foreign_keys="Track.uploader_id"
    )
    library_entries: Mapped[List["UserLibrary"]] = relationship(
        back_populates="user", 
        cascade="all, delete-orphan"
    )
    playlists: Mapped[List["Playlist"]] = relationship(
        back_populates="owner", 
        cascade="all, delete-orphan"
    )
    channel: Mapped[Optional["UserChannel"]] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    external_accounts: Mapped[List["UserExternalAccount"]] = relationship(
        back_populates="user", 
        cascade="all, delete-orphan"
    )
    import_files: Mapped[List["UserImportFile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    
    @property
    def display_name(self) -> str:
        """Get user's display name (prefers custom nickname if set)"""
        if self.custom_nickname and self.custom_nickname.strip():
            return self.custom_nickname.strip()
        if self.first_name:
            name = self.first_name
            if self.last_name:
                name += f" {self.last_name}"
            return name.strip()
        return self.username or f"User {self.id}"


# ============== Track (Core Entity) ==============

class Track(Base):
    """
    Audio track - core entity.
    One instance per unique file (file_unique_id is globally unique).
    """
    __tablename__ = "tracks"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Telegram file reference
    file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    mime_type: Mapped[Optional[str]] = mapped_column(String(50))
    file_name: Mapped[Optional[str]] = mapped_column(String(255))  # Original filename from Telegram
    
    # Basic metadata (from ID3 tags or user input)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    artist: Mapped[Optional[str]] = mapped_column(String(255))
    normalized_artist: Mapped[Optional[str]] = mapped_column(String(255))  # For fast SQL filtering
    duration: Mapped[Optional[int]] = mapped_column(Integer)  # seconds
    
    # Who uploaded this track
    uploader_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    
    # Visibility & Chunk / Preview status
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_unavailable: Mapped[bool] = mapped_column(Boolean, default=False)  # File deleted from Telegram
    is_chunk: Mapped[bool] = mapped_column(Boolean, default=False)  # True if 30s preview chunk, not full track
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Original external URL for full upgrade

    # Enrichment
    enrichment_status: Mapped[EnrichmentStatus] = enum_column(
        EnrichmentStatus, 
        default=EnrichmentStatus.PENDING
    )
    
    # Global statistics
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    last_played_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    # Forward source (if track was forwarded from somewhere)
    forward_source_type: Mapped[Optional[ForwardSourceType]] = enum_column(ForwardSourceType, nullable=True)
    forward_source_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    forward_source_name: Mapped[Optional[str]] = mapped_column(String(255))
    forward_source_username: Mapped[Optional[str]] = mapped_column(String(255))
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationships
    uploader: Mapped["User"] = relationship(back_populates="uploaded_tracks", foreign_keys=[uploader_id])
    enrichment: Mapped[Optional["TrackEnrichment"]] = relationship(
        back_populates="track",
        uselist=False,
        cascade="all, delete-orphan"
    )
    lyrics: Mapped[Optional["TrackLyrics"]] = relationship(
        back_populates="track",
        uselist=False,
        cascade="all, delete-orphan"
    )
    library_entries: Mapped[List["UserLibrary"]] = relationship(
        back_populates="track", 
        cascade="all, delete-orphan"
    )
    track_tags: Mapped[List["TrackTag"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan"
    )
    album_tracks: Mapped[List["AlbumTrack"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan"
    )
    playlist_tracks: Mapped[List["PlaylistTrack"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan"
    )
    channel_messages: Mapped[List["ChannelMessage"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan"
    )
    
    # ===== Properties from enrichment =====
    # These provide access to enrichment data directly from Track
    
    @property
    def display_title(self) -> str:
        """Get best available title for display.
        
        Priority:
        1. ID3 title tag (if not placeholder)
        2. Filename without extension
        3. Fallback to 'Без названия'
        """
        # Use title if it's not a placeholder
        if self.title and self.title != "Без названия":
            return self.title
        
        # Use filename without extension
        if self.file_name:
            import os
            name = os.path.splitext(self.file_name)[0]
            # Clean up common patterns
            name = name.strip()
            if name:
                return name
        
        return "Без названия"
    
    @property
    def has_metadata(self) -> bool:
        """Check if track has real metadata (not placeholders)."""
        has_title = bool(self.title and self.title != "Без названия")
        has_artist = bool(self.artist)
        return has_title or has_artist
    
    @property
    def album(self) -> Optional[str]:
        """Get album name from enrichment (Last.fm/Deezer data)"""
        try:
            return self.enrichment.album_name if self.enrichment else None
        except Exception:
            return None

    @property
    def album_name(self) -> Optional[str]:
        """Alias for album name from enrichment"""
        return self.album
    
    @property
    def cover_url(self) -> Optional[str]:
        """Get cover URL from enrichment"""
        return self.enrichment.cover_url if self.enrichment else None
    
    @property
    def genre(self) -> Optional[str]:
        """Get genre from enrichment (Deezer - broad category)"""
        return self.enrichment.genre if self.enrichment else None
    
    @property
    def tags(self) -> Optional[List[str]]:
        """Get tags from enrichment (Last.fm - detailed)"""
        return self.enrichment.tags if self.enrichment else None
    
    @property
    def release_date(self) -> Optional[str]:
        """Get release date from enrichment"""
        return self.enrichment.release_date if self.enrichment else None
    
    __table_args__ = (
        Index("idx_tracks_artist", "artist"),
        Index("idx_tracks_normalized_artist", "normalized_artist"),
        Index("idx_tracks_title", "title"),
        Index("idx_tracks_uploader", "uploader_id"),
        Index("idx_tracks_public", "is_public"),
        Index("idx_tracks_enrichment", "enrichment_status"),
    )


# ============== Track Enrichment (External API Data) ==============

class TrackEnrichment(Base):
    """
    Enrichment data from external APIs (Deezer, Last.fm, MusicBrainz).
    Separated from Track to allow easy re-enrichment and rollback.
    """
    __tablename__ = "track_enrichments"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"), unique=True)
    
    # Enriched metadata
    album_name: Mapped[Optional[str]] = mapped_column(String(255))
    genre: Mapped[Optional[str]] = mapped_column(String(100))  # Deezer genre (broad category)
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON)  # Last.fm tags (detailed, up to 5)
    cover_url: Mapped[Optional[str]] = mapped_column(String(500))
    release_date: Mapped[Optional[str]] = mapped_column(String(20))  # YYYY-MM-DD
    track_number: Mapped[Optional[int]] = mapped_column(Integer)
    
    # Source info
    deezer_track_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    deezer_album_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    lastfm_url: Mapped[Optional[str]] = mapped_column(String(500))
    musicbrainz_id: Mapped[Optional[str]] = mapped_column(String(50))
    
    # Confidence score (0-100) - how sure we are about the match
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    enriched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    # Relationship
    track: Mapped["Track"] = relationship(back_populates="enrichment")
    
    __table_args__ = (
        Index("idx_enrichment_deezer_album", "deezer_album_id"),
        Index("idx_enrichment_album_name", "album_name"),
    )


# ============== Track Lyrics ==============

class TrackLyrics(Base):
    """
    Lyrics data for a track.
    Supports synced lyrics (LRC format with timestamps) and plain text lyrics.
    """
    __tablename__ = "track_lyrics"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"), unique=True)
    
    plain_lyrics: Mapped[Optional[str]] = mapped_column(Text)
    synced_lyrics: Mapped[Optional[str]] = mapped_column(Text)  # [mm:ss.xx] LRC formatted
    is_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    is_instrumental: Mapped[bool] = mapped_column(Boolean, default=False)
    
    source: Mapped[str] = mapped_column(String(50), default="lrclib")  # lrclib, user_custom, id3
    offset_ms: Mapped[int] = mapped_column(Integer, default=0)  # timing sync offset in ms
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationship
    track: Mapped["Track"] = relationship(back_populates="lyrics")
    
    __table_args__ = (
        Index("idx_track_lyrics_track", "track_id"),
    )


# ============== Track Tags (User-Generated & Enrichment) ==============

class TagSource(str, Enum):
    """How the tag was created"""
    ENRICHMENT = "enrichment"  # Auto-imported from Last.fm
    USER = "user"              # Manually added by user


class TrackTag(Base):
    """
    A tag associated with a track.
    Tags can come from enrichment (Last.fm) or be added by users.
    Each tag accumulates votes (endorsements) from users.
    """
    __tablename__ = "track_tags"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"))
    tag: Mapped[str] = mapped_column(String(50), nullable=False)  # Normalized: lowercase, trimmed
    source: Mapped[TagSource] = enum_column(TagSource, default=TagSource.USER)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    # Relationships
    track: Mapped["Track"] = relationship(back_populates="track_tags")
    votes: Mapped[List["TrackTagVote"]] = relationship(
        back_populates="track_tag",
        cascade="all, delete-orphan"
    )
    
    __table_args__ = (
        UniqueConstraint("track_id", "tag", name="uq_track_tag"),
        Index("idx_track_tag_track", "track_id"),
        Index("idx_track_tag_name", "tag"),
    )


class TrackTagVote(Base):
    """
    User's vote (endorsement) on a track tag.
    One vote per user per tag.
    """
    __tablename__ = "track_tag_votes"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_tag_id: Mapped[int] = mapped_column(Integer, ForeignKey("track_tags.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    # Relationships
    track_tag: Mapped["TrackTag"] = relationship(back_populates="votes")
    
    __table_args__ = (
        UniqueConstraint("track_tag_id", "user_id", name="uq_track_tag_vote"),
        Index("idx_track_tag_vote_user", "user_id"),
    )


# ============== Album ==============

class Album(Base):
    """
    Album entity - represents a music album.
    Can be auto-created from enrichment or manually by user.
    Albums are global (not per-user).
    """
    __tablename__ = "albums"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Album metadata
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artist: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)  # For matching
    normalized_artist: Mapped[str] = mapped_column(String(255), nullable=False)  # For matching
    
    cover_url: Mapped[Optional[str]] = mapped_column(String(500))
    release_date: Mapped[Optional[str]] = mapped_column(String(20))  # YYYY-MM-DD
    total_tracks: Mapped[Optional[int]] = mapped_column(Integer)
    
    # Full tracklist from Deezer (JSON array)
    # Format: [{"track_number": 1, "title": "...", "artist": "...", "duration": 123, "deezer_id": 456}, ...]
    full_tracklist: Mapped[Optional[str]] = mapped_column(Text)
    
    # External IDs
    deezer_album_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationships
    tracks: Mapped[List["AlbumTrack"]] = relationship(
        back_populates="album",
        cascade="all, delete-orphan",
        order_by="AlbumTrack.track_number"
    )
    
    __table_args__ = (
        # Unique constraint on normalized name + artist
        UniqueConstraint("normalized_name", "normalized_artist", name="uq_album_name_artist"),
        Index("idx_album_artist", "normalized_artist"),
        Index("idx_album_deezer", "deezer_album_id"),
    )


class AlbumTrack(Base):
    """Association between Album and Track with ordering"""
    __tablename__ = "album_tracks"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    album_id: Mapped[int] = mapped_column(Integer, ForeignKey("albums.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"))
    
    track_number: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    # Relationships
    album: Mapped["Album"] = relationship(back_populates="tracks")
    track: Mapped["Track"] = relationship(back_populates="album_tracks")
    
    __table_args__ = (
        UniqueConstraint("album_id", "track_id", name="uq_album_track"),
        Index("idx_album_tracks_track_id", "track_id"),
    )


# ============== User Library ==============

class UserLibrary(Base):
    """
    User's personal library - links users to tracks they've added.
    Contains user-specific data (likes, play counts, etc.)
    """
    __tablename__ = "user_library"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"))
    
    # How user got this track
    source: Mapped[LibrarySource] = enum_column(LibrarySource, default=LibrarySource.UPLOADED)
    
    # User's personal data for this track
    is_liked: Mapped[bool] = mapped_column(Boolean, default=False)
    liked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_disliked: Mapped[bool] = mapped_column(Boolean, default=False)
    disliked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    last_played_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    # Timestamps
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    # Relationships
    user: Mapped["User"] = relationship(back_populates="library_entries")
    track: Mapped["Track"] = relationship(back_populates="library_entries")
    
    __table_args__ = (
        UniqueConstraint("user_id", "track_id", name="uq_user_library_track"),
        Index("idx_user_library_user", "user_id"),
        Index("idx_user_library_liked", "user_id", "is_liked"),
        Index("idx_user_library_disliked", "user_id", "is_disliked"),
    )


# ============== Playlist (User-Created Only) ==============

class Playlist(Base):
    """
    User-created playlist.
    Only for manual playlists, NOT for auto-albums or source collections.
    is_system marks the auto-created "Minecraft Media" playlist (non-deletable/renamable).
    """
    __tablename__ = "playlists"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    share_code: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    custom_cover_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="0")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("idx_playlists_owner_system", "owner_id", "is_system"),
        Index("idx_playlists_is_system", "is_system"),
    )
    
    # Relationships
    owner: Mapped["User"] = relationship(back_populates="playlists")
    tracks: Mapped[List["PlaylistTrack"]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistTrack.position"
    )


class PlaylistTrack(Base):
    """Association between Playlist and Track with ordering"""
    __tablename__ = "playlist_tracks"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"))
    
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    # Relationships
    playlist: Mapped["Playlist"] = relationship(back_populates="tracks")
    track: Mapped["Track"] = relationship(back_populates="playlist_tracks")
    
    __table_args__ = (
        UniqueConstraint("playlist_id", "track_id", name="uq_playlist_track"),
    )


# ============== Source Collection (Auto-Playlist by Forward Source) ==============

class SourceCollection(Base):
    """
    Auto-generated collection based on forward source.
    Created when user forwards tracks from a bot/channel/user.
    """
    __tablename__ = "source_collections"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    
    # Source identification
    source_type: Mapped[ForwardSourceType] = enum_column(ForwardSourceType)
    source_id: Mapped[Optional[int]] = mapped_column(BigInteger)  # Can be null for hidden sources
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_username: Mapped[Optional[str]] = mapped_column(String(255))
    
    # Display
    cover_url: Mapped[Optional[str]] = mapped_column(String(500))
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationships
    tracks: Mapped[List["SourceCollectionTrack"]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="SourceCollectionTrack.added_at"
    )
    
    __table_args__ = (
        UniqueConstraint("owner_id", "source_type", "source_id", name="uq_source_collection"),
        Index("idx_source_collection_owner", "owner_id"),
    )


class SourceCollectionTrack(Base):
    """Association between SourceCollection and Track"""
    __tablename__ = "source_collection_tracks"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("source_collections.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"))
    
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    # Relationships
    collection: Mapped["SourceCollection"] = relationship(back_populates="tracks")
    track: Mapped["Track"] = relationship()
    
    __table_args__ = (
        UniqueConstraint("collection_id", "track_id", name="uq_source_collection_track"),
    )


# ============== User Channel (Backup to Telegram Channel) ==============

class UserChannel(Base):
    """
    User's personal Telegram channel for library backup.
    All tracks added to library are forwarded here with hashtags.
    """
    __tablename__ = "user_channels"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    
    # Channel info
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_username: Mapped[Optional[str]] = mapped_column(String(255))
    channel_title: Mapped[Optional[str]] = mapped_column(String(255))
    
    # Settings
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_forward: Mapped[bool] = mapped_column(Boolean, default=True)  # Auto-forward new tracks
    include_hashtags: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationships
    user: Mapped["User"] = relationship(back_populates="channel")
    messages: Mapped[List["ChannelMessage"]] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan"
    )


class ChannelMessage(Base):
    """
    Record of a track message in user's channel.
    
    SOURCE OF TRUTH for whether a track is in the channel.
    Uses write-ahead pattern:
      1. INSERT with status=PENDING before sending to Telegram
      2. UPDATE to status=SENT + set message_id on success
      3. UPDATE to status=FAILED on error (can be retried)
    
    Invariant: a track is considered "in the channel" IFF
    a ChannelMessage exists with status=SENT and a valid message_id.
    """
    __tablename__ = "channel_messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("user_channels.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracks.id", ondelete="CASCADE"))
    
    # Status of the message (write-ahead pattern)
    status: Mapped[str] = enum_column(ChannelMessageStatus, default=ChannelMessageStatus.PENDING)
    
    # Telegram message reference (nullable: not known until successfully sent)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    
    # Hashtags stored with message
    hashtags: Mapped[Optional[str]] = mapped_column(Text)  # JSON array
    
    # Retry tracking
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationships
    channel: Mapped["UserChannel"] = relationship(back_populates="messages")
    track: Mapped["Track"] = relationship(back_populates="channel_messages")
    
    @property
    def is_in_channel(self) -> bool:
        """Whether this message is confirmed to exist in the Telegram channel."""
        return self.status == ChannelMessageStatus.SENT and self.message_id is not None
    
    __table_args__ = (
        UniqueConstraint("channel_id", "track_id", name="uq_channel_message_track"),
        Index("idx_channel_message_track", "track_id"),
        Index("idx_channel_message_status", "channel_id", "status"),
    )


# ============== Playlist Subscription (Follow Public Playlists) ==============

class PlaylistSubscription(Base):
    """
    User subscription to a public playlist.
    Allows users to add public playlists to their library.
    The playlist content auto-updates when the original owner modifies it.
    """
    __tablename__ = "playlist_subscriptions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    playlist_id: Mapped[int] = mapped_column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"))
    
    # Timestamps
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    __table_args__ = (
        UniqueConstraint("user_id", "playlist_id", name="uq_playlist_subscription"),
        Index("idx_playlist_subscription_user", "user_id"),
        Index("idx_playlist_subscription_playlist", "playlist_id"),
    )


# ============== User Following (Social) ==============

class UserFollow(Base):
    """
    User following relationship for social features.
    Allows users to follow friends and view their public libraries.
    """
    __tablename__ = "user_follows"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    follower_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    following_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    
    __table_args__ = (
        UniqueConstraint("follower_id", "following_id", name="uq_user_follow"),
        Index("idx_user_follow_follower", "follower_id"),
        Index("idx_user_follow_following", "following_id"),
    )


# ============== External Connected Accounts (SoundCloud, Spotify) ==============

class UserExternalAccount(Base):
    """
    Connected external streaming service account (SoundCloud, Spotify, etc.)
    """
    __tablename__ = "user_external_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # 'soundcloud', 'spotify'

    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    auth_token: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    likes_count: Mapped[int] = mapped_column(Integer, default=0)
    tracks_count: Mapped[int] = mapped_column(Integer, default=0)

    # Privacy configuration for public profile
    show_on_profile: Mapped[bool] = mapped_column(Boolean, default=True)
    show_playlists: Mapped[bool] = mapped_column(Boolean, default=True)
    show_tracks: Mapped[bool] = mapped_column(Boolean, default=True)

    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationship to user
    user: Mapped["User"] = relationship(back_populates="external_accounts")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_external_provider"),
        Index("idx_user_external_user", "user_id"),
    )


# ============== Saved Import Files (Exportify CSV, etc. stored in Telegram) ==============

class UserImportFile(Base):
    """
    Saved external import file (e.g. Spotify Exportify CSV) backed up in user's Telegram channel.
    Server stores only Telegram file_id & metadata, compliant with stateless storage architecture.
    """
    __tablename__ = "user_import_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), default="spotify", nullable=False)  # 'spotify'
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_id: Mapped[str] = mapped_column(String(255), nullable=False)  # Telegram document file_id
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    total_tracks: Mapped[int] = mapped_column(Integer, default=0)
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    summary_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationship to user
    user: Mapped["User"] = relationship(back_populates="import_files")

    __table_args__ = (
        Index("idx_user_import_files_user", "user_id"),
        Index("idx_user_import_files_user_provider", "user_id", "provider"),
    )

