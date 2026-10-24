"""
Minecraft micro-pack service: 1 track = 1 cacheable zip.
- Deterministic UUID per track_id
- ffmpeg mono ogg (OpenAL attenuation requires mono)
- pack_format 97 for 26.3 (resource_major from version.json)
- sha1 for ClientboundResourcePackPushPacket caching
"""
import asyncio
import hashlib
import io
import json
import logging
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from shared.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Verified from server-26.3.jar version.json: resource_major 97
PACK_FORMAT_MAJOR = 97
PACK_FORMAT_MINOR = 1
PACK_DESCRIPTION = "GanjaCraft Media"

# Disk cache for built zips: data/minecraft_packs/{track_id}.zip + .sha1
PACKS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "minecraft_packs"
PACKS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory sha1 cache
_sha1_cache: dict[int, str] = {}


def pack_uuid_for_track(track_id: int) -> uuid.UUID:
    """
    Deterministic UUID matching Java's java.util.UUID.nameUUIDFromBytes.
    Uses MD5 with version 3 and variant 2.
    """
    data = f"ganjacraft_track_{track_id}".encode("utf-8")
    md5_bytes = bytearray(hashlib.md5(data).digest())
    md5_bytes[6] = (md5_bytes[6] & 0x0f) | 0x30
    md5_bytes[8] = (md5_bytes[8] & 0x3f) | 0x80
    return uuid.UUID(bytes=bytes(md5_bytes))


def pack_filename(track_id: int) -> Path:
    return PACKS_DIR / f"{track_id}.zip"


def sha1_path(track_id: int) -> Path:
    return PACKS_DIR / f"{track_id}.sha1"


def get_cached_sha1(track_id: int) -> Optional[str]:
    if track_id in _sha1_cache:
        return _sha1_cache[track_id]
    p = sha1_path(track_id)
    if p.exists():
        try:
            v = p.read_text(encoding="utf-8").strip()
            if v:
                _sha1_cache[track_id] = v
                return v
        except Exception:
            pass
    return None


def _compute_sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _build_zip_bytes(track_id: int, ogg_bytes: bytes, title: str = "") -> bytes:
    """
    Build micro-pack zip in memory.
    Structure:
      pack.mcmeta: {"pack":{"pack_format":97,"description":"GanjaCraft Media"}}
      assets/ganjacraft/sounds/track_{id}.ogg
      assets/ganjacraft/sounds.json: {"track_{id}":{"sounds":[{"name":"ganjacraft:track_{id}","stream":true}],"subtitle":"GanjaCraft Media"}}
    Note: sounds.json key WITHOUT namespace per spec.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        # pack.mcmeta - 26.3 uses int pack_format (97), not object
        mcmeta = {
            "pack": {
                "pack_format": PACK_FORMAT_MAJOR,
                "description": PACK_DESCRIPTION,
            }
        }
        z.writestr("pack.mcmeta", json.dumps(mcmeta, ensure_ascii=False))

        # pack.png is optional - skip to keep micro-pack minimal

        # ogg file
        ogg_path = f"assets/ganjacraft/sounds/track_{track_id}.ogg"
        z.writestr(ogg_path, ogg_bytes)

        # sounds.json - key without namespace
        sounds = {
            f"track_{track_id}": {
                "sounds": [{"name": f"ganjacraft:track_{track_id}", "stream": True}],
                "subtitle": PACK_DESCRIPTION,
            }
        }
        z.writestr("assets/ganjacraft/sounds.json", json.dumps(sounds, ensure_ascii=False))

    return buf.getvalue()


async def transcode_to_ogg_mono(source_path: Path, out_path: Path) -> bool:
    """
    Transcode source audio to mono ogg vorbis for OpenAL attenuation.
    Uses ffmpeg:
      -vn: Strip any embedded video / album art (embedded APIC pictures create
           a Theora video stream in .ogg that crashes Minecraft's audio engine!)
      -map 0:a:0: Strictly select only the primary audio stream.
      -map_metadata -1: Strip metadata tags that can corrupt Vorbis headers.
      -ac 1: Mono audio for 3D positional playback.
      -c:a libvorbis -q:a 4: Standard Vorbis quality.
    Returns True on success.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", str(source_path),
        "-vn",
        "-map", "0:a:0",
        "-map_metadata", "-1",
        "-ac", "1",
        "-c:a", "libvorbis",
        "-q:a", "4",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"ffmpeg mono transcode failed for {source_path}: {stderr.decode(errors='ignore')[:500]}")
        return False
    return True


async def download_telegram_file(file_id: str, dest: Path) -> bool:
    """Download Telegram file via getFile + file download. Returns True on success."""
    from api.routers.player import get_telegram_file_path
    import aiohttp
    from shared.config import get_settings as _gs
    s = _gs()
    file_path = await get_telegram_file_path(file_id)
    if not file_path:
        logger.warning(f"Could not get file_path for {file_id[:20]}...")
        return False
    base_url = s.telegram_api_url.rstrip("/")
    # file_path already includes e.g. "documents/file_123.ogg" - need full URL
    # Telegram file URL: {api_url}/file/bot{token}/{file_path}
    url = f"{base_url}/file/bot{s.bot_token}/{file_path}"
    # Use shared http session if available
    try:
        from api.utils.bot_helpers import get_http_session
        session = await get_http_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error(f"Download failed status={resp.status} for {file_id[:20]}")
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    f.write(chunk)
            return True
    except Exception as e:
        logger.error(f"download_telegram_file error: {e}")
        return False


async def ensure_pack_for_track(track_id: int, file_id: str, title: str = "") -> Optional[tuple[bytes, str]]:
    """
    Ensure micro-pack zip exists for track_id. Returns (zip_bytes, sha1) or None.
    Caches on disk: data/minecraft_packs/{id}.zip + .sha1
    If zip exists and sha1 cached, returns immediately.
    Otherwise downloads source, transcodes to mono ogg, builds zip.
    """
    zpath = pack_filename(track_id)
    if zpath.exists():
        sha1 = get_cached_sha1(track_id)
        if sha1:
            try:
                data = zpath.read_bytes()
                # Verify sha1 matches file (in case of corruption)
                actual = _compute_sha1(data)
                if actual == sha1:
                    return data, sha1
                else:
                    logger.warning(f"sha1 mismatch for track {track_id}, rebuilding")
            except Exception:
                pass

    # Need to build
    tmp_dir = PACKS_DIR / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    src_path = tmp_dir / f"{track_id}_src"
    ogg_path = tmp_dir / f"{track_id}.ogg"

    try:
        ok = await download_telegram_file(file_id, src_path)
        if not ok or not src_path.exists() or src_path.stat().st_size == 0:
            logger.error(f"Failed to download source for track {track_id}")
            return None

        # If source is already ogg mono, we could skip transcode, but always transcode to ensure mono
        ok = await transcode_to_ogg_mono(src_path, ogg_path)
        if not ok or not ogg_path.exists():
            logger.error(f"Transcode failed for track {track_id}")
            return None

        ogg_bytes = ogg_path.read_bytes()
        if not ogg_bytes:
            logger.error(f"Empty ogg for track {track_id}")
            return None

        zip_bytes = _build_zip_bytes(track_id, ogg_bytes, title)
        sha1 = _compute_sha1(zip_bytes)

        # Atomic write
        tmp_zip = zpath.with_suffix(".tmp.zip")
        tmp_zip.write_bytes(zip_bytes)
        tmp_zip.replace(zpath)
        sha1_path(track_id).write_text(sha1, encoding="utf-8")
        _sha1_cache[track_id] = sha1

        logger.info(f"Built micro-pack for track {track_id}: {len(zip_bytes)} bytes sha1={sha1[:8]}...")
        return zip_bytes, sha1

    except Exception as e:
        logger.exception(f"ensure_pack_for_track {track_id} failed: {e}")
        return None
    finally:
        for p in (src_path, ogg_path):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass


def invalidate_pack(track_id: int):
    """Remove cached pack (e.g. if track file_id changed)."""
    _sha1_cache.pop(track_id, None)
    for p in (pack_filename(track_id), sha1_path(track_id)):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
