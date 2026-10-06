"""
TG Player - Track Service v2

Unified service for track management with new model structure:
- Track is global (one per file_unique_id)
- UserLibrary connects users to tracks
- Albums via AlbumTrack association
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass

from sqlalchemy import select, func, and_, or_, desc, delete
from sqlalchemy.orm import selectinload

from shared.database import get_session
from shared.models import (
    Track, TrackEnrichment, Album, AlbumTrack, User, UserLibrary,
    EnrichmentStatus, LibrarySource, ForwardSourceType, TagSource, TrackTag, utcnow
)
from shared.matching import normalize_artist, clean_track_metadata, sanitize_album_name

from ..enrichment import enrichment_worker, enrichment_processor
from ..albums import album_service

logger = logging.getLogger(__name__)


def sanitize_artist(artist: Optional[str]) -> Optional[str]:
    """Sanitize artist name to prevent URL routing issues.
    
    Removes forward slashes that would break REST API paths like /api/artists/{name}
    """
    if not artist:
        return artist
    
    # Replace forward slashes with space/comma/and depending on context
    # "Ecco2k/Bladee" -> "Ecco2k & Bladee"
    artist = artist.replace('/', ' & ')
    
    # Clean up multiple spaces
    artist = ' '.join(artist.split())
    
    return artist.strip()


@dataclass
class SaveTrackResult:
    """Result of saving a track"""
    track_id: int
    is_new: bool  # True if track was newly created (not just added to library)
    was_in_library: bool = False  # True if already in user's library
    album_id: Optional[int] = None


@dataclass
class TrackSearchResult:
    """Result of track search"""
    tracks: List[Track]
    total: int
    has_more: bool


class TrackService:
    """Unified service for track operations"""
    
    def __init__(self):
        self._setup_enrichment_callback()
    
    def _setup_enrichment_callback(self):
        """Connect enrichment completion to album assignment and channel update"""
        async def on_enrichment_complete(track_id: int, result):
            if result.success:
                async with get_session() as session:
                    track = await session.get(Track, track_id)
                    forward_src = track.forward_source_name if track else None

                # Auto-assign album if found, but NOT for SoundCloud singles/remixes
                if result.album_name and forward_src != "soundcloud":
                    try:
                        await album_service.auto_assign_album_from_enrichment(track_id)
                    except Exception as e:
                        logger.error(f"Auto album assignment failed for track {track_id}: {e}")
                
                # Update channel messages with new metadata
                try:
                    from bot.services.channels import channel_service
                    await channel_service.update_channel_message(track_id)
                except Exception as e:
                    logger.error(f"Channel message update failed for track {track_id}: {e}")
        
        enrichment_worker.set_on_enrichment_complete(on_enrichment_complete)
    
    async def save_track(
        self,
        user_id: int,
        file_id: str,
        file_unique_id: str,
        title: Optional[str] = None,
        artist: Optional[str] = None,
        duration: Optional[int] = None,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        file_name: Optional[str] = None,
        library_source: LibrarySource = LibrarySource.UPLOADED,
        forward_source_type: Optional[ForwardSourceType] = None,
        forward_source_id: Optional[int] = None,
        forward_source_name: Optional[str] = None,
        forward_source_username: Optional[str] = None,
        enrich: bool = True,
        add_to_library: bool = True,
        cover_url: Optional[str] = None,
        genre: Optional[str] = None,
        tags: Optional[List[str]] = None,
        album_name: Optional[str] = None,
        source_provider: Optional[str] = None,
        is_chunk: bool = False,
        source_url: Optional[str] = None,
    ) -> SaveTrackResult:
        """
        Save a new track or add existing one to user's library.
        
        Track is global - if file_unique_id exists, we add it to user's library.
        If track is new, schedules enrichment.
        Supports 30s preview chunks and seamless upgrade to full tracks.
        
        Args:
            user_id: Telegram user ID
            file_id: Telegram file ID
            file_unique_id: Unique file identifier
            title: Track title (from ID3 tags)
            artist: Artist name
            duration: Duration in seconds
            file_size: File size in bytes
            mime_type: MIME type
            file_name: Original filename from Telegram
            library_source: How track was added
            forward_source_*: Forwarding source info
            enrich: Whether to schedule enrichment
            is_chunk: True if 30s preview chunk
            source_url: Original streaming URL for future upgrade
            
        Returns:
            SaveTrackResult with track_id, is_new, and was_in_library flags
        """
        album_name = sanitize_album_name(album_name)

        async with get_session() as session:
            # Check for existing track by file_unique_id (globally unique)
            result = await session.execute(
                select(Track).where(Track.file_unique_id == file_unique_id)
            )
            existing_track = result.scalar_one_or_none()
            
            is_new = False
            
            mime_type_normalized = mime_type.lower() if mime_type else None

            if existing_track:
                track = existing_track
                # Seamless upgrade: if existing track was a preview chunk and full track arrived
                if track.is_chunk and not is_chunk:
                    track.file_id = file_id
                    track.file_unique_id = file_unique_id
                    track.duration = duration
                    track.file_size = file_size
                    if mime_type_normalized:
                        track.mime_type = mime_type_normalized
                    if file_name:
                        track.file_name = file_name
                    track.is_chunk = False
                    track.is_unavailable = False
                    logger.info(f"Upgraded chunk track {track.id} to full track: {title} - {artist}")
                else:
                    # Update file_id if needed (can change when user re-sends file)
                    if track.file_id != file_id:
                        track.file_id = file_id
                        logger.info(f"Updated file_id for track {track.id}: {title} - {artist}")

                    # Backfill mime_type if we now know it
                    if mime_type_normalized and track.mime_type != mime_type_normalized:
                        track.mime_type = mime_type_normalized
                        logger.info(f"Updated mime_type for track {track.id} to {mime_type_normalized}")
                    
                    # Clear is_unavailable flag if it was set
                    if track.is_unavailable:
                        track.is_unavailable = False
                        logger.info(f"Track {track.id} is now available again (file re-uploaded)")

                if source_url and not track.source_url:
                    track.source_url = source_url

                # If track enrichment exists or provider metadata supplied, backfill missing fields
                if cover_url or genre or tags or album_name:
                    existing_enrichment = await session.scalar(
                        select(TrackEnrichment).where(TrackEnrichment.track_id == track.id)
                    )
                    if existing_enrichment:
                        if cover_url and not existing_enrichment.cover_url:
                            existing_enrichment.cover_url = cover_url
                        if genre and not existing_enrichment.genre:
                            existing_enrichment.genre = genre
                        if tags and not existing_enrichment.tags:
                            existing_enrichment.tags = tags
                        if album_name and not existing_enrichment.album_name:
                            existing_enrichment.album_name = album_name
                    else:
                        enrichment = TrackEnrichment(
                            track_id=track.id,
                            cover_url=cover_url,
                            genre=genre,
                            tags=tags,
                            album_name=album_name,
                            confidence=85 if source_provider in ("soundcloud", "spotify") else 50,
                        )
                        session.add(enrichment)
            else:
                # Create new track
                is_new = True
                
                # Ensure user exists
                user = await session.get(User, user_id)
                if not user:
                    user = User(id=user_id)
                    session.add(user)
                    await session.flush()
                
                # Clean promotional junk and format from title & artist
                clean_title, clean_artist = clean_track_metadata(title, artist, file_name)
                title = clean_title
                artist = clean_artist
                
                # Sanitize artist name to prevent URL issues
                sanitized_artist = sanitize_artist(artist)
                
                effective_source_name = forward_source_name or source_provider

                track = Track(
                    file_id=file_id,
                    file_unique_id=file_unique_id,
                    title=title,
                    artist=sanitized_artist,
                    normalized_artist=normalize_artist(sanitized_artist) if sanitized_artist else None,
                    duration=duration,
                    file_size=file_size,
                    mime_type=mime_type_normalized,
                    file_name=file_name,
                    uploader_id=user_id,
                    forward_source_type=forward_source_type,
                    forward_source_id=forward_source_id,
                    forward_source_name=effective_source_name,
                    forward_source_username=forward_source_username,
                    is_chunk=is_chunk,
                    source_url=source_url,
                    enrichment_status=EnrichmentStatus.PENDING if (enrich and not is_chunk) else EnrichmentStatus.COMPLETED,
                )
                session.add(track)
                await session.flush()

                # Immediately register provider metadata in enrichment
                if cover_url or genre or tags or album_name:
                    enrichment = TrackEnrichment(
                        track_id=track.id,
                        cover_url=cover_url,
                        genre=genre,
                        tags=tags,
                        album_name=album_name,
                        confidence=85 if source_provider in ("soundcloud", "spotify") else 50,
                    )
                    session.add(enrichment)

                    if tags:
                        for tag_text in tags:
                            normalized = tag_text.strip().lower()[:50]
                            if normalized:
                                session.add(TrackTag(
                                    track_id=track.id,
                                    tag=normalized,
                                    source=TagSource.ENRICHMENT,
                                ))
                
                logger.info(f"Created track {track.id}: {title} - {artist}")
            
            track_id = track.id
            
            # Check if already in user's library
            lib_result = await session.execute(
                select(UserLibrary)
                .where(UserLibrary.user_id == user_id, UserLibrary.track_id == track_id)
            )
            existing_lib = lib_result.scalar_one_or_none()
            
            if existing_lib:
                return SaveTrackResult(
                    track_id=track_id,
                    is_new=False,
                    was_in_library=True,
                )
            
            # Add to user's library if requested
            if add_to_library:
                lib_entry = UserLibrary(
                    user_id=user_id,
                    track_id=track_id,
                    source=library_source,
                )
                session.add(lib_entry)
                logger.info(f"Added track {track_id} to user {user_id}'s library")
            else:
                logger.info(f"Track {track_id} registered globally without adding to user {user_id}'s library")
        
        if is_new and enrich and not is_chunk:
            enrichment_worker.notify_new_track()
        
        return SaveTrackResult(
            track_id=track_id,
            is_new=is_new,
            was_in_library=False,
        )
    
    async def get_track(
        self,
        track_id: int,
        with_enrichment: bool = True,
        with_albums: bool = False,
    ) -> Optional[Track]:
        """Get track by ID with optional relationships"""
        async with get_session() as session:
            options = []
            if with_enrichment:
                options.append(selectinload(Track.enrichment))
            if with_albums:
                options.append(selectinload(Track.album_tracks).selectinload(AlbumTrack.album))
            
            query = select(Track).where(Track.id == track_id)
            if options:
                query = query.options(*options)
            
            result = await session.execute(query)
            return result.scalar_one_or_none()
    
    async def get_user_tracks(
        self,
        user_id: int,
        source: Optional[LibrarySource] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "added_desc",
    ) -> TrackSearchResult:
        """
        Get tracks from user's library.
        
        Args:
            user_id: User ID
            source: Filter by library source
            search: Search query (matches title, artist)
            limit: Max results
            offset: Pagination offset
            order_by: Sort order (added_desc, added_asc, title_asc, artist_asc)
            
        Returns:
            TrackSearchResult with tracks, total count, and has_more flag
        """
        async with get_session() as session:
            # Base query - join Track with UserLibrary
            query = (
                select(Track)
                .join(UserLibrary, UserLibrary.track_id == Track.id)
                .where(UserLibrary.user_id == user_id)
            )
            
            # Source filter
            if source:
                query = query.where(UserLibrary.source == source)
            
            # Search filter
            if search:
                search_term = f"%{search.lower()}%"
                query = query.where(
                    or_(
                        func.lower(Track.title).like(search_term),
                        func.lower(Track.artist).like(search_term),
                    )
                )
            
            # Count total
            count_query = select(func.count()).select_from(query.subquery())
            total = (await session.execute(count_query)).scalar() or 0
            
            # Ordering
            if order_by == "added_desc":
                query = query.order_by(desc(UserLibrary.added_at))
            elif order_by == "added_asc":
                query = query.order_by(UserLibrary.added_at)
            elif order_by == "title_asc":
                query = query.order_by(func.lower(Track.title))
            elif order_by == "artist_asc":
                query = query.order_by(func.lower(Track.artist))
            
            # Pagination
            query = query.offset(offset).limit(limit)
            
            # Load with enrichment
            query = query.options(selectinload(Track.enrichment))
            
            result = await session.execute(query)
            tracks = list(result.scalars().unique().all())
            
            return TrackSearchResult(
                tracks=tracks,
                total=total,
                has_more=(offset + len(tracks)) < total,
            )
    
    async def update_track(
        self,
        track_id: int,
        user_id: int,
        title: Optional[str] = None,
        artist: Optional[str] = None,
    ) -> bool:
        """
        Update track metadata.
        
        Allowed for track uploader or any user who has the track in their library.
        
        Returns:
            True if track was found and updated
        """
        async with get_session() as session:
            track = await session.get(Track, track_id)
            if not track:
                return False
            
            # Check permission: user must be uploader OR have track in UserLibrary
            lib_entry = await session.scalar(
                select(UserLibrary).where(
                    UserLibrary.user_id == user_id,
                    UserLibrary.track_id == track_id,
                )
            )
            if track.uploader_id != user_id and not lib_entry:
                logger.warning(f"User {user_id} tried to edit track {track_id} not in library")
                return False
            
            changed = False
            
            if title and title.strip() and title.strip() != track.title:
                track.title = title.strip()
                changed = True
            
            if artist and artist.strip():
                sanitized_artist = sanitize_artist(artist.strip())
                if sanitized_artist != track.artist:
                    track.artist = sanitized_artist
                    track.normalized_artist = normalize_artist(sanitized_artist)
                    changed = True
            
            if changed:
                track.updated_at = utcnow()
                track.enrichment_status = EnrichmentStatus.COMPLETED
                logger.info(f"Updated track {track_id}")
            
            await session.commit()
            
            if changed:
                try:
                    from ..channels import channel_service
                    await channel_service.update_channel_message(track_id)
                except Exception as e:
                    logger.debug(f"Failed to update channel message for track {track_id}: {e}")
            
            return True
    
    async def remove_from_library(self, track_id: int, user_id: int) -> bool:
        """
        Remove a track from user's library.
        
        If user is uploader and no one else has the track, track is deleted.
        
        Returns:
            True if track was removed
        """
        async with get_session() as session:
            # Find library entry
            result = await session.execute(
                select(UserLibrary)
                .where(UserLibrary.track_id == track_id, UserLibrary.user_id == user_id)
            )
            lib_entry = result.scalar_one_or_none()
            
            if not lib_entry:
                return False
            
            # Remove from library
            await session.delete(lib_entry)
            
            # Check if anyone else has this track
            other_count = await session.scalar(
                select(func.count(UserLibrary.id))
                .where(UserLibrary.track_id == track_id, UserLibrary.user_id != user_id)
            )
            
            if other_count == 0:
                # No one else has it - delete if user is uploader
                track = await session.get(Track, track_id)
                if track and track.uploader_id == user_id:
                    # Delete album associations
                    await session.execute(
                        delete(AlbumTrack).where(AlbumTrack.track_id == track_id)
                    )
                    # Delete enrichment
                    if track.enrichment:
                        await session.delete(track.enrichment)
                    # Delete track
                    await session.delete(track)
                    logger.info(f"Deleted track {track_id}")
            
            return True
    
    async def get_library_stats(self, user_id: int) -> Dict[str, Any]:
        """Get user's library statistics"""
        async with get_session() as session:
            # Total tracks
            total_result = await session.execute(
                select(func.count(UserLibrary.id))
                .where(UserLibrary.user_id == user_id)
            )
            total_tracks = total_result.scalar() or 0
            
            # By source
            source_result = await session.execute(
                select(UserLibrary.source, func.count(UserLibrary.id))
                .where(UserLibrary.user_id == user_id)
                .group_by(UserLibrary.source)
            )
            by_source = {
                source.value if hasattr(source, 'value') else str(source): count
                for source, count in source_result.all()
            }
            
            # Enrichment stats
            enrichment_result = await session.execute(
                select(Track.enrichment_status, func.count(Track.id))
                .join(UserLibrary, UserLibrary.track_id == Track.id)
                .where(UserLibrary.user_id == user_id)
                .group_by(Track.enrichment_status)
            )
            enrichment_stats = {
                status.value if hasattr(status, 'value') else str(status): count
                for status, count in enrichment_result.all()
            }
            
            # Album count (albums with tracks in user's library)
            album_count_result = await session.execute(
                select(func.count(func.distinct(AlbumTrack.album_id)))
                .join(UserLibrary, UserLibrary.track_id == AlbumTrack.track_id)
                .where(UserLibrary.user_id == user_id)
            )
            album_count = album_count_result.scalar() or 0
            
            # Total duration
            duration_result = await session.execute(
                select(func.sum(Track.duration))
                .join(UserLibrary, UserLibrary.track_id == Track.id)
                .where(UserLibrary.user_id == user_id)
            )
            total_duration = duration_result.scalar() or 0
            
            return {
                "total_tracks": total_tracks,
                "by_source": by_source,
                "enrichment": enrichment_stats,
                "album_count": album_count,
                "total_duration_seconds": total_duration,
            }
    
    async def trigger_enrichment(self, track_id: int) -> bool:
        """Manually trigger enrichment for a track"""
        return await enrichment_worker.enrich_single(track_id)
    
    async def retry_failed_enrichment(self) -> int:
        """Reset all failed enrichments to pending"""
        return await enrichment_worker.retry_failed()

    async def re_source_audio(
        self,
        bot: Any,
        track_id: int,
        user_id: Optional[int] = None,
        custom_url: Optional[str] = None,
        target_chat_id: Optional[int] = None,
    ) -> Optional[Track]:
        """
        Re-download and replace audio stream for an existing track with a dead/invalid file_id.
        Replaces Telegram file_id, duration, and file_size in-place without breaking user libraries or playlists.
        """
        import os
        import tempfile
        from aiogram.types import FSInputFile
        from bot.services.ingestion.audio_resolver import audio_resolver
        from bot.services.ingestion.base import TrackMetadata
        from bot.services.ingestion.registry import provider_registry
        from bot.services.channels import get_channel_service

        async with get_session() as session:
            track = await session.get(Track, track_id)
            if not track:
                return None

            with tempfile.TemporaryDirectory() as temp_dir:
                if custom_url:
                    prov = provider_registry.find_provider(custom_url)
                    if not prov:
                        raise ValueError(f"No provider found for URL: {custom_url}")
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

                dest_chat = target_chat_id
                if not dest_chat and user_id:
                    dest_chat = user_id
                    try:
                        channel_svc = get_channel_service()
                        if channel_svc:
                            user_ch = await channel_svc.get_user_channel(user_id)
                            if user_ch and user_ch.is_active:
                                dest_chat = user_ch.channel_id
                    except Exception:
                        pass

                if not dest_chat:
                    dest_chat = user_id

                safe_filename = f"{track.artist} - {track.title}.mp3".replace("/", "-")
                audio_input = FSInputFile(downloaded.audio_path, filename=safe_filename)
                thumb_input = None
                if downloaded.cover_path and os.path.exists(downloaded.cover_path):
                    thumb_input = FSInputFile(downloaded.cover_path)

                sent_msg = await bot.send_audio(
                    chat_id=dest_chat,
                    audio=audio_input,
                    title=track.title,
                    performer=track.artist,
                    duration=downloaded.metadata.duration if downloaded.metadata else track.duration,
                    thumbnail=thumb_input,
                )

                if not sent_msg or not sent_msg.audio:
                    raise RuntimeError("Telegram failed to upload re-sourced audio")

                track.file_id = sent_msg.audio.file_id
                track.file_unique_id = sent_msg.audio.file_unique_id
                track.duration = sent_msg.audio.duration or track.duration
                track.file_size = sent_msg.audio.file_size or downloaded.file_size
                track.is_unavailable = False
                track.updated_at = utcnow()
                await session.commit()
                await session.refresh(track)
                logger.info(f"Successfully re-sourced audio for track {track_id} ('{track.artist} - {track.title}')")
                return track


# Global instance
track_service = TrackService()
