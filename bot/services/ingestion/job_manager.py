"""
TG Player - Ingestion Job Manager
Tracks status and progress of asynchronous background import jobs.
"""
import uuid
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class JobStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class IngestionJob:
    """Represents a background audio import job."""
    id: str
    user_id: int
    url: str
    provider_name: str
    entity_type: str
    title: str
    author: Optional[str] = None
    cover_url: Optional[str] = None
    total_tracks: int = 1
    processed_tracks: int = 0
    skipped_tracks: int = 0
    failed_tracks: int = 0
    current_track_title: Optional[str] = None
    current_step: Optional[str] = None
    download_percent: Optional[int] = None
    status: JobStatus = JobStatus.PENDING
    error_message: Optional[str] = None
    playlist_id: Optional[int] = None
    album_id: Optional[int] = None
    imported_track_ids: List[int] = field(default_factory=list)
    uploaded_chat_id: Optional[int] = None
    uploaded_message_id: Optional[int] = None
    selected_urls: Optional[List[str]] = None
    custom_tracks: Optional[List[Dict[str, Any]]] = None
    raw_data: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def progress_percent(self) -> int:
        if self.total_tracks <= 0:
            return 100 if self.status == JobStatus.COMPLETED else 0
        return min(100, int((self.processed_tracks / self.total_tracks) * 100))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "provider_name": self.provider_name,
            "entity_type": self.entity_type,
            "title": self.title,
            "author": self.author,
            "cover_url": self.cover_url,
            "total_tracks": self.total_tracks,
            "processed_tracks": self.processed_tracks,
            "skipped_tracks": self.skipped_tracks,
            "failed_tracks": self.failed_tracks,
            "current_track_title": self.current_track_title,
            "current_step": self.current_step,
            "download_percent": self.download_percent,
            "status": self.status.value,
            "progress_percent": self.progress_percent,
            "error_message": self.error_message,
            "playlist_id": self.playlist_id,
            "album_id": self.album_id,
            "imported_track_ids": self.imported_track_ids,
            "selected_urls": self.selected_urls,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class IngestionJobManager:
    """Manages active and historical ingestion jobs."""

    def __init__(self, max_history_per_user: int = 20, max_concurrent_downloads: int = 1):
        self._jobs: Dict[str, IngestionJob] = {}
        self._user_jobs: Dict[int, List[str]] = {}
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._max_history = max_history_per_user
        # Global semaphore to limit heavy yt-dlp & ffmpeg processes to protect 1-vCPU server
        self._download_semaphore = asyncio.Semaphore(max_concurrent_downloads)

    @property
    def download_semaphore(self) -> asyncio.Semaphore:
        """Global concurrency limiter for heavy audio downloads & transcoding."""
        return self._download_semaphore

    async def create_job(
        self,
        user_id: int,
        url: str,
        provider_name: str,
        entity_type: str,
        title: str,
        total_tracks: int = 1,
        author: Optional[str] = None,
        cover_url: Optional[str] = None,
        selected_urls: Optional[List[str]] = None,
        custom_tracks: Optional[List[Dict[str, Any]]] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> IngestionJob:
        """Create and register a new pending job."""
        job_id = uuid.uuid4().hex
        job = IngestionJob(
            id=job_id,
            user_id=user_id,
            url=url,
            provider_name=provider_name,
            entity_type=entity_type,
            title=title,
            author=author,
            cover_url=cover_url,
            total_tracks=total_tracks,
            selected_urls=selected_urls,
            custom_tracks=custom_tracks,
            raw_data=raw_data,
        )

        async with self._lock:
            self._jobs[job_id] = job
            if user_id not in self._user_jobs:
                self._user_jobs[user_id] = []
            self._user_jobs[user_id].insert(0, job_id)
            if len(self._user_jobs[user_id]) > self._max_history:
                old_id = self._user_jobs[user_id].pop()
                self._jobs.pop(old_id, None)

        return job

    def get_job(self, job_id: str) -> Optional[IngestionJob]:
        return self._jobs.get(job_id)

    def get_user_jobs(self, user_id: int, limit: int = 10) -> List[IngestionJob]:
        job_ids = self._user_jobs.get(user_id, [])[:limit]
        return [self._jobs[jid] for jid in job_ids if jid in self._jobs]

    def register_task(self, job_id: str, task: asyncio.Task):
        self._active_tasks[job_id] = task

    def cancel_job(self, job_id: str) -> bool:
        task = self._active_tasks.get(job_id)
        job = self._jobs.get(job_id)
        if job:
            job.status = JobStatus.CANCELLED
            job.updated_at = datetime.now(timezone.utc)
        if task and not task.done():
            task.cancel()
            return True
        return False


# Global singleton instance
job_manager = IngestionJobManager()
