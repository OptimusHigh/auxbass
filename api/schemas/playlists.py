"""
TG Player API - Playlist Schemas
"""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

from api.schemas.common import PaginatedResponse
from api.schemas.tracks import TrackResponse


class PlaylistCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_public: bool = True  # Default to public


class PlaylistUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None
    custom_cover_url: Optional[str] = None


class PlaylistResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    track_count: int = 0
    unavailable_track_count: int = 0
    total_duration: int = 0
    cover_url: Optional[str] = None
    custom_cover_url: Optional[str] = None
    covers: List[str] = []  # Array of cover URLs for collage display
    tags: Optional[List[str]] = None  # Tags aggregated from playlist tracks
    is_public: bool = False
    is_system: bool = False
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    is_subscribed: bool = False  # Whether current user is subscribed
    is_owner: bool = False  # Whether current user is the owner
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PlaylistDetailResponse(PlaylistResponse):
    tracks: List[TrackResponse]


class PlaylistsListResponse(PaginatedResponse):
    items: List[PlaylistResponse]


class AddTrackRequest(BaseModel):
    track_id: int


class ReorderRequest(BaseModel):
    track_ids: List[int]
