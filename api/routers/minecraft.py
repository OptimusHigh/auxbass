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
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.config import get_settings
from shared.database import get_db
from shared.models import Playlist, PlaylistTrack, Track, User
from api.services.minecraft_playlist import get_or_create_system_playlist, ensure_user_and_system_playlist
from api.services.minecraft_pack import (
    pack_uuid_for_track,
    get_cached_sha1,
    ensure_pack_for_track,
    pack_filename,
    base_pack_uuid,
    get_base_pack_meta,
    get_base_pack_sha1,
    build_base_pack,
    BASE_PACK_FILENAME,
)

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


def _extract_cover_ref(tr) -> Optional[str]:
    img_id = getattr(tr, "image_file_id", None)
    if img_id:
        return img_id
    enr = tr.__dict__.get("enrichment")
    if enr and getattr(enr, "cover_url", None):
        return enr.cover_url
    return None


class EnsureUserRequest(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    rank: Optional[str] = None          # e.g. "player", "veteran" — from ganj4core RankManager
    max_tracks: Optional[int] = None    # from Rank.getMaxMusicTracks()


class UpdateRankRequest(BaseModel):
    telegram_id: int
    rank: str
    max_tracks: int


class ForwardTrackRequest(BaseModel):
    telegram_id: int
    file_id: str
    file_unique_id: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    duration: Optional[int] = None
    file_size: Optional[int] = None
    file_name: Optional[str] = None
    mime_type: Optional[str] = None


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
    """Idempotent: ensure User + system playlist exist. Returns playlist id.
    
    Optionally updates mc_rank / mc_max_tracks if provided by ganj4core.
    """
    user, pl = await ensure_user_and_system_playlist(
        telegram_id=data.telegram_id,
        username=data.username,
        first_name=data.first_name,
        db=db,
    )
    # Sync rank if provided
    if data.rank is not None:
        user.mc_rank = data.rank
    if data.max_tracks is not None:
        user.mc_max_tracks = data.max_tracks
    await db.commit()
    return {
        "user_id": user.id,
        "playlist_id": pl.id,
        "playlist_name": pl.name,
        "is_system": True,
        "mc_rank": user.mc_rank,
        "mc_max_tracks": user.mc_max_tracks,
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

    # Load tracks with Track and TrackEnrichment
    result = await db.execute(
        select(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == pl.id)
        .options(selectinload(PlaylistTrack.track).selectinload(Track.enrichment))
        .order_by(PlaylistTrack.position)
    )
    pts = result.scalars().all()

    base_url = _pack_base_url(request)
    tracks: List[TrackManifestItem] = []
    for pt in pts:
        tr: Track = pt.track
        if not tr:
            continue
        puuid = str(pack_uuid_for_track(tr.id))
        sha1 = get_cached_sha1(tr.id)
        if not sha1 and tr.file_id and not tr.file_id.startswith("lazy:") and not tr.file_id.startswith("pending:"):
            # Warm up pack in background without blocking the playlist response
            import asyncio
            asyncio.create_task(ensure_pack_for_track(
                tr.id, tr.file_id, tr.title or "", 
                duration=tr.duration, 
                cover_ref=_extract_cover_ref(tr)
            ))
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
    # Check if track exists with enrichment
    stmt = select(Track).options(selectinload(Track.enrichment)).where(Track.id == track_id)
    track = (await db.execute(stmt)).scalar_one_or_none()
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
    result = await ensure_pack_for_track(
        track_id, track.file_id, 
        title=track.title or "", 
        duration=track.duration, 
        cover_ref=_extract_cover_ref(track)
    )
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


def _minecraft_tracks_stmt():
    """
    Returns query for all tracks currently added to any player's Minecraft disc playlist (is_system=True).
    Filters out lazy/pending placeholders.
    """
    return (
        select(Track)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
        .where(Playlist.is_system == True)  # noqa: E712
        .where(Track.file_id.isnot(None))
        .where(~Track.file_id.startswith("lazy:"))
        .where(~Track.file_id.startswith("pending:"))
        .options(selectinload(Track.enrichment))
        .distinct()
        .order_by(Track.id)
    )


@router.get("/packs/base.zip")
async def get_base_pack_zip(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Serve master Base Music Pack. If missing, automatically builds it from all available tracks.
    """
    sha1 = get_base_pack_sha1()
    if not BASE_PACK_FILENAME.exists() or not sha1:
        tracks_db = (await db.execute(_minecraft_tracks_stmt())).scalars().all()
        track_items = [
            {
                "id": t.id,
                "file_id": t.file_id,
                "title": t.title or f"Track {t.id}",
                "duration": t.duration,
                "cover_ref": _extract_cover_ref(t),
            }
            for t in tracks_db
        ]
        res = await build_base_pack(track_items)
        if not res:
            raise HTTPException(status_code=503, detail="Base pack is empty or build failed")
        _, sha1, _ = res

    return FileResponse(
        path=BASE_PACK_FILENAME,
        media_type="application/zip",
        filename="ganjacraft_base_music.zip",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{sha1}"',
            "X-Pack-UUID": str(base_pack_uuid()),
        },
    )


@router.get("/sync-manifest", dependencies=[Depends(verify_service_token)])
async def get_sync_manifest(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns Base Pack metadata + list of Delta micro-packs (tracks not yet baked into Base Pack).
    Used on JOIN to deliver 100% music content with minimal bandwidth.
    """
    base_meta = get_base_pack_meta()
    base_track_ids = set(base_meta.get("track_ids", []))
    base_sha1 = get_base_pack_sha1()
    base_url = _pack_base_url(request)

    # Only tracks in Minecraft playlists
    all_tracks = (await db.execute(_minecraft_tracks_stmt())).scalars().all()

    # If base pack does not exist at all, build it right now so players get base pack immediately
    if (not BASE_PACK_FILENAME.exists() or not base_sha1) and all_tracks:
        track_items = [
            {
                "id": t.id,
                "file_id": t.file_id,
                "title": t.title or f"Track {t.id}",
                "duration": t.duration,
                "cover_ref": _extract_cover_ref(t),
            }
            for t in all_tracks
        ]
        res = await build_base_pack(track_items)
        if res:
            _, base_sha1, inc_ids = res
            base_track_ids = set(inc_ids)

    base_pack_info = None
    if BASE_PACK_FILENAME.exists() and base_sha1:
        base_pack_info = {
            "uuid": str(base_pack_uuid()),
            "url": f"{base_url}/api/minecraft/packs/base.zip",
            "sha1": base_sha1,
            "track_ids": list(base_track_ids),
            "track_count": len(base_track_ids),
        }

    deltas = []
    for tr in all_tracks:
        if tr.id not in base_track_ids:
            sha1 = get_cached_sha1(tr.id)
            if not sha1:
                import asyncio
                asyncio.create_task(ensure_pack_for_track(
                    tr.id, tr.file_id, tr.title or "",
                    duration=tr.duration,
                    cover_ref=_extract_cover_ref(tr)
                ))
            deltas.append({
                "track_id": tr.id,
                "title": tr.title or f"Track {tr.id}",
                "artist": tr.artist,
                "duration": tr.duration,
                "file_id": tr.file_id,
                "pack_uuid": str(pack_uuid_for_track(tr.id)),
                "pack_url": f"{base_url}/api/minecraft/packs/{tr.id}.zip",
                "sha1": sha1,
            })

    return {
        "base_pack": base_pack_info,
        "deltas": deltas,
        "total_tracks": len(all_tracks),
    }


@router.post("/rebuild-base-pack", dependencies=[Depends(verify_service_token)])
async def rebuild_base_pack_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Consolidate all tracks into the master Base Pack (Rollup/Rebase).
    Clears out deltas and updates base_pack.sha1.
    """
    all_tracks = (await db.execute(_minecraft_tracks_stmt())).scalars().all()
    if not all_tracks:
        return {"status": "ok", "message": "No Minecraft tracks in DB"}

    track_items = [
        {
            "id": t.id,
            "file_id": t.file_id,
            "title": t.title or f"Track {t.id}",
            "duration": t.duration,
            "cover_ref": _extract_cover_ref(t),
        }
        for t in all_tracks
    ]
    res = await build_base_pack(track_items)
    if not res:
        raise HTTPException(status_code=500, detail="Failed to rebuild base pack")
    _, sha1, included = res
    return {
        "status": "ok",
        "sha1": sha1,
        "track_count": len(included),
        "uuid": str(base_pack_uuid()),
    }



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
            sha1 = get_cached_sha1(tr.id)
            if not sha1 and tr.file_id and not tr.file_id.startswith("lazy:") and not tr.file_id.startswith("pending:"):
                try:
                    res = await ensure_pack_for_track(
                        tr.id, tr.file_id, tr.title or "", 
                        duration=tr.duration, 
                        image_file_id=_extract_image_file_id(tr)
                    )
                    if res:
                        _, sha1 = res
                except Exception as e:
                    logger.warning(f"Failed to pre-ensure pack for track {tr.id}: {e}")
            tracks.append({
                "track_id": tr.id,
                "title": tr.title,
                "artist": tr.artist,
                "duration": tr.duration,
                "pack_uuid": str(pack_uuid_for_track(tr.id)),
                "pack_url": f"{base_url}/api/minecraft/packs/{tr.id}.zip",
                "sha1": sha1,
            })
        out.append({"telegram_id": tid, "playlist_id": pl.id, "tracks": tracks})

    await db.commit()
    return {"playlists": out}


@router.post("/update-rank", dependencies=[Depends(verify_service_token)])
async def update_rank(
    data: UpdateRankRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update mc_rank and mc_max_tracks for a user.

    Called by ganj4core on rank purchase (purchaseRank / checkAutoPromoteNoob).
    """
    from sqlalchemy import update as sql_update
    result = await db.execute(
        sql_update(User)
        .where(User.id == data.telegram_id)
        .values(mc_rank=data.rank, mc_max_tracks=data.max_tracks)
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info(f"[minecraft] updated rank for telegram_id={data.telegram_id} rank={data.rank} max_tracks={data.max_tracks}")
    return {"status": "ok", "telegram_id": data.telegram_id, "rank": data.rank, "max_tracks": data.max_tracks}


@router.get("/quota/{telegram_id}", dependencies=[Depends(verify_service_token)])
async def get_quota(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return current track usage and limit for a user's system playlist.

    Used by ganj4core and Vue frontend (via /api/minecraft/quota/{telegram_id}).
    """
    user = await db.get(User, telegram_id)
    if not user:
        return {"current": 0, "max": 5, "rank": "noob", "rank_display": "Новичок"}

    # Get system playlist
    pl_result = await db.execute(
        select(Playlist).where(Playlist.owner_id == telegram_id, Playlist.is_system == True)  # noqa: E712
    )
    pl = pl_result.scalar_one_or_none()
    current = 0
    if pl:
        count_result = await db.execute(
            select(func.count()).where(PlaylistTrack.playlist_id == pl.id)
        )
        current = count_result.scalar() or 0

    rank_display_map = {
        "noob": "Новичок", "player": "Игрок", "veteran": "Ветеран",
        "pro": "Про", "elite": "Элита", "legend": "Легенда",
    }
    rank = user.mc_rank or "noob"
    return {
        "current": current,
        "max": user.mc_max_tracks or 5,
        "rank": rank,
        "rank_display": rank_display_map.get(rank, rank.capitalize()),
    }


@router.post("/track/forward", dependencies=[Depends(verify_service_token)])
async def forward_track_to_minecraft_playlist(
    data: ForwardTrackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Fast-track endpoint: accepts an audio file forwarded via Telegram bot
    and adds it directly to user's system "Minecraft Media" playlist.
    """
    import asyncio
    from shared.models import utcnow, LibrarySource
    from bot.services import track_service

    # Ensure user & system playlist exist
    user, pl = await ensure_user_and_system_playlist(telegram_id=data.telegram_id, db=db)

    # Check quota
    count_res = await db.execute(
        select(func.count()).where(PlaylistTrack.playlist_id == pl.id)
    )
    current_count = count_res.scalar() or 0
    max_tracks = getattr(user, "mc_max_tracks", 5) or 5
    if current_count >= max_tracks:
        rank_id = getattr(user, "mc_rank", "noob") or "noob"
        rank_names = {
            "noob": "Новичок", "player": "Игрок", "veteran": "Ветеран",
            "pro": "Про", "elite": "Элита", "legend": "Легенда",
        }
        rank_name = rank_names.get(rank_id, rank_id.capitalize())
        raise HTTPException(
            status_code=403,
            detail=f"Лимит пластинок ранга «{rank_name}» исчерпан ({max_tracks} шт.). Повышение: /ranks",
        )

    # Save track via standard track_service pipeline
    unique_id = data.file_unique_id or f"tg_fwd_{data.file_id[:16]}"
    res = await track_service.save_track(
        user_id=data.telegram_id,
        file_id=data.file_id,
        file_unique_id=unique_id,
        title=data.title,
        artist=data.artist,
        duration=data.duration,
        file_size=data.file_size,
        file_name=data.file_name,
        mime_type=data.mime_type,
        library_source=LibrarySource.UPLOADED,
        enrich=True,
    )
    track_id = res.track_id

    # Check if already in system playlist
    existing_pt = await db.execute(
        select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == pl.id,
            PlaylistTrack.track_id == track_id,
        )
    )
    if existing_pt.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Трек уже добавлен в пластинки",
        )

    # Add to playlist
    max_pos = await db.scalar(
        select(func.coalesce(func.max(PlaylistTrack.position), 0))
        .where(PlaylistTrack.playlist_id == pl.id)
    ) or 0

    pt = PlaylistTrack(
        playlist_id=pl.id,
        track_id=track_id,
        position=max_pos + 1,
    )
    pl.updated_at = utcnow()
    db.add(pt)
    await db.commit()

    # Trigger background micro-pack compilation
    title = data.title or (data.file_name.rsplit(".", 1)[0] if data.file_name else "Track")
    asyncio.create_task(
        ensure_pack_for_track(
            track_id,
            data.file_id,
            title,
            duration=data.duration,
        )
    )

    logger.info(
        f"[minecraft] added fast-track {track_id} to system playlist {pl.id} for user {data.telegram_id} ({current_count + 1}/{max_tracks})"
    )

    return {
        "status": "ok",
        "track_id": track_id,
        "title": title,
        "artist": data.artist or "Неизвестный исполнитель",
        "current": current_count + 1,
        "max": max_tracks,
    }

