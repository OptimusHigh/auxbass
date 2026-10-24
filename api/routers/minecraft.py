"""
Minecraft integration router: /api/minecraft/*
- Service auth via X-Service-Token (minecraft_service_token) or allow empty in dev
- ensure-user, playlist, manifest, packs
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.config import get_settings
from shared.database import get_db
from shared.models import Playlist, PlaylistTrack, Track, User
from api.services.minecraft_playlist import get_or_create_system_playlist, ensure_user_and_system_playlist
from api.services.minecraft_pack import pack_uuid_for_track, get_cached_sha1, ensure_pack_for_track, pack_filename

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


def verify_service_token(x_service_token: Optional[str] = Header(default=None)):
    """If minecraft_service_token is set, require it. Otherwise allow (dev)."""
    expected = (settings.minecraft_service_token or "").strip()
    if not expected:
        return
    if not x_service_token or x_service_token != expected:
        raise HTTPException(status_code=401, detail="Invalid service token")


class EnsureUserRequest(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None


class TrackManifestItem(BaseModel):
    track_id: int
    title: str
    artist: Optional[str] = None
    duration: Optional[int] = None
    file_id: Optional[str] = None
    pack_uuid: str
    pack_url: str
    sha1: Optional[str] = None


class PlaylistManifestResponse(BaseModel):
    playlist_id: int
    playlist_name: str
    is_system: bool
    tracks: List[TrackManifestItem]


def _pack_base_url(request: Request) -> str:
    # Prefer explicit config, else derive from request
    base = (settings.minecraft_pack_base_url or "").strip().rstrip("/")
    if base:
        return base
    # Fallback to api_url or request base
    api_url = (settings.api_url or "").strip().rstrip("/")
    if api_url and api_url.startswith("http"):
        return api_url
    # Last resort: request base
    return str(request.base_url).rstrip("/")


@router.post("/ensure-user", dependencies=[Depends(verify_service_token)])
async def ensure_user(
    data: EnsureUserRequest,
    db: AsyncSession = Depends(get_db),
):
    """Idempotent: ensure User + system playlist exist. Returns playlist id."""
    user, pl = await ensure_user_and_system_playlist(
        telegram_id=data.telegram_id,
        username=data.username,
        first_name=data.first_name,
        db=db,
    )
    await db.commit()
    return {
        "user_id": user.id,
        "playlist_id": pl.id,
        "playlist_name": pl.name,
        "is_system": True,
    }


@router.get("/playlist/{telegram_id}", dependencies=[Depends(verify_service_token)])
async def get_minecraft_playlist(
    telegram_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Get system playlist manifest for a Telegram user.
    Auto-creates User + system playlist if not exists (so ganj4core gets empty playlist for new players).
    """
    # Auto-create if missing
    user, pl = await ensure_user_and_system_playlist(telegram_id=telegram_id, db=db)
    await db.flush()

    # Load tracks with Track
    result = await db.execute(
        select(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == pl.id)
        .options(selectinload(PlaylistTrack.track))
        .order_by(PlaylistTrack.position)
    )
    pts = result.scalars().all()

    base_url = _pack_base_url(request)
    tracks: List[TrackManifestItem] = []
    for pt in pts:
        tr: Track = pt.track
        if not tr:
            continue
        # Skip unavailable/chunk? Keep but mark
        puuid = str(pack_uuid_for_track(tr.id))
        sha1 = get_cached_sha1(tr.id)
        # Build pack URL: GET /api/minecraft/packs/{track_id}.zip
        pack_url = f"{base_url}/api/minecraft/packs/{tr.id}.zip"
        tracks.append(TrackManifestItem(
            track_id=tr.id,
            title=tr.title or f"Track {tr.id}",
            artist=tr.artist,
            duration=tr.duration,
            file_id=tr.file_id,
            pack_uuid=puuid,
            pack_url=pack_url,
            sha1=sha1,
        ))

    await db.commit()
    return PlaylistManifestResponse(
        playlist_id=pl.id,
        playlist_name=pl.name,
        is_system=True,
        tracks=tracks,
    )


@router.get("/packs/{track_id}.zip")
async def get_pack_zip(
    track_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Serve micro-pack zip for a track. Builds on-demand if not cached.
    No service token required - pack URL is public but unguessable via sha1; client caches by sha1.
    We still allow ganj4core to fetch without auth for simplicity (pack URL is per-track).
    """
    # Check if track exists
    track = await db.get(Track, track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.file_id or track.file_id.startswith("lazy:") or track.file_id.startswith("pending:"):
        raise HTTPException(status_code=404, detail="Track file not ready")

    zpath = pack_filename(track_id)
    sha1 = get_cached_sha1(track_id)

    # If cached, serve directly
    if zpath.exists() and sha1:
        return FileResponse(
            path=zpath,
            media_type="application/zip",
            filename=f"track_{track_id}.zip",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": f'"{sha1}"',
                "X-Pack-UUID": str(pack_uuid_for_track(track_id)),
            },
        )

    # Build on-demand (may take a few seconds: download + ffmpeg)
    result = await ensure_pack_for_track(track_id, track.file_id, title=track.title or "")
    if not result:
        raise HTTPException(status_code=502, detail="Failed to build pack (download/transcode error)")

    zip_bytes, new_sha1 = result
    # Serve from disk (now cached)
    return FileResponse(
        path=zpath,
        media_type="application/zip",
        filename=f"track_{track_id}.zip",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{new_sha1}"',
            "X-Pack-UUID": str(pack_uuid_for_track(track_id)),
        },
    )


@router.get("/manifest", dependencies=[Depends(verify_service_token)])
async def get_manifest_batch(
    request: Request,
    telegram_ids: str = "",
    db: AsyncSession = Depends(get_db),
):
    """
    Batch manifest for multiple telegram_ids (comma-separated).
    Used on JOIN to pre-push only not-yet-sent packs.
    Query: ?telegram_ids=123,456,789
    """
    if not telegram_ids.strip():
        return {"playlists": []}
    try:
        ids = [int(x.strip()) for x in telegram_ids.split(",") if x.strip().isdigit()]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid telegram_ids")

    base_url = _pack_base_url(request)
    out = []
    for tid in ids[:100]:  # limit
        user, pl = await ensure_user_and_system_playlist(telegram_id=tid, db=db)
        await db.flush()
        result = await db.execute(
            select(PlaylistTrack).where(PlaylistTrack.playlist_id == pl.id).options(selectinload(PlaylistTrack.track)).order_by(PlaylistTrack.position)
        )
        pts = result.scalars().all()
        tracks = []
        for pt in pts:
            tr = pt.track
            if not tr:
                continue
            tracks.append({
                "track_id": tr.id,
                "title": tr.title,
                "artist": tr.artist,
                "duration": tr.duration,
                "pack_uuid": str(pack_uuid_for_track(tr.id)),
                "pack_url": f"{base_url}/api/minecraft/packs/{tr.id}.zip",
                "sha1": get_cached_sha1(tr.id),
            })
        out.append({"telegram_id": tid, "playlist_id": pl.id, "tracks": tracks})

    await db.commit()
    return {"playlists": out}
