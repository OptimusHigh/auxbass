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


# Global transcode semaphore to protect CPU from starvation
_transcode_semaphore = asyncio.Semaphore(1)


def generate_vinyl_texture(cover_bytes: Optional[bytes] = None) -> bytes:
    """Generate authentic 16x16 Minecraft vinyl disc texture with pixelated cover."""
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw vinyl grooved body
        for y in range(16):
            for x in range(16):
                dx = x - 7.5
                dy = y - 7.5
                dist = (dx * dx + dy * dy) ** 0.5
                if 1.2 <= dist <= 6.8:
                    val = 24 if int(dist * 2.2) % 2 == 0 else 38
                    draw.point((x, y), fill=(val, val, val, 255))

        # Outer edge rim
        rim_points = [
            (5,1),(6,1),(7,1),(8,1),(9,1),(10,1),
            (3,2),(4,2),(11,2),(12,2),
            (2,3),(13,3),(2,4),(13,4),
            (1,5),(14,5),(1,6),(14,6),(1,7),(14,7),(1,8),(14,8),(1,9),(14,9),(1,10),(14,10),
            (2,11),(13,11),(2,12),(13,12),
            (3,13),(4,13),(11,13),(12,13),
            (5,14),(6,14),(7,14),(8,14),(9,14),(10,14)
        ]
        for p in rim_points:
            draw.point(p, fill=(18, 18, 18, 255))

        # Draw label if cover provided
        if cover_bytes:
            try:
                cimg = Image.open(io.BytesIO(cover_bytes)).convert("RGBA")
                cimg = cimg.resize((6, 6), Image.Resampling.LANCZOS)
                for cy in range(6):
                    for cx in range(6):
                        dx = cx - 2.5
                        dy = cy - 2.5
                        if dx * dx + dy * dy <= 6.5:
                            px = cimg.getpixel((cx, cy))
                            img.putpixel((cx + 5, cy + 5), px)
            except Exception as e:
                logger.debug(f"Cover label overlay failed: {e}")

        # Spindle hole (center)
        for sx in (7, 8):
            for sy in (7, 8):
                img.putpixel((sx, sy), (0, 0, 0, 0))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"generate_vinyl_texture failed: {e}")
        return b""


def _build_zip_bytes(track_id: int, ogg_bytes: bytes, title: str = "", cover_bytes: Optional[bytes] = None) -> bytes:
    """
    Build micro-pack zip in memory.
    Structure:
      pack.mcmeta: {"pack":{"pack_format":97,"description":"GanjaCraft Media"}}
      assets/ganjacraft/sounds/track_{id}.ogg
      assets/ganjacraft/sounds.json
      assets/minecraft/optifine/cit/music_disc_{id}.png (16x16 vinyl texture)
      assets/minecraft/optifine/cit/music_disc_{id}.properties (CIT support)
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        mcmeta = {
            "pack": {
                "pack_format": PACK_FORMAT_MAJOR,
                "description": PACK_DESCRIPTION,
            }
        }
        z.writestr("pack.mcmeta", json.dumps(mcmeta, ensure_ascii=False))

        # ogg file
        ogg_path = f"assets/ganjacraft/sounds/track_{track_id}.ogg"
        z.writestr(ogg_path, ogg_bytes)

        # sounds.json - key without namespace per spec
        sounds = {
            f"track_{track_id}": {
                "sounds": [{"name": f"ganjacraft:track_{track_id}", "stream": True}],
                "subtitle": PACK_DESCRIPTION,
            }
        }
        z.writestr("assets/ganjacraft/sounds.json", json.dumps(sounds, ensure_ascii=False))

        # Vinyl custom texture & CIT support (OptiFine / CIT Resewn)
        vinyl_png = generate_vinyl_texture(cover_bytes)
        if vinyl_png:
            z.writestr(f"assets/minecraft/optifine/cit/music_disc_{track_id}.png", vinyl_png)
            cit_props = (
                f"type=item\n"
                f"matchItems=minecraft:music_disc_13\n"
                f"texture=music_disc_{track_id}.png\n"
                f"nbt.ganjacraft_track_id={track_id}\n"
            )
            z.writestr(f"assets/minecraft/optifine/cit/music_disc_{track_id}.properties", cit_props)

    return buf.getvalue()


async def transcode_to_ogg_mono(source_path: Path, out_path: Path, is_long_mix: bool = False) -> bool:
    """
    Transcode source audio to mono ogg vorbis for OpenAL attenuation.
    Throttled to max 2 threads and low priority (nice -n 19 on Linux) to prevent MC lag.
    """
    quality_args = ["-b:a", "64k"] if is_long_mix else ["-q:a", "4"]
    
    import shutil
    has_nice = shutil.which("nice") is not None
    cmd = []
    if has_nice:
        cmd.extend(["nice", "-n", "19"])

    cmd.extend([
        "ffmpeg", "-y",
        "-threads", "2",
        "-i", str(source_path),
        "-vn",
        "-map", "0:a:0",
        "-map_metadata", "-1",
        "-ac", "1",
        "-c:a", "libvorbis",
        *quality_args,
        str(out_path),
    ])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
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


async def ensure_pack_for_track(
    track_id: int, 
    file_id: str, 
    title: str = "", 
    duration: Optional[int] = None,
    image_file_id: Optional[str] = None
) -> Optional[tuple[bytes, str]]:
    """
    Ensure micro-pack zip exists for track_id. Returns (zip_bytes, sha1) or None.
    Caches on disk: data/minecraft_packs/{id}.zip + .sha1
    """
    zpath = pack_filename(track_id)
    if zpath.exists():
        sha1 = get_cached_sha1(track_id)
        if sha1:
            try:
                data = zpath.read_bytes()
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
    cover_path = tmp_dir / f"{track_id}_cover.jpg" if image_file_id else None

    try:
        ok = await download_telegram_file(file_id, src_path)
        if not ok or not src_path.exists() or src_path.stat().st_size == 0:
            logger.error(f"Failed to download source for track {track_id}")
            return None

        # Download cover if available
        cover_bytes = None
        if image_file_id and cover_path:
            try:
                cov_ok = await download_telegram_file(image_file_id, cover_path)
                if cov_ok and cover_path.exists():
                    cover_bytes = cover_path.read_bytes()
            except Exception as e:
                logger.debug(f"Failed to download cover for track {track_id}: {e}")

        # Transcode strictly with concurrency 1 and low priority
        is_long_mix = bool(duration and duration > 900)
        async with _transcode_semaphore:
            ok = await transcode_to_ogg_mono(src_path, ogg_path, is_long_mix=is_long_mix)
            
        if not ok or not ogg_path.exists():
            logger.error(f"Transcode failed for track {track_id}")
            return None

        ogg_bytes = ogg_path.read_bytes()
        if not ogg_bytes:
            logger.error(f"Empty ogg for track {track_id}")
            return None

        zip_bytes = _build_zip_bytes(track_id, ogg_bytes, title, cover_bytes=cover_bytes)
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
        for p in (src_path, ogg_path, cover_path):
            try:
                if p and p.exists():
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
