"""
TG Player - User Channel Service

Handles backup of user's music library to their Telegram channel.
Features:
- Forward tracks to user's channel with rate-limiting queue
- Generate hashtags for easy searching
- Update messages when enrichment completes
"""
from typing import Optional, List
from datetime import datetime, timezone
from collections import deque
from dataclasses import dataclass
import html
import json
import logging

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

import asyncio

from shared.models import (
    UserChannel, ChannelMessage, ChannelMessageStatus, Track, utcnow
)
from shared.matching import generate_hashtags, format_hashtags
from shared.database import get_session
from shared.config import get_settings

MAX_RETRY_COUNT = 3

logger = logging.getLogger(__name__)


def build_track_caption(track, hashtags=None, *, enrichment_override=None):
    """Build a standard caption for a track in a channel message (HTML escaped).
    
    Args:
        track: Track model instance
        hashtags: Optional list of hashtag strings
        enrichment_override: If provided, use this instead of track.enrichment
    
    Returns:
        Caption string or None if no parts
    """
    caption_parts = []
    if hasattr(track, 'has_metadata') and not track.has_metadata:
        caption_parts.append(f"🎵 {html.escape(track.display_title or '')}")
    else:
        if track.title:
            caption_parts.append(f"🎵 {html.escape(track.title)}")
        if track.artist:
            caption_parts.append(f"👤 {html.escape(track.artist)}")
        enr = enrichment_override if enrichment_override is not None else getattr(track, 'enrichment', None)
        if enr and enr.album_name:
            caption_parts.append(f"💿 {html.escape(enr.album_name)}")
    
    if hashtags:
        caption_parts.append("")
        caption_parts.append(format_hashtags(hashtags))
    
    return "\n".join(caption_parts) if caption_parts else None


@dataclass
class ForwardQueueItem:
    """Item in the forward queue"""
    user_id: int
    track_id: int
    added_at: datetime


class ChannelService:
    """Service for managing user channels and messages"""
    
    # Delay between forwarding tracks (seconds) - Telegram allows ~20 msg/min
    FORWARD_DELAY = 3.0
    
    def __init__(self):
        self.bot: Optional[Bot] = None
        self._cancel_sync: dict[int, bool] = {}  # user_id -> cancel flag
        self._active_sync: dict[int, dict] = {}  # user_id -> sync status info
        
        # Forward queue for rate-limited sending
        self._forward_queue: deque[ForwardQueueItem] = deque()
        self._queue_notify_event: Optional[asyncio.Event] = None
        self._queue_worker_task: Optional[asyncio.Task] = None
        self._queue_running = False

    def _get_queue_event(self) -> asyncio.Event:
        if self._queue_notify_event is None:
            self._queue_notify_event = asyncio.Event()
        return self._queue_notify_event
    
    def set_bot(self, bot: Bot):
        """Set bot instance for sending messages"""
        self.bot = bot
    
    async def _restore_pending_from_db(self):
        """Restore pending channel forwards from database after server restart."""
        try:
            async with get_session() as session:
                result = await session.execute(
                    select(ChannelMessage.track_id, UserChannel.user_id, ChannelMessage.created_at)
                    .join(UserChannel, ChannelMessage.channel_id == UserChannel.id)
                    .where(
                        ChannelMessage.status == ChannelMessageStatus.PENDING,
                        UserChannel.is_active == True,
                        UserChannel.auto_forward == True,
                    )
                    .order_by(ChannelMessage.id.asc())
                )
                existing_track_ids = {item.track_id for item in self._forward_queue}
                restored_count = 0
                for track_id, user_id, created_at in result.all():
                    if track_id not in existing_track_ids:
                        self._forward_queue.append(
                            ForwardQueueItem(
                                user_id=user_id,
                                track_id=track_id,
                                added_at=created_at or utcnow(),
                            )
                        )
                        existing_track_ids.add(track_id)
                        restored_count += 1
                
                if restored_count > 0:
                    logger.info(f"Restored {restored_count} pending channel forward tasks from database")
                    if self._queue_notify_event:
                        self._queue_notify_event.set()
        except Exception as e:
            logger.error(f"Failed to restore pending channel forwards from DB: {e}")

    async def start_queue_worker(self):
        """Start background queue worker for rate-limited forwarding"""
        if self._queue_running:
            return
        
        self._queue_running = True
        self._queue_notify_event = asyncio.Event()
        
        # Restore any pending tasks that were in DB before restart
        await self._restore_pending_from_db()
        
        self._queue_worker_task = asyncio.create_task(self._queue_worker_loop())
        logger.info("Channel forward queue worker started")
    
    async def stop_queue_worker(self):
        """Stop background queue worker"""
        self._queue_running = False
        if self._queue_notify_event:
            self._queue_notify_event.set()
        if self._queue_worker_task:
            self._queue_worker_task.cancel()
            try:
                await self._queue_worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Channel forward queue worker stopped")
    
    async def _queue_worker_loop(self):
        """Main loop for processing forward queue with rate limiting and event-driven wait"""
        event = self._get_queue_event()
        while self._queue_running:
            try:
                if self._forward_queue:
                    item = self._forward_queue.popleft()
                    await self._forward_track_immediately(
                        user_id=item.user_id,
                        track_id=item.track_id,
                    )
                    # Delay to avoid rate limiting
                    await asyncio.sleep(self.FORWARD_DELAY)
                else:
                    # No items in queue: sleep until notified or periodic check
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=30.0)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Forward queue worker error: {e}")
                await asyncio.sleep(1)
    
    def queue_track_for_forward(self, user_id: int, track_id: int):
        """
        Add track to forward queue for rate-limited sending.
        Uses the same mechanism as manual sync.
        """
        item = ForwardQueueItem(
            user_id=user_id,
            track_id=track_id,
            added_at=datetime.now(timezone.utc),
        )
        self._forward_queue.append(item)
        if self._queue_notify_event:
            self._queue_notify_event.set()
        logger.debug(f"Track {track_id} queued for forward (queue size: {len(self._forward_queue)})")
    
    def get_queue_size(self, user_id: Optional[int] = None) -> int:
        """Get current queue size, optionally filtered by user"""
        if user_id is None:
            return len(self._forward_queue)
        return sum(1 for item in self._forward_queue if item.user_id == user_id)
    
    def request_cancel_sync(self, user_id: int):
        """Request cancellation of ongoing sync for user"""
        self._cancel_sync[user_id] = True
    
    def is_sync_cancelled(self, user_id: int) -> bool:
        """Check if sync is cancelled for user"""
        return self._cancel_sync.get(user_id, False)
    
    def clear_cancel_flag(self, user_id: int):
        """Clear cancel flag for user"""
        self._cancel_sync.pop(user_id, None)
    
    def is_sync_active(self, user_id: int) -> bool:
        """Check if sync is currently running for user"""
        return user_id in self._active_sync
    
    def get_sync_status(self, user_id: int) -> Optional[dict]:
        """Get current sync status for user"""
        return self._active_sync.get(user_id)
    
    def _update_sync_status(self, user_id: int, current: int, total: int, synced: int):
        """Update sync status for user"""
        self._active_sync[user_id] = {
            "current": current,
            "total": total,
            "synced": synced
        }
    
    def _clear_sync_status(self, user_id: int):
        """Clear sync status for user"""
        self._active_sync.pop(user_id, None)
    
    async def setup_channel(
        self,
        user_id: int,
        channel_id: int,
        channel_username: Optional[str] = None,
        channel_title: Optional[str] = None,
        bot: Optional[Bot] = None,
    ) -> Optional[UserChannel]:
        """
        Setup or update user's channel for library backup.
        Verifies bot has access before saving.
        
        Args:
            user_id: Telegram user ID
            channel_id: Telegram channel ID (negative number)
            channel_username: Channel @username (optional)
            channel_title: Channel title (optional)
            bot: Bot instance for verification
        
        Returns:
            UserChannel object if successful, None if verification failed
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for channel setup")
            return None
        
        # Verify access first
        success, title, error = await self.verify_channel_access(channel_id, use_bot)
        if not success:
            logger.warning(f"Channel verification failed for {channel_id}: {error}")
            return None
        
        # Use title from verification if not provided
        if not channel_title and title:
            channel_title = title
        
        async with get_session() as session:
            # Check if user already has a channel
            existing = await session.scalar(
                select(UserChannel).where(UserChannel.user_id == user_id)
            )
            
            if existing:
                # Update existing
                existing.channel_id = channel_id
                existing.channel_username = channel_username
                existing.channel_title = channel_title
                existing.is_active = True
                existing.updated_at = utcnow()
                logger.info(f"Channel updated for user {user_id}: {channel_id}")
                return existing
            
            # Create new
            channel = UserChannel(
                user_id=user_id,
                channel_id=channel_id,
                channel_username=channel_username,
                channel_title=channel_title,
            )
            session.add(channel)
            await session.flush()
            
            logger.info(f"Channel setup for user {user_id}: {channel_id}")
            return channel
    
    async def get_user_channel(
        self,
        user_id: int
    ) -> Optional[UserChannel]:
        """Get user's channel if configured"""
        async with get_session() as session:
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            if channel:
                # Eagerly load message count (only SENT = actually in channel)
                count = await session.scalar(
                    select(func.count(ChannelMessage.id)).where(
                        ChannelMessage.channel_id == channel.id,
                        ChannelMessage.status == ChannelMessageStatus.SENT,
                    )
                )
                # Add as dynamic attribute
                channel._message_count = count
            return channel
    
    @property
    def message_count(self):
        """Get message count - for use with UserChannel object"""
        return getattr(self, '_message_count', 0)
    
    async def get_channel_message_count(self, user_id: int) -> int:
        """Get count of messages in user's channel"""
        async with get_session() as session:
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            if not channel:
                return 0
            
            count = await session.scalar(
                select(func.count(ChannelMessage.id)).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                )
            )
            return count or 0
    
    async def get_sync_stats(self, user_id: int) -> dict:
        """
        Get sync statistics before starting sync.
        
        Returns:
            Dict with: total_tracks, already_synced, to_sync
        """
        async with get_session() as session:
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            
            if not channel:
                return {"total_tracks": 0, "already_synced": 0, "to_sync": 0, "error": "No channel"}
            
            # Count total tracks in library
            from shared.models import UserLibrary
            total = await session.scalar(
                select(func.count(UserLibrary.id)).where(
                    UserLibrary.user_id == user_id
                )
            ) or 0
            
            # Count already synced (only SENT status = confirmed in channel)
            already_synced = await session.scalar(
                select(func.count(ChannelMessage.id)).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                )
            ) or 0
            
            return {
                "total_tracks": total,
                "already_synced": already_synced,
                "to_sync": max(0, total - already_synced),
                "channel_title": channel.channel_title or "Канал"
            }
    
    async def disable_channel(
        self,
        user_id: int
    ) -> bool:
        """Disable user's channel (don't delete, just deactivate)"""
        async with get_session() as session:
            channel = await session.scalar(
                select(UserChannel).where(UserChannel.user_id == user_id)
            )
            
            if channel:
                channel.is_active = False
                channel.updated_at = utcnow()
                return True
            
            return False
    
    async def reconcile_channel_tracks(self, user_id: int) -> int:
        """
        Reconcile unavailable tracks that exist in the user's channel.
        Resets track.is_unavailable = False for tracks with confirmed ChannelMessage.
        Returns count of restored tracks.
        """
        async with get_session() as session:
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True,
                )
            )
            if not channel:
                return 0
            
            stmt = (
                select(Track)
                .join(ChannelMessage, ChannelMessage.track_id == Track.id)
                .where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                    Track.is_unavailable == True,
                )
            )
            tracks = (await session.scalars(stmt)).all()
            count = 0
            for t in tracks:
                t.is_unavailable = False
                count += 1
            
            if count > 0:
                await session.commit()
                logger.info(f"Reconciled {count} unavailable tracks for user {user_id}")
            return count
    
    async def forward_track_to_channel(
        self,
        user_id: int,
        track_id: int,
        bot: Optional[Bot] = None,
        immediate: bool = False,
    ) -> bool:
        """
        Queue a track for forwarding to user's channel with hashtags.
        Uses rate-limited queue to avoid Telegram flood limits.
        
        Args:
            user_id: User who owns the channel
            track_id: Track ID to forward
            bot: Bot instance for sending (used if immediate=True)
            immediate: If True, send immediately without queue (for sync)
        
        Returns:
            True if queued/forwarded successfully, False otherwise
        """
        if immediate:
            return await self._forward_track_immediately(user_id, track_id, bot)
        
        # Check if channel exists and auto_forward is enabled before queuing
        async with get_session() as session:
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            
            if not channel or not channel.auto_forward:
                return False
            
            # Check if track already sent (SENT) or pending send
            existing = await session.scalar(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.track_id == track_id,
                )
            )
            if existing:
                if existing.status == ChannelMessageStatus.SENT:
                    logger.debug(f"Track {track_id} already in channel, skipping queue")
                    return True
                if existing.status == ChannelMessageStatus.PENDING:
                    logger.debug(f"Track {track_id} already pending, skipping queue")
                    return True
                if existing.status == ChannelMessageStatus.FAILED and existing.retry_count >= MAX_RETRY_COUNT:
                    logger.debug(f"Track {track_id} failed {existing.retry_count} times, skipping")
                    return False
                # FAILED with retries left, or DELETED — re-queue
        
        # Add to queue for rate-limited sending
        self.queue_track_for_forward(user_id, track_id)
        return True
    
    async def _forward_track_immediately(
        self,
        user_id: int,
        track_id: int,
        bot: Optional[Bot] = None,
    ) -> bool:
        """
        Forward a track to user's channel immediately (internal use).
        Uses write-ahead pattern:
          1. INSERT/UPDATE ChannelMessage with status=PENDING (committed to DB)
          2. Send to Telegram
          3. UPDATE to status=SENT + message_id (on success)
             or status=FAILED + last_error (on failure)
        
        This guarantees the DB always knows about in-flight operations.
        If the bot crashes between steps 2 and 3, the PENDING record
        will be picked up and reconciled on next sync.
        
        Returns:
            True if forwarded successfully, False otherwise
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for forwarding")
            return False
        
        async with get_session() as session:
            # Get channel
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            
            if not channel or not channel.auto_forward:
                return False
            
            # Check existing record
            existing = await session.scalar(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.track_id == track_id,
                )
            )
            
            if existing:
                if existing.status == ChannelMessageStatus.SENT:
                    logger.debug(f"Track {track_id} already sent to channel, skipping")
                    return True
                if existing.status == ChannelMessageStatus.FAILED and existing.retry_count >= MAX_RETRY_COUNT:
                    logger.warning(f"Track {track_id} exceeded max retries ({MAX_RETRY_COUNT})")
                    return False
                # PENDING (e.g. restored from DB on startup), FAILED (retries left), or DELETED → reuse record
                channel_message = existing
                channel_message.status = ChannelMessageStatus.PENDING
                channel_message.message_id = None
                channel_message.last_error = None
                channel_message.updated_at = utcnow()
            else:
                # STEP 1: Write-ahead — create PENDING record before sending
                channel_message = ChannelMessage(
                    channel_id=channel.id,
                    track_id=track_id,
                    status=ChannelMessageStatus.PENDING,
                    message_id=None,
                )
                session.add(channel_message)
            
            await session.commit()  # PENDING record is now durable
        
        # STEP 2: Send to Telegram (outside DB transaction)
        # From here, if we crash, PENDING record survives and will be reconciled
        
        async with get_session() as session:
            # Re-load the record (new session)
            channel_message = await session.scalar(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.track_id == track_id,
                )
            )
            if not channel_message or channel_message.status != ChannelMessageStatus.PENDING:
                return channel_message.status == ChannelMessageStatus.SENT if channel_message else False
            
            channel = await session.scalar(
                select(UserChannel).where(UserChannel.id == channel_message.channel_id)
            )
            if not channel:
                return False
            
            result = await session.execute(
                select(Track)
                .options(selectinload(Track.enrichment))
                .where(Track.id == track_id)
            )
            track = result.scalar_one_or_none()
            if not track:
                channel_message.status = ChannelMessageStatus.FAILED
                channel_message.last_error = "Track not found"
                await session.commit()
                return False
            
            try:
                # Generate hashtags (only for tracks with real metadata)
                hashtags = []
                if channel.include_hashtags and track.has_metadata:
                    enrichment = track.enrichment
                    hashtags = generate_hashtags(
                        artist=track.artist,
                        title=track.title,
                        album=enrichment.album_name if enrichment else None,
                        genre=enrichment.genre if enrichment else None,
                        extra_tags=enrichment.tags if enrichment else None,
                    )
                
                caption = build_track_caption(track, hashtags)
                
                # Send to Telegram (with fallback for document audio or formatting errors)
                sent_message = None
                try:
                    sent_message = await use_bot.send_audio(
                        chat_id=channel.channel_id,
                        audio=track.file_id,
                        caption=caption,
                        parse_mode="HTML",
                    )
                except TelegramBadRequest as e:
                    err_str = str(e).lower()
                    if "wrong file identifier" in err_str or "can't use file" in err_str:
                        # File was uploaded as document/file, retry with send_document
                        sent_message = await use_bot.send_document(
                            chat_id=channel.channel_id,
                            document=track.file_id,
                            caption=caption,
                            parse_mode="HTML",
                        )
                    elif "can't parse entities" in err_str:
                        # HTML entity issue, retry without parse_mode
                        sent_message = await use_bot.send_audio(
                            chat_id=channel.channel_id,
                            audio=track.file_id,
                            caption=caption,
                            parse_mode=None,
                        )
                    else:
                        raise
                
                # STEP 3a: Mark as SENT with message_id
                channel_message.status = ChannelMessageStatus.SENT
                channel_message.message_id = sent_message.message_id
                channel_message.hashtags = json.dumps(hashtags) if hashtags else None
                channel_message.last_error = None
                channel_message.updated_at = utcnow()
                await session.commit()
                
                logger.info(f"Track {track_id} forwarded to channel {channel.channel_id}")
                return True
                
            except TelegramRetryAfter as e:
                # STEP 3b: Rate limited — mark FAILED for retry
                logger.warning(f"Rate limited, waiting {e.retry_after} seconds")
                channel_message.status = ChannelMessageStatus.FAILED
                channel_message.retry_count += 1
                channel_message.last_error = f"Rate limited ({e.retry_after}s)"
                channel_message.updated_at = utcnow()
                await session.commit()
                
                await asyncio.sleep(e.retry_after + 1)
                # Re-queue for retry (will check retry_count)
                self.queue_track_for_forward(user_id, track_id)
                return False
                
            except TelegramForbiddenError:
                # Bot was removed from channel
                logger.warning(f"Bot removed from channel {channel.channel_id}, disabling")
                channel_message.status = ChannelMessageStatus.FAILED
                channel_message.last_error = "Bot removed from channel"
                channel_message.updated_at = utcnow()
                channel.is_active = False
                await session.commit()
                return False
                
            except TelegramBadRequest as e:
                logger.error(f"Failed to forward to channel: {e}")
                channel_message.status = ChannelMessageStatus.FAILED
                channel_message.retry_count += 1
                channel_message.last_error = str(e)[:500]
                channel_message.updated_at = utcnow()
                await session.commit()
                return False
    
    async def update_channel_message(
        self,
        track_id: int,
        bot: Optional[Bot] = None,
    ) -> int:
        """
        Update all channel messages for a track (after enrichment).
        Updates hashtags based on new metadata.
        Only updates messages with status=SENT (confirmed in channel).
        
        Returns:
            Number of messages updated
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for updating messages")
            return 0
        
        async with get_session() as session:
            # Get track with enrichment (eager load)
            result = await session.execute(
                select(Track)
                .options(selectinload(Track.enrichment))
                .where(Track.id == track_id)
            )
            track = result.scalar_one_or_none()
            if not track:
                return 0
            
            # Get all SENT channel messages for this track
            result = await session.execute(
                select(ChannelMessage)
                .join(UserChannel)
                .where(
                    ChannelMessage.track_id == track_id,
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                    UserChannel.is_active == True,
                    UserChannel.include_hashtags == True,
                )
            )
            messages = result.scalars().all()
            
            updated = 0
            for msg in messages:
                channel = await session.get(UserChannel, msg.channel_id)
                if not channel:
                    continue
                
                # Generate new hashtags
                new_hashtags = generate_hashtags(
                    artist=track.artist,
                    title=track.title,
                    album=track.enrichment.album_name if track.enrichment else None,
                    genre=track.enrichment.genre if track.enrichment else None,
                    extra_tags=track.enrichment.tags if track.enrichment else None,
                )
                
                caption = build_track_caption(track, new_hashtags)
                
                try:
                    await use_bot.edit_message_caption(
                        chat_id=channel.channel_id,
                        message_id=msg.message_id,
                        caption=caption,
                        parse_mode="HTML",
                    )
                    
                    msg.hashtags = json.dumps(new_hashtags)
                    msg.updated_at = utcnow()
                    updated += 1
                    
                except TelegramBadRequest as e:
                    if "message is not modified" in str(e):
                        pass  # Same content, skip
                    elif "message to edit not found" in str(e).lower():
                        # Message was deleted from channel — mark as DELETED
                        msg.status = ChannelMessageStatus.DELETED
                        msg.last_error = "Message deleted from channel"
                        msg.updated_at = utcnow()
                        logger.info(f"Channel message {msg.message_id} marked DELETED (not found during edit)")
                    else:
                        logger.error(f"Failed to update message: {e}")
                except TelegramForbiddenError:
                    # Channel access lost
                    channel.is_active = False
        
            logger.info(f"Updated {updated} channel messages for track {track_id}")
            return updated
    
    async def update_incomplete_messages(
        self,
        user_id: Optional[int] = None,
        bot: Optional[Bot] = None,
        progress_callback=None,
    ) -> dict:
        """
        Update channel messages for tracks that were synced before enrichment completed.
        Finds messages where track now has enrichment data but message has incomplete hashtags.
        
        OPTIMIZED: Pre-calculates expected hashtags and only processes messages that need updates.
        
        Args:
            user_id: Specific user to update, or None for all users
            bot: Bot instance
            progress_callback: Optional async callback(current, total, updated)
        
        Returns:
            Dict with update results: {checked, updated, failed}
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for updating messages")
            return {"checked": 0, "updated": 0, "failed": 0}
        
        stats = {"checked": 0, "updated": 0, "failed": 0, "skipped": 0}
        
        async with get_session() as session:
            # Build query for ALL channel messages for this user
            # We'll check each message and update if hashtags differ from expected
            # This covers: incomplete enrichment, new enrichment data, featured artists from title
            query = (
                select(ChannelMessage)
                .join(Track, ChannelMessage.track_id == Track.id)
                .join(UserChannel, ChannelMessage.channel_id == UserChannel.id)
                .outerjoin(TrackEnrichment, Track.id == TrackEnrichment.track_id)
                .where(
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                    UserChannel.is_active == True,
                    UserChannel.include_hashtags == True,
                )
                .options(
                    selectinload(ChannelMessage.track).selectinload(Track.enrichment),
                    selectinload(ChannelMessage.channel),
                )
            )
            
            if user_id:
                query = query.where(UserChannel.user_id == user_id)
            
            result = await session.execute(query)
            messages = result.scalars().all()
            
            total = len(messages)
            logger.info(f"Checking {total} channel messages for incomplete hashtags")
            
            # OPTIMIZATION: Pre-calculate all expected hashtags in memory first
            # This is much faster than doing it during iteration with API calls
            messages_to_update = []
            
            for msg in messages:
                track = msg.track
                enrichment = track.enrichment
                
                # Parse current hashtags
                current_hashtags = []
                if msg.hashtags:
                    try:
                        current_hashtags = json.loads(msg.hashtags)
                    except:
                        current_hashtags = []
                
                # Generate what hashtags should be (works even without enrichment)
                expected_hashtags = generate_hashtags(
                    artist=track.artist,
                    title=track.title,
                    album=enrichment.album_name if enrichment else None,
                    genre=enrichment.genre if enrichment else None,
                    extra_tags=enrichment.tags if enrichment else None,
                )
                
                # Check if update needed (compare sets to ignore order)
                if set(current_hashtags) == set(expected_hashtags):
                    stats["skipped"] += 1
                    continue  # Already up to date
                
                caption = build_track_caption(track, expected_hashtags, enrichment_override=enrichment)
                
                messages_to_update.append({
                    "msg": msg,
                    "channel": msg.channel,
                    "caption": caption,
                    "expected_hashtags": expected_hashtags,
                })
            
            stats["checked"] = total
            update_total = len(messages_to_update)
            logger.info(f"Found {update_total} messages that need hashtag updates (skipped {stats['skipped']} up-to-date)")
            
            # If nothing to update, return early
            if update_total == 0:
                if progress_callback:
                    try:
                        await progress_callback(total, total, 0)
                    except:
                        pass
                return stats
            
            # OPTIMIZATION: Progress update every 50 messages or 10% of total, whichever is smaller
            progress_step = min(50, max(1, update_total // 10))
            last_progress_update = 0
            batch_count = 0
            
            for i, item in enumerate(messages_to_update):
                msg = item["msg"]
                channel = item["channel"]
                caption = item["caption"]
                expected_hashtags = item["expected_hashtags"]
                
                # Update progress less frequently to reduce Telegram API overhead
                if progress_callback and (i - last_progress_update >= progress_step or i == 0):
                    try:
                        await progress_callback(stats["skipped"] + i, total, stats["updated"])
                        last_progress_update = i
                    except:
                        pass
                
                try:
                    await use_bot.edit_message_caption(
                        chat_id=channel.channel_id,
                        message_id=msg.message_id,
                        caption=caption,
                        parse_mode="HTML",
                    )
                    
                    msg.hashtags = json.dumps(expected_hashtags)
                    msg.updated_at = utcnow()
                    stats["updated"] += 1
                    batch_count += 1
                    
                    # OPTIMIZATION: Minimal rate limiting - Telegram allows ~30 edits/sec for own channels
                    # Only add delay every 20 messages to stay well under limits
                    if batch_count >= 20:
                        await asyncio.sleep(0.5)
                        batch_count = 0
                    
                except TelegramBadRequest as e:
                    if "message is not modified" in str(e):
                        pass  # Same content - no delay needed
                    elif "message to edit not found" in str(e).lower():
                        # Message was deleted from channel — mark as DELETED
                        msg.status = ChannelMessageStatus.DELETED
                        msg.last_error = "Message deleted from channel"
                        msg.updated_at = utcnow()
                    else:
                        logger.error(f"Failed to update message {msg.message_id}: {e}")
                        stats["failed"] += 1
                except TelegramForbiddenError:
                    channel.is_active = False
                    stats["failed"] += 1
                    break  # No point continuing if we lost access
                except TelegramRetryAfter as e:
                    logger.warning(f"Rate limited, waiting {e.retry_after}s")
                    await asyncio.sleep(e.retry_after + 1)
                    batch_count = 0  # Reset batch counter
                    # Don't count as failed, will be caught next time
            
            # Final progress update
            if progress_callback:
                try:
                    await progress_callback(total, total, stats["updated"])
                except:
                    pass
            
            await session.commit()
        
        logger.info(f"Updated incomplete messages: {stats}")
        return stats

    async def sync_all_tracks(
        self,
        user_id: int,
        bot: Optional[Bot] = None,
        progress_callback=None,
    ) -> dict:
        """
        Idempotent sync: ensure every track in UserLibrary has a SENT
        ChannelMessage, and every ChannelMessage whose track is no longer
        in the library is cleaned up.
        
        Safe to run repeatedly — never creates duplicate messages.
        
        Phases:
          1. Reconcile PENDING records (crash recovery)
          2. Clean up orphaned records (track removed from library)
          3. Re-send DELETED records (message removed from channel)
          4. Send new tracks (not yet in channel_messages at all)
        
        Args:
            user_id: User ID
            bot: Bot instance
            progress_callback: Optional async callback(current, total, synced)
        
        Returns:
            Dict with sync results: {synced, skipped, failed, deleted, recovered, total}
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for sync")
            return {"synced": 0, "skipped": 0, "failed": 0, "total": 0}
        
        async with get_session() as session:
            # Get channel
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            
            if not channel:
                return {"synced": 0, "skipped": 0, "failed": 0, "total": 0, "error": "No channel"}
            
            # Get all user's tracks from library with enrichment
            from shared.models import UserLibrary
            result = await session.execute(
                select(Track)
                .options(selectinload(Track.enrichment))
                .join(UserLibrary, UserLibrary.track_id == Track.id)
                .where(UserLibrary.user_id == user_id)
                .order_by(UserLibrary.added_at.asc())
            )
            tracks = result.scalars().all()
            library_track_ids = {t.id for t in tracks}
            tracks_by_id = {t.id: t for t in tracks}
            
            # Get ALL channel message records (any status)
            synced_result = await session.execute(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id
                )
            )
            all_messages = synced_result.scalars().all()
            messages_by_track = {msg.track_id: msg for msg in all_messages}
            
            stats = {
                "synced": 0, "skipped": 0, "failed": 0, "deleted": 0,
                "recovered": 0, "total": len(tracks), "cancelled": False,
            }
            
            # --- Phase 1: Reconcile PENDING records (crash recovery) ---
            pending_messages = [m for m in all_messages if m.status == ChannelMessageStatus.PENDING]
            for msg in pending_messages:
                if msg.track_id not in library_track_ids:
                    # Track removed from library while PENDING — clean up
                    await session.delete(msg)
                    stats["deleted"] += 1
                else:
                    # PENDING from a previous crash — mark FAILED so it gets retried below
                    msg.status = ChannelMessageStatus.FAILED
                    msg.last_error = "Recovered from PENDING after restart"
                    msg.updated_at = utcnow()
                    stats["recovered"] += 1
            
            # --- Phase 2: Clean up orphaned records ---
            orphaned = [m for m in all_messages
                       if m.track_id not in library_track_ids
                       and m.status != ChannelMessageStatus.PENDING]  # PENDING handled above
            for msg in orphaned:
                if msg.status == ChannelMessageStatus.SENT and msg.message_id:
                    try:
                        await use_bot.delete_message(
                            chat_id=channel.channel_id,
                            message_id=msg.message_id
                        )
                    except (TelegramBadRequest, TelegramForbiddenError) as e:
                        logger.debug(f"Could not delete message {msg.message_id}: {e}")
                
                await session.delete(msg)
                stats["deleted"] += 1
            
            if pending_messages or orphaned:
                await session.commit()
                # Rebuild messages_by_track after cleanup
                synced_result = await session.execute(
                    select(ChannelMessage).where(
                        ChannelMessage.channel_id == channel.id
                    )
                )
                all_messages = synced_result.scalars().all()
                messages_by_track = {msg.track_id: msg for msg in all_messages}
            
            # --- Phase 3 & 4: Build list of tracks that need sending ---
            # A track needs sending if:
            #   - No ChannelMessage record at all (new track)
            #   - ChannelMessage with status FAILED (retry)
            #   - ChannelMessage with status DELETED (re-send)
            tracks_to_send = []
            for track in tracks:
                msg = messages_by_track.get(track.id)
                if msg is None:
                    tracks_to_send.append(track)
                elif msg.status == ChannelMessageStatus.SENT:
                    stats["skipped"] += 1
                elif msg.status in (ChannelMessageStatus.FAILED, ChannelMessageStatus.DELETED):
                    if msg.status == ChannelMessageStatus.FAILED and msg.retry_count >= MAX_RETRY_COUNT:
                        stats["failed"] += 1  # Exhausted retries
                    else:
                        tracks_to_send.append(track)
                else:
                    stats["skipped"] += 1  # PENDING (shouldn't happen after Phase 1)
            
            to_sync_count = len(tracks_to_send)
            
            # Clear any previous cancel flag and set active sync
            self.clear_cancel_flag(user_id)
            self._update_sync_status(user_id, 0, to_sync_count, 0)
            
            try:
                sent_count = 0
                
                for i, track in enumerate(tracks_to_send):
                    # Check for cancellation
                    if self.is_sync_cancelled(user_id):
                        stats["cancelled"] = True
                        self.clear_cancel_flag(user_id)
                        break
                    
                    # Update sync status and call progress callback
                    self._update_sync_status(user_id, sent_count, to_sync_count, stats["synced"])
                    if progress_callback:
                        try:
                            await progress_callback(sent_count, to_sync_count, stats["synced"])
                        except:
                            pass
                    
                    # Use _forward_track_immediately which handles write-ahead
                    success = await self._forward_track_immediately(
                        user_id=user_id,
                        track_id=track.id,
                        bot=use_bot,
                    )
                    
                    if success:
                        stats["synced"] += 1
                    else:
                        stats["failed"] += 1
                    
                    sent_count += 1
                    
                    # Delay to avoid rate limiting
                    await asyncio.sleep(3)
                
                logger.info(f"Sync completed for user {user_id}: {stats}")
                return stats
            finally:
                # Always clear sync status when done
                self._clear_sync_status(user_id)
    
    async def pin_track_in_channel(
        self,
        user_id: int,
        track_id: int,
        bot: Optional[Bot] = None,
    ) -> bool:
        """
        Pin a track message in user's channel (called when track is liked).
        
        Args:
            user_id: User who owns the channel
            track_id: Track ID to pin
            bot: Bot instance for pinning
        
        Returns:
            True if pinned successfully, False otherwise
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for pinning")
            return False
        
        async with get_session() as session:
            # Get channel
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            
            if not channel:
                return False
            
            # Get channel message record for this track (must be SENT)
            result = await session.execute(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.track_id == track_id,
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                )
            )
            channel_message = result.scalar_one_or_none()
            
            if not channel_message or not channel_message.message_id:
                logger.debug(f"No SENT channel message for track {track_id}, can't pin")
                return False
            
            try:
                await use_bot.pin_chat_message(
                    chat_id=channel.channel_id,
                    message_id=channel_message.message_id,
                    disable_notification=True,
                )
                logger.info(f"Pinned track {track_id} in channel {channel.channel_id}")
                return True
                
            except TelegramForbiddenError:
                logger.warning(f"Bot removed from channel {channel.channel_id}")
                return False
            except TelegramBadRequest as e:
                if "message to pin not found" in str(e).lower():
                    channel_message.status = ChannelMessageStatus.DELETED
                    channel_message.last_error = "Message not found during pin"
                    channel_message.updated_at = utcnow()
                logger.warning(f"Failed to pin message in channel: {e}")
                return False
    
    async def unpin_track_in_channel(
        self,
        user_id: int,
        track_id: int,
        bot: Optional[Bot] = None,
    ) -> bool:
        """
        Unpin a track message in user's channel (called when track is unliked).
        
        Args:
            user_id: User who owns the channel
            track_id: Track ID to unpin
            bot: Bot instance for unpinning
        
        Returns:
            True if unpinned successfully, False otherwise
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for unpinning")
            return False
        
        async with get_session() as session:
            # Get channel
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            
            if not channel:
                return False
            
            # Get channel message record for this track (must be SENT)
            result = await session.execute(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.track_id == track_id,
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                )
            )
            channel_message = result.scalar_one_or_none()
            
            if not channel_message or not channel_message.message_id:
                logger.debug(f"No SENT channel message for track {track_id}, can't unpin")
                return False
            
            try:
                await use_bot.unpin_chat_message(
                    chat_id=channel.channel_id,
                    message_id=channel_message.message_id,
                )
                logger.info(f"Unpinned track {track_id} in channel {channel.channel_id}")
                return True
                
            except TelegramForbiddenError:
                logger.warning(f"Bot removed from channel {channel.channel_id}")
                return False
            except TelegramBadRequest as e:
                # Message might not be pinned or already deleted
                logger.warning(f"Failed to unpin message in channel: {e}")
                return False
    
    async def find_track_by_channel_message(
        self,
        telegram_channel_id: int,
        message_id: int,
    ) -> Optional[tuple[int, int]]:
        """
        Find track and user by a Telegram channel message.
        Used for reverse direction: pin in channel → like in library.
        
        Args:
            telegram_channel_id: Telegram channel ID (negative number)
            message_id: Telegram message ID that was pinned
        
        Returns:
            Tuple of (user_id, track_id) if found, None otherwise
        """
        async with get_session() as session:
            result = await session.execute(
                select(UserChannel, ChannelMessage)
                .join(ChannelMessage, ChannelMessage.channel_id == UserChannel.id)
                .where(
                    UserChannel.channel_id == telegram_channel_id,
                    UserChannel.is_active == True,
                    ChannelMessage.message_id == message_id,
                    ChannelMessage.status == ChannelMessageStatus.SENT,
                )
            )
            row = result.first()
            if row:
                channel, msg = row
                return (channel.user_id, msg.track_id)
            return None
    
    async def like_track_from_pin(
        self,
        user_id: int,
        track_id: int,
    ) -> bool:
        """
        Mark a track as liked in user's library (called when message is pinned in channel).
        
        Args:
            user_id: User ID
            track_id: Track ID
        
        Returns:
            True if liked successfully, False otherwise
        """
        from shared.models import UserLibrary
        
        async with get_session() as session:
            result = await session.execute(
                select(UserLibrary).where(
                    UserLibrary.user_id == user_id,
                    UserLibrary.track_id == track_id,
                )
            )
            entry = result.scalar_one_or_none()
            
            if not entry:
                logger.warning(f"Track {track_id} not in library for user {user_id}, can't like from pin")
                return False
            
            if entry.is_liked:
                logger.debug(f"Track {track_id} already liked by user {user_id}")
                return True
            
            entry.is_liked = True
            entry.liked_at = utcnow()
            await session.commit()
            
            logger.info(f"Track {track_id} liked from channel pin by user {user_id}")
            return True
    
    async def _find_max_message_id(
        self,
        bot: Bot,
        channel_id: int,
        buffer_chat_id: int,
    ) -> int:
        """
        Find the latest message_id in a channel by sending a temporary
        message and immediately deleting it. The returned message_id
        equals the current max in the channel.
        
        This is the only reliable way — Telegram Bot API has no
        getHistory/getMessages, and binary search fails on sparse channels.
        """
        try:
            # Send a dot message to the channel — its ID = current max
            tmp = await bot.send_message(
                chat_id=channel_id,
                text="⏳",
                disable_notification=True,
            )
            max_id = tmp.message_id
            # Delete immediately — user won't see it
            try:
                await bot.delete_message(chat_id=channel_id, message_id=tmp.message_id)
            except Exception:
                pass
            # The temp message consumed one ID, so actual content ends at max_id - 1
            logger.debug(f"Channel {channel_id}: max_msg_id = {max_id - 1}")
            return max_id - 1
        except TelegramForbiddenError:
            logger.error(f"Bot can't post to channel {channel_id} — not admin or no post rights")
            return 0
        except Exception as e:
            logger.error(f"Failed to determine max_msg_id for channel {channel_id}: {e}")
            return 0

    async def scan_channel(
        self,
        user_id: int,
        bot: Optional[Bot] = None,
        progress_callback=None,
    ) -> dict:
        """
        Scan Telegram channel to rebuild channel_messages index.
        
        Uses forward_message to read channel messages and match audio
        to library tracks by file_unique_id. Creates missing 
        ChannelMessage records.
        
        To avoid spamming the user's DM, forwards are sent to a buffer
        chat (configured via SCANNER_BUFFER_CHAT_ID). If not configured,
        falls back to user DM with disable_notification + batch delete.
        
        Args:
            user_id: User who owns the channel
            bot: Bot instance
            progress_callback: Optional async callback(scanned, found, restored)
            
        Returns:
            Dict with scan statistics
        """
        use_bot = bot or self.bot
        if not use_bot:
            return {"error": "Bot not initialized"}
        
        settings = get_settings()
        
        async with get_session() as session:
            # Get channel
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            if not channel:
                return {"error": "No channel configured"}
            
            # Get existing channel_message records
            existing_result = await session.execute(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id
                )
            )
            existing_messages = existing_result.scalars().all()
            known_message_ids = {msg.message_id for msg in existing_messages if msg.message_id}
            known_track_ids = {msg.track_id for msg in existing_messages}
            
            # Build file_unique_id -> Track mapping for user's library
            from shared.models import UserLibrary
            track_result = await session.execute(
                select(Track)
                .join(UserLibrary, UserLibrary.track_id == Track.id)
                .where(UserLibrary.user_id == user_id)
            )
            tracks = track_result.scalars().all()
            fuid_to_track = {t.file_unique_id: t for t in tracks}
            
            stats = {
                "scanned": 0,
                "audio_found": 0,
                "restored": 0,
                "already_known": 0,
                "not_in_library": 0,
                "failed": 0,
                "errors": 0,
            }
            
            # Determine where to forward messages for reading.
            # Buffer chat = no spam in user's DM; fallback = user DM (with silent mode).
            buffer_chat_id = settings.scanner_buffer_chat_id or user_id
            use_buffer = settings.scanner_buffer_chat_id != 0
            
            # --- Validate buffer chat access before starting ---
            if use_buffer:
                try:
                    await use_bot.get_chat(buffer_chat_id)
                except Exception as e:
                    logger.warning(
                        f"Buffer chat {buffer_chat_id} not accessible ({e}), "
                        f"falling back to user DM"
                    )
                    buffer_chat_id = user_id
                    use_buffer = False
            
            # --- Find the max message_id ---
            # Send a temp message to the channel and read its ID.
            max_msg_id = await self._find_max_message_id(
                use_bot, channel.channel_id, buffer_chat_id
            )
            
            logger.info(
                f"Starting channel scan for user {user_id}, "
                f"channel {channel.channel_id}, "
                f"max_msg_id={max_msg_id}, "
                f"buffer={'configured' if use_buffer else 'user DM'}"
            )
            
            if max_msg_id == 0:
                logger.info("Channel appears empty, nothing to scan")
                return stats
            
            # Collect forwarded message IDs for batch deletion
            pending_delete_ids: list[int] = []
            BATCH_DELETE_SIZE = 80
            
            # Adaptive throttling: start conservative, increase on rate limits
            scan_delay = 0.35  # ~3 req/sec — safe for Telegram
            MAX_RATE_LIMIT_WAIT = 120  # Abort scan if Telegram says wait > 2 min
            MAX_TIMEOUT_RETRIES = 2  # Retry timeout errors up to N times
            
            # Clear cancel flag
            self.clear_cancel_flag(user_id)
            
            for message_id in range(1, max_msg_id + 1):
                
                # Check for cancellation
                if self.is_sync_cancelled(user_id):
                    self.clear_cancel_flag(user_id)
                    stats["cancelled"] = True
                    break
                
                # Skip message IDs we already know about
                if message_id in known_message_ids:
                    stats["already_known"] += 1
                    continue
                
                # --- Forward with retry on timeout ---
                forwarded = None
                for attempt in range(1, MAX_TIMEOUT_RETRIES + 2):
                    try:
                        forwarded = await use_bot.forward_message(
                            chat_id=buffer_chat_id,
                            from_chat_id=channel.channel_id,
                            message_id=message_id,
                            disable_notification=True,
                        )
                        break  # Success
                        
                    except TelegramBadRequest as e:
                        error_text = str(e).lower()
                        if "chat not found" in error_text or "chat_not_found" in error_text:
                            if use_buffer:
                                logger.error(
                                    f"Buffer chat {buffer_chat_id} lost during scan, "
                                    f"falling back to user DM"
                                )
                                buffer_chat_id = user_id
                                use_buffer = False
                                break  # Will retry in next attempt
                            else:
                                stats["error"] = "Не удалось переслать: чат не найден"
                                break
                        elif "message to forward not found" in error_text or "message not found" in error_text:
                            break  # Normal — message deleted or doesn't exist
                        elif "can't be forwarded" in error_text or "cannot be forwarded" in error_text:
                            stats["scanned"] += 1  # Service message
                            break
                        else:
                            stats["errors"] += 1
                            logger.warning(f"Scan error at msg {message_id}: {e}")
                            break
                            
                    except TelegramRetryAfter as e:
                        if e.retry_after > MAX_RATE_LIMIT_WAIT:
                            logger.error(
                                f"Rate limit too long ({e.retry_after}s > {MAX_RATE_LIMIT_WAIT}s), "
                                f"aborting scan at msg {message_id}/{max_msg_id}"
                            )
                            stats["error"] = f"Rate limit {e.retry_after}s — скан остановлен"
                            # Notify user
                            if progress_callback:
                                try:
                                    await progress_callback(
                                        stats["scanned"],
                                        stats["audio_found"],
                                        stats["restored"],
                                        message_id,
                                        max_msg_id,
                                    )
                                except Exception:
                                    pass
                            break
                        logger.warning(f"Rate limited during scan, waiting {e.retry_after}s")
                        await asyncio.sleep(e.retry_after + 1)
                        # Slow down after rate limit
                        scan_delay = min(scan_delay * 2, 2.0)
                        logger.info(f"Increased scan delay to {scan_delay:.2f}s")
                        continue  # Retry same message
                        
                    except TelegramForbiddenError:
                        stats["error"] = "Bot lost access to channel"
                        break
                        
                    except Exception as e:
                        if attempt <= MAX_TIMEOUT_RETRIES:
                            logger.warning(
                                f"Scan error at msg {message_id} (attempt {attempt}/"
                                f"{MAX_TIMEOUT_RETRIES + 1}): {e}, retrying..."
                            )
                            await asyncio.sleep(2 * attempt)
                            continue
                        stats["errors"] += 1
                        logger.error(f"Scan error at msg {message_id} after {attempt} attempts: {e}")
                        break
                
                # Abort on fatal errors
                if "error" in stats and isinstance(stats.get("error"), str):
                    break
                
                # Process forwarded message
                if forwarded:
                    stats["scanned"] += 1
                    pending_delete_ids.append(forwarded.message_id)
                    
                    if forwarded.audio:
                        stats["audio_found"] += 1
                        file_unique_id = forwarded.audio.file_unique_id
                        
                        if file_unique_id in fuid_to_track:
                            track = fuid_to_track[file_unique_id]
                            
                            if track.id in known_track_ids:
                                stats["already_known"] += 1
                            else:
                                channel_message = ChannelMessage(
                                    channel_id=channel.id,
                                    track_id=track.id,
                                    message_id=message_id,
                                    status=ChannelMessageStatus.SENT,
                                )
                                session.add(channel_message)
                                known_track_ids.add(track.id)
                                known_message_ids.add(message_id)
                                stats["restored"] += 1
                                
                                if stats["restored"] % 50 == 0:
                                    await session.commit()
                        else:
                            stats["not_in_library"] += 1
                    
                    # Batch-delete forwarded copies
                    if len(pending_delete_ids) >= BATCH_DELETE_SIZE:
                        try:
                            await use_bot.delete_messages(
                                chat_id=buffer_chat_id,
                                message_ids=pending_delete_ids,
                            )
                        except Exception:
                            pass
                        pending_delete_ids.clear()
                
                # Progress callback
                msg_count = stats["scanned"] + stats["errors"]
                if progress_callback and (msg_count % 100 == 0 or message_id % 500 == 0):
                    try:
                        await progress_callback(
                            stats["scanned"],
                            stats["audio_found"],
                            stats["restored"],
                            message_id,
                            max_msg_id,
                        )
                    except Exception:
                        pass
                
                # Adaptive delay
                await asyncio.sleep(scan_delay)
            
            # Delete remaining forwarded copies
            if pending_delete_ids:
                try:
                    await use_bot.delete_messages(
                        chat_id=buffer_chat_id,
                        message_ids=pending_delete_ids,
                    )
                except Exception:
                    pass
            
            # Final commit
            await session.commit()
            
            logger.info(
                f"Channel scan completed for user {user_id}: "
                f"scanned={stats['scanned']}, audio={stats['audio_found']}, "
                f"restored={stats['restored']}, already_known={stats['already_known']}"
            )
            
            # Final progress callback
            if progress_callback:
                try:
                    await progress_callback(
                        stats["scanned"],
                        stats["audio_found"],
                        stats["restored"],
                        max_msg_id,
                        max_msg_id,
                    )
                except Exception:
                    pass
            
            return stats

    async def verify_channel_access(
        self,
        channel_id: int,
        bot: Optional[Bot] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Verify bot has access to send messages to channel.
        
        Returns:
            (success, channel_title, error_message)
        """
        use_bot = bot or self.bot
        if not use_bot:
            return False, None, "Bot not initialized"
        
        try:
            chat = await use_bot.get_chat(channel_id)
            
            # Check if bot can post
            member = await use_bot.get_chat_member(channel_id, use_bot.id)
            
            if member.status not in ("administrator", "creator"):
                return False, chat.title, "Бот должен быть администратором канала"
            
            return True, chat.title, None
            
        except TelegramForbiddenError:
            return False, None, "Бот не имеет доступа к каналу"
        except TelegramBadRequest as e:
            return False, None, f"Ошибка: {e}"

    async def delete_track_from_channel(
        self,
        user_id: int,
        track_id: int,
        bot: Optional[Bot] = None,
    ) -> bool:
        """
        Delete a track message from user's channel.
        Marks ChannelMessage as DELETED (preserves audit trail).
        
        Args:
            user_id: User who owns the channel
            track_id: Track ID to delete
            bot: Bot instance for deleting
        
        Returns:
            True if deleted successfully, False otherwise
        """
        use_bot = bot or self.bot
        if not use_bot:
            logger.error("No bot instance available for deleting")
            return False
        
        async with get_session() as session:
            # Get channel
            channel = await session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.is_active == True
                )
            )
            
            if not channel:
                return False
            
            # Get channel message record (any status that has a message_id)
            result = await session.execute(
                select(ChannelMessage).where(
                    ChannelMessage.channel_id == channel.id,
                    ChannelMessage.track_id == track_id
                )
            )
            channel_message = result.scalar_one_or_none()
            
            if not channel_message:
                # No record at all
                return False
            
            if channel_message.status == ChannelMessageStatus.DELETED:
                # Already marked deleted
                return True
            
            # Try to delete from Telegram if we have a message_id
            if channel_message.message_id:
                try:
                    await use_bot.delete_message(
                        chat_id=channel.channel_id,
                        message_id=channel_message.message_id
                    )
                except TelegramForbiddenError:
                    logger.warning(f"Bot removed from channel {channel.channel_id}")
                    return False
                except TelegramBadRequest as e:
                    if "message to delete not found" not in str(e).lower():
                        logger.error(f"Failed to delete message from channel: {e}")
                        return False
                    # Message already gone — that's fine, mark DELETED below
            
            # Mark as DELETED
            channel_message.status = ChannelMessageStatus.DELETED
            channel_message.last_error = "Deleted by user"
            channel_message.updated_at = utcnow()
            await session.commit()
            
            logger.info(f"Track {track_id} message marked DELETED in channel {channel.channel_id}")
            return True


# Global singleton instance
channel_service: ChannelService = ChannelService()


def init_channel_service(bot: Bot) -> ChannelService:
    """Initialize channel service with bot instance"""
    global channel_service
    channel_service.set_bot(bot)
    logger.info("Channel service initialized with bot")
    return channel_service


async def start_channel_service():
    """Start channel service background workers (queue worker)"""
    await channel_service.start_queue_worker()


async def stop_channel_service():
    """Stop channel service background workers"""
    await channel_service.stop_queue_worker()


def get_channel_service() -> ChannelService:
    """Get channel service instance"""
    return channel_service
