"""
TG Player - Background Enrichment Worker

Periodically processes tracks with pending enrichment status.
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import select, func

from shared.database import get_session
from shared.models import Track, TrackEnrichment, TrackTag, TagSource, EnrichmentStatus, utcnow
from shared.matching import clean_track_metadata, normalize_artist, is_bogus_album_name
from .processor import enrichment_processor, EnrichmentResult

logger = logging.getLogger(__name__)


class EnrichmentWorker:
    """Background worker for automatic track enrichment"""
    
    def __init__(self):
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._notify_event: Optional[asyncio.Event] = None
        self._stats = {
            "processed": 0,
            "success": 0,
            "failed": 0,
        }
        
        # Callbacks
        self._on_enrichment_complete = None

    def _get_event(self) -> asyncio.Event:
        if self._notify_event is None:
            self._notify_event = asyncio.Event()
        return self._notify_event

    def notify_new_track(self):
        """Signal worker that new pending tracks are available for instant processing"""
        if self._notify_event:
            self._notify_event.set()
    
    def set_on_enrichment_complete(self, callback):
        """
        Set callback to be called when track enrichment completes.
        Callback signature: async def callback(track_id: int, result: EnrichmentResult)
        """
        self._on_enrichment_complete = callback
    
    async def start(self, idle_interval: int = 300, busy_interval: int = 2):
        """
        Start background enrichment loop.
        
        Args:
            idle_interval: Seconds between fallback sanity checks when idle (default 300s)
            busy_interval: Seconds to wait between batches when processing active queue (default 2s)
        """
        if self.running:
            return
        
        self.running = True
        self._notify_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._worker_loop(idle_interval, busy_interval)
        )
        logger.info("Enrichment worker started (event-driven)")
    
    async def stop(self):
        """Stop background enrichment"""
        self.running = False
        if self._notify_event:
            self._notify_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        await enrichment_processor.close()
        logger.info("Enrichment worker stopped")
    
    async def _worker_loop(self, idle_interval: int, busy_interval: int):
        """Main worker loop with event-driven wakeup and zero idle I/O"""
        event = self._get_event()
        while self.running:
            try:
                had_work = await self._process_batch()
                
                if had_work:
                    # Continue quickly while there is work in the batch
                    await asyncio.sleep(busy_interval)
                else:
                    # No pending tracks: sleep until notified or periodic health-check
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=idle_interval)
                    except asyncio.TimeoutError:
                        pass
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Enrichment worker error: {e}")
                await asyncio.sleep(min(idle_interval, 30))
    
    async def _process_batch(self, batch_size: int = 10) -> bool:
        """
        Process a batch of pending tracks.
        
        Returns:
            True if there was work to do
        """
        async with get_session() as session:
            # Get pending tracks - prioritize newest tracks (higher id = newer)
            result = await session.execute(
                select(Track)
                .where(Track.enrichment_status == EnrichmentStatus.PENDING)
                .order_by(Track.id.desc())
                .limit(batch_size)
            )
            tracks = list(result.scalars().all())
            
            if not tracks:
                return False
            
            logger.info(f"Processing {len(tracks)} tracks for enrichment")
            
            # Mark as processing
            for track in tracks:
                track.enrichment_status = EnrichmentStatus.PROCESSING
            await session.flush()
        
        # Process each track (outside of main session to avoid long locks)
        for track in tracks:
            await self._enrich_track(track.id, track.title, track.artist, track.duration)
            await asyncio.sleep(0.4)  # Pacing to protect 1-vCPU server and prevent API rate-limits
        
        self._stats["processed"] += len(tracks)
        return True
    
    async def _enrich_track(self, track_id: int, title: str, artist: str, duration: Optional[int] = None):
        """Enrich a single track"""
        try:
            # Get enrichment data
            result = await enrichment_processor.enrich_track(title, artist or "", duration=duration)
            
            async with get_session() as session:
                track = await session.get(Track, track_id)
                if not track:
                    return
                
                if result.success:
                    # Create or update enrichment record
                    enrichment = await session.scalar(
                        select(TrackEnrichment)
                        .where(TrackEnrichment.track_id == track_id)
                    )
                    
                    if not enrichment:
                        enrichment = TrackEnrichment(track_id=track_id)
                        session.add(enrichment)
                    
                    is_soundcloud = (track.forward_source_name == "soundcloud")
                    is_from_provider = is_soundcloud or (track.forward_source_name == "spotify")

                    # Update enrichment data non-destructively:
                    # 1. Album: For SoundCloud tracks, never attach unrelated Deezer albums
                    if not is_soundcloud:
                        has_real_album = bool(enrichment.album_name and not is_bogus_album_name(enrichment.album_name))
                        if result.album_name:
                            if not has_real_album and not is_bogus_album_name(result.album_name):
                                enrichment.album_name = result.album_name
                        elif is_bogus_album_name(enrichment.album_name):
                            enrichment.album_name = None

                        if result.deezer_album_id and (not enrichment.deezer_album_id or not has_real_album):
                            enrichment.deezer_album_id = result.deezer_album_id
                    elif is_bogus_album_name(enrichment.album_name):
                        enrichment.album_name = None

                    # 2. Genre: Preserve original provider genre (e.g. from SoundCloud), fill if missing
                    if result.genre and not enrichment.genre:
                        enrichment.genre = result.genre

                    # 3. Tags: Merge external tags with existing tags without duplicates
                    if result.tags:
                        current_tags = list(enrichment.tags or [])
                        merged_tags = list(dict.fromkeys(current_tags + result.tags))[:10]
                        enrichment.tags = merged_tags
                        # Also populate normalized track_tags table
                        for tag_text in merged_tags:
                            normalized = tag_text.strip().lower()[:50]
                            if normalized:
                                existing = await session.scalar(
                                    select(TrackTag).where(
                                        TrackTag.track_id == track_id,
                                        TrackTag.tag == normalized,
                                    )
                                )
                                if not existing:
                                    session.add(TrackTag(
                                        track_id=track_id,
                                        tag=normalized,
                                        source=TagSource.ENRICHMENT,
                                    ))

                    # 4. Cover: Never overwrite an existing high-res cover (e.g. SoundCloud 500x500, Telegram file_id)
                    # BUT do replace YouTube covers (16:9 video frame / letterbox) with official square Deezer artwork (1000x1000)
                    is_yt_cover = bool(enrichment.cover_url and ("ytimg.com" in enrichment.cover_url or "youtube.com" in enrichment.cover_url))
                    if result.cover_url and (not enrichment.cover_url or is_yt_cover):
                        enrichment.cover_url = result.cover_url

                    # 5. Missing supplementary metadata
                    if result.release_date and not enrichment.release_date:
                        enrichment.release_date = result.release_date
                    if result.track_number and not enrichment.track_number:
                        enrichment.track_number = result.track_number
                    if result.deezer_track_id and not enrichment.deezer_track_id:
                        enrichment.deezer_track_id = result.deezer_track_id
                    if result.lastfm_url and not enrichment.lastfm_url:
                        enrichment.lastfm_url = result.lastfm_url
                    
                    enrichment.confidence = max(enrichment.confidence or 0, result.confidence)
                    enrichment.enriched_at = utcnow()
                    
                    # Propagate canonical title and artist ONLY for placeholder/empty metadata
                    # External provider tracks (SoundCloud, Spotify) already have author metadata
                    if not is_from_provider and result.confidence >= 65:
                        if result.canonical_title:
                            placeholder_titles = ("без названия", "unknown track", "untitled", "audio", "track")
                            if not track.title or track.title.strip().lower() in placeholder_titles:
                                track.title = result.canonical_title
                        if result.canonical_artist:
                            placeholder_artists = ("unknown", "unknown artist", "неизвестен", "неизвестный исполнитель", "неизвестный")
                            if not track.artist or track.artist.strip().lower() in placeholder_artists or "@" in track.artist or "t.me/" in track.artist:
                                track.artist = result.canonical_artist
                                track.normalized_artist = normalize_artist(result.canonical_artist)
                    
                    track.enrichment_status = EnrichmentStatus.COMPLETED
                    track.updated_at = utcnow()
                    self._stats["success"] += 1
                    
                    logger.info(f"Enriched track {track_id}: {track.title} - {track.artist} (provider: {track.forward_source_name or 'upload'})")
                else:
                    # Check if track already has enrichment from provider (cover, genre, tags)
                    enrichment = await session.scalar(
                        select(TrackEnrichment)
                        .where(TrackEnrichment.track_id == track_id)
                    )
                    if enrichment and (enrichment.cover_url or enrichment.genre or enrichment.tags):
                        track.enrichment_status = EnrichmentStatus.COMPLETED
                        track.updated_at = utcnow()
                        self._stats["success"] += 1
                        logger.info(f"Track {track_id} preserved with provider metadata ({track.forward_source_name or 'custom'})")
                    else:
                        track.enrichment_status = EnrichmentStatus.FAILED
                        self._stats["failed"] += 1
                        logger.debug(f"No enrichment data for track {track_id}")
            
            # Call completion callback
            if self._on_enrichment_complete:
                try:
                    await self._on_enrichment_complete(track_id, result)
                except Exception as e:
                    logger.error(f"Enrichment callback error: {e}")

            # Invalidate any cached minecraft pack so it can rebuild with the new artwork
            try:
                from api.services.minecraft_pack import invalidate_pack
                invalidate_pack(track_id)
            except Exception:
                pass
                    
        except Exception as e:
            logger.error(f"Failed to enrich track {track_id}: {e}")
            
            async with get_session() as session:
                track = await session.get(Track, track_id)
                if track:
                    track.enrichment_status = EnrichmentStatus.FAILED
    
    async def get_stats(self) -> dict:
        """Get enrichment statistics"""
        async with get_session() as session:
            # Count by status
            result = await session.execute(
                select(Track.enrichment_status, func.count(Track.id))
                .group_by(Track.enrichment_status)
            )
            
            status_counts = {
                status.value if isinstance(status, EnrichmentStatus) else status: count 
                for status, count in result.all()
            }
            
            return {
                "pending": status_counts.get("pending", 0),
                "processing": status_counts.get("processing", 0),
                "completed": status_counts.get("completed", 0),
                "failed": status_counts.get("failed", 0),
                "total": sum(status_counts.values()),
                "session_processed": self._stats["processed"],
                "session_success": self._stats["success"],
                "session_failed": self._stats["failed"],
            }
    
    async def retry_failed(self) -> int:
        """
        Reset failed tracks to pending for retry.
        
        Returns:
            Number of tracks reset
        """
        async with get_session() as session:
            result = await session.execute(
                select(Track)
                .where(Track.enrichment_status == EnrichmentStatus.FAILED)
            )
            tracks = result.scalars().all()
            
            for track in tracks:
                track.enrichment_status = EnrichmentStatus.PENDING
            
            count = len(tracks)
            logger.info(f"Reset {count} failed tracks to pending")
            if count > 0:
                self.notify_new_track()
            return count
    
    async def enrich_single(self, track_id: int) -> bool:
        """
        Manually trigger enrichment for a single track.
        
        Returns:
            True if enrichment was successful
        """
        async with get_session() as session:
            track = await session.get(Track, track_id)
            if not track:
                return False
            
            title = track.title
            artist = track.artist
            duration = track.duration
            track.enrichment_status = EnrichmentStatus.PROCESSING
        
        await self._enrich_track(track_id, title, artist, duration=duration)
        
        async with get_session() as session:
            track = await session.get(Track, track_id)
            return track.enrichment_status == EnrichmentStatus.COMPLETED if track else False


# Global instance
enrichment_worker = EnrichmentWorker()
