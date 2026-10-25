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
import time
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

BASE_PACK_FILENAME = PACKS_DIR / "base_pack.zip"
BASE_PACK_SHA1_PATH = PACKS_DIR / "base_pack.sha1"
BASE_PACK_META_PATH = PACKS_DIR / "base_pack_meta.json"


def base_pack_uuid() -> uuid.UUID:
    """
    Deterministic UUID matching Java's UUID.nameUUIDFromBytes("ganjacraft_base_music_pack".getBytes(StandardCharsets.UTF_8)).
    """
    data = b"ganjacraft_base_music_pack"
    md5_bytes = bytearray(hashlib.md5(data).digest())
    md5_bytes[6] = (md5_bytes[6] & 0x0f) | 0x30
    md5_bytes[8] = (md5_bytes[8] & 0x3f) | 0x80
    return uuid.UUID(bytes=bytes(md5_bytes))


def get_base_pack_meta() -> dict:
    if BASE_PACK_META_PATH.exists():
        try:
            return json.loads(BASE_PACK_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"track_ids": [], "built_at": 0, "sha1": ""}


def get_base_pack_sha1() -> Optional[str]:
    meta = get_base_pack_meta()
    if meta.get("sha1"):
        return meta["sha1"]
    if BASE_PACK_SHA1_PATH.exists():
        try:
            return BASE_PACK_SHA1_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None



def generate_vinyl_texture(cover_bytes: Optional[bytes] = None) -> bytes:
    """
    Generate authentic 32x32 transparent CD Jewel Case with silkscreen picture disc print.
    Features:
    - 28x28 acrylic transparent case with pixelated glass highlights
    - Left black hinge spine
    - 23px Compact Disc with silver rim and spindle hub
    - Full-color album cover print across the disc surface (picture disc)
    """
    try:
        import math
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Jewel case bounds: 28x28 (x=2, y=2 to x=29, y=29)
        x0, y0, x1, y1 = 2, 2, 29, 29

        # 1. Subtle drop shadow
        draw.rounded_rectangle([x0 + 1, y0 + 1, x1 + 1, y1 + 1], radius=2, fill=(10, 10, 14, 100))

        # 2. Transparent dark tinted plastic case base
        draw.rounded_rectangle([x0, y0, x1, y1], radius=2, fill=(28, 30, 36, 170))

        # 3. Compact Disc (Center cx=16.5, cy=15.5, Radius R=11.5)
        cx, cy = 16.5, 15.5
        R_outer = 11.5
        R_inner_hub = 4.2
        R_hole = 1.8

        disc_img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        disc_draw = ImageDraw.Draw(disc_img)

        # Base disc geometry
        for y in range(4, 28):
            for x in range(5, 29):
                dx = x - cx
                dy = y - cy
                dist = math.hypot(dx, dy)
                if dist <= R_outer:
                    if dist >= R_outer - 0.9:
                        disc_draw.point((x, y), fill=(220, 225, 235, 255)) # Silver rim
                    elif dist <= R_hole:
                        pass # Clear spindle hole
                    elif dist <= R_inner_hub:
                        disc_draw.point((x, y), fill=(160, 170, 185, 140)) # Clear hub
                    else:
                        disc_draw.point((x, y), fill=(30, 32, 38, 255))

        # Picture Disc Silk-screen artwork print
        cover_img = None
        if cover_bytes:
            try:
                cover_img = Image.open(io.BytesIO(cover_bytes)).convert("RGBA")
            except Exception:
                cover_img = None

        if cover_img:
            print_size = int((R_outer - 0.8) * 2)
            cover_resized = cover_img.resize((print_size, print_size), Image.Resampling.LANCZOS)
            for y in range(4, 28):
                for x in range(5, 29):
                    dx = x - cx
                    dy = y - cy
                    dist = math.hypot(dx, dy)
                    if R_inner_hub < dist <= R_outer - 0.9:
                        u = int(dx + R_outer - 0.8)
                        v = int(dy + R_outer - 0.8)
                        if 0 <= u < print_size and 0 <= v < print_size:
                            cp = cover_resized.getpixel((u, v))
                            disc_draw.point((x, y), fill=(cp[0], cp[1], cp[2], 255))
        else:
            # Silver CD rainbow sheen
            for y in range(4, 28):
                for x in range(5, 29):
                    dx = x - cx
                    dy = y - cy
                    dist = math.hypot(dx, dy)
                    if R_inner_hub < dist <= R_outer - 0.9:
                        angle = (math.atan2(dy, dx) + math.pi) / (2 * math.pi)
                        v = int((math.sin(angle * 8) * 0.5 + 0.5) * 50) + 175
                        disc_draw.point((x, y), fill=(v, v + 10, v + 22, 255))

        # Center spindle hub clips (4 dots)
        for a in (0, 90, 180, 270):
            rad = math.radians(a)
            tx = int(cx + 3.0 * math.cos(rad))
            ty = int(cy + 3.0 * math.sin(rad))
            disc_draw.point((tx, ty), fill=(240, 245, 255, 190))

        img = Image.alpha_composite(img, disc_img)
        draw = ImageDraw.Draw(img)

        # 4. Black spine hinge on left edge (x=2, 3)
        draw.line([(x0, y0), (x0, y1)], fill=(18, 18, 22, 240))
        draw.line([(x0 + 1, y0 + 1), (x0 + 1, y1 - 1)], fill=(35, 38, 44, 240))

        # 5. Clear plastic Jewel Case highlights & borders
        draw.line([(x0 + 1, y0), (x1 - 1, y0)], fill=(255, 255, 255, 200)) # Top
        draw.line([(x0 + 1, y1), (x1 - 1, y1)], fill=(15, 15, 20, 220))     # Bottom
        draw.line([(x1, y0 + 1), (x1, y1 - 1)], fill=(25, 28, 35, 180))     # Right

        # Diagonal glass glare
        for i in range(8):
            draw.point((x0 + 3 + i, y0 + 2 + i), fill=(255, 255, 255, 80))
            draw.point((x0 + 4 + i, y0 + 2 + i), fill=(255, 255, 255, 110))

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
      pack.png: 64x64 album art or vinyl icon
      assets/ganjacraft/sounds/track_{id}.ogg
      assets/ganjacraft/sounds.json
      assets/minecraft/optifine/cit/music_disc_{id}.png (16x16 vinyl texture)
      assets/minecraft/optifine/cit/music_disc_{id}.properties (CIT support for OptiFine / Variants CIT)
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        mcmeta = {
            "pack": {
                "pack_format": PACK_FORMAT_MAJOR,
                "min_format": 48,
                "max_format": PACK_FORMAT_MAJOR,
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

        # Vinyl custom texture & Native 26.3 Item Model + CIT fallback
        vinyl_png = generate_vinyl_texture(cover_bytes)
        if vinyl_png:
            # 1. Native 26.3 Item Definition: assets/ganjacraft/items/disc_{track_id}.json
            item_def = {
                "model": {
                    "type": "minecraft:model",
                    "model": f"ganjacraft:item/disc_{track_id}"
                }
            }
            z.writestr(f"assets/ganjacraft/items/disc_{track_id}.json", json.dumps(item_def, ensure_ascii=False))

            # 2. Native 26.3 Item Model: assets/ganjacraft/models/item/disc_{track_id}.json
            item_model = {
                "parent": "minecraft:item/template_music_disc",
                "textures": {
                    "layer0": f"ganjacraft:item/disc_{track_id}"
                }
            }
            z.writestr(f"assets/ganjacraft/models/item/disc_{track_id}.json", json.dumps(item_model, ensure_ascii=False))

            # 3. Native 26.3 Texture: assets/ganjacraft/textures/item/disc_{track_id}.png
            z.writestr(f"assets/ganjacraft/textures/item/disc_{track_id}.png", vinyl_png)

            # 4. OptiFine CIT fallback
            z.writestr(f"assets/minecraft/optifine/cit/music_disc_{track_id}.png", vinyl_png)
            cit_props = (
                f"type=item\n"
                f"items=music_disc_13\n"
                f"matchItems=minecraft:music_disc_13\n"
                f"texture=music_disc_{track_id}.png\n"
                f"nbt.ganjacraft_track_id={track_id}\n"
            )
            z.writestr(f"assets/minecraft/optifine/cit/music_disc_{track_id}.properties", cit_props)

        # pack.png for the pack icon
        if cover_bytes:
            try:
                from PIL import Image
                cimg = Image.open(io.BytesIO(cover_bytes)).convert("RGBA")
                cimg = cimg.resize((64, 64), Image.Resampling.LANCZOS)
                buf_icon = io.BytesIO()
                cimg.save(buf_icon, format="PNG")
                z.writestr("pack.png", buf_icon.getvalue())
            except Exception as e:
                logger.debug(f"Failed to generate pack.png: {e}")
        elif vinyl_png:
            z.writestr("pack.png", vinyl_png)

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
    url = f"{base_url}/file/bot{s.bot_token}/{file_path.lstrip('/')}"
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


async def download_telegram_file_bytes(file_id: str) -> Optional[bytes]:
    """Download Telegram file into bytes using get_http_session."""
    from api.routers.player import get_telegram_file_path
    from api.utils.bot_helpers import get_http_session
    from shared.config import get_settings as _gs
    s = _gs()
    file_path = await get_telegram_file_path(file_id)
    if not file_path:
        logger.warning(f"Could not get file_path for {file_id[:20]}...")
        return None
    base_url = s.telegram_api_url.rstrip("/")
    url = f"{base_url}/file/bot{s.bot_token}/{file_path.lstrip('/')}"
    try:
        session = await get_http_session()
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
            logger.warning(f"Telegram file download status={resp.status} for {file_id[:20]}")
    except Exception as e:
        logger.error(f"download_telegram_file_bytes error: {e}")
    return None


async def download_cover_bytes(cover_ref: Optional[str]) -> Optional[bytes]:
    """Download album artwork from external URL, Telegram proxy route, or direct Telegram file_id."""
    if not cover_ref:
        return None
    cover_ref = cover_ref.strip()

    # 1. Telegram proxy route (/api/images/<file_id>)
    if cover_ref.startswith("/api/images/"):
        file_id = cover_ref.split("/api/images/")[-1].strip()
        return await download_telegram_file_bytes(file_id)

    # 2. External HTTP / HTTPS image URL (Deezer, Last.fm, YouTube CDN, etc.)
    if cover_ref.startswith("http://") or cover_ref.startswith("https://"):
        try:
            import aiohttp
            from api.utils.bot_helpers import get_http_session
            from shared.config import get_proxy_url
            session = await get_http_session()
            proxy = get_proxy_url()
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/*,*/*;q=0.8",
            }
            async with session.get(cover_ref, headers=headers, timeout=timeout, proxy=proxy) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.warning(f"External cover download status={resp.status} for {cover_ref[:60]}")
        except Exception as e:
            logger.warning(f"Failed to fetch external cover {cover_ref[:60]}: {e}")
        return None

    # 3. Direct Telegram file_id
    return await download_telegram_file_bytes(cover_ref)


async def ensure_pack_for_track(
    track_id: int, 
    file_id: str, 
    title: str = "", 
    duration: Optional[int] = None,
    cover_ref: Optional[str] = None
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

    # Permanent audio cache for transcoded mono ogg
    audio_dir = PACKS_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    ogg_cache_path = audio_dir / f"{track_id}.ogg"

    tmp_dir = PACKS_DIR / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    src_path = tmp_dir / f"{track_id}_src"

    try:
        if ogg_cache_path.exists() and ogg_cache_path.stat().st_size > 0:
            ogg_bytes = ogg_cache_path.read_bytes()
        else:
            ok = await download_telegram_file(file_id, src_path)
            if not ok or not src_path.exists() or src_path.stat().st_size == 0:
                logger.error(f"Failed to download source for track {track_id}")
                return None

            # Transcode strictly with concurrency 1 and low priority
            is_long_mix = bool(duration and duration > 900)
            tmp_ogg = tmp_dir / f"{track_id}.ogg"
            async with _transcode_semaphore:
                ok = await transcode_to_ogg_mono(src_path, tmp_ogg, is_long_mix=is_long_mix)

            if not ok or not tmp_ogg.exists() or tmp_ogg.stat().st_size == 0:
                logger.error(f"Transcode failed for track {track_id}")
                return None

            # Store in permanent audio cache
            tmp_ogg.replace(ogg_cache_path)
            ogg_bytes = ogg_cache_path.read_bytes()

        # Download cover if available
        cover_bytes = None
        if cover_ref:
            try:
                cover_bytes = await download_cover_bytes(cover_ref)
            except Exception as e:
                logger.debug(f"Failed to download cover for track {track_id}: {e}")

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
        for p in (src_path, tmp_dir / f"{track_id}.ogg"):
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


async def build_base_pack(track_items: list[dict]) -> Optional[tuple[bytes, str, list[int]]]:
    """
    Build unified Base Music Pack combining given tracks.
    Each item in track_items has: id, file_id, title, duration, cover_ref.
    Merges their micro-pack assets into a single zip with unified sounds.json.
    """
    if not track_items:
        return None

    # 1. Ensure all micro-packs are built
    for item in track_items:
        tr_id = item["id"]
        zpath = pack_filename(tr_id)
        if not zpath.exists() or not get_cached_sha1(tr_id):
            file_id = item.get("file_id")
            if file_id and not file_id.startswith("lazy:") and not file_id.startswith("pending:"):
                try:
                    await ensure_pack_for_track(
                        tr_id,
                        file_id,
                        title=item.get("title") or f"Track {tr_id}",
                        duration=item.get("duration"),
                        cover_ref=item.get("cover_ref")
                    )
                except Exception as e:
                    logger.warning(f"Failed to ensure pack for track {tr_id} while building base pack: {e}")

    # 2. Build master zip
    buf = io.BytesIO()
    merged_sounds = {}
    included_ids = []
    first_icon = None

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as master_zip:
        mcmeta = {
            "pack": {
                "pack_format": PACK_FORMAT_MAJOR,
                "min_format": 48,
                "max_format": PACK_FORMAT_MAJOR,
                "description": "GanjaCraft Base Music Pack",
            }
        }
        master_zip.writestr("pack.mcmeta", json.dumps(mcmeta, ensure_ascii=False))

        for item in track_items:
            tr_id = item["id"]
            zpath = pack_filename(tr_id)
            if not zpath.exists():
                continue

            try:
                with zipfile.ZipFile(zpath, "r") as single_zip:
                    for zinfo in single_zip.infolist():
                        if zinfo.filename in ("pack.mcmeta", "assets/ganjacraft/sounds.json"):
                            continue
                        if zinfo.filename == "pack.png":
                            if not first_icon:
                                first_icon = single_zip.read(zinfo.filename)
                            continue
                        content = single_zip.read(zinfo.filename)
                        master_zip.writestr(zinfo, content)

                merged_sounds[f"track_{tr_id}"] = {
                    "sounds": [{"name": f"ganjacraft:track_{tr_id}", "stream": True}],
                    "subtitle": PACK_DESCRIPTION,
                }
                included_ids.append(tr_id)
            except Exception as e:
                logger.error(f"Error copying track {tr_id} into base pack: {e}")

        if first_icon:
            master_zip.writestr("pack.png", first_icon)

        # Write unified sounds.json
        master_zip.writestr("assets/ganjacraft/sounds.json", json.dumps(merged_sounds, ensure_ascii=False))

    zip_bytes = buf.getvalue()
    if not zip_bytes or not included_ids:
        logger.error("Base pack build resulted in 0 tracks")
        return None

    sha1 = _compute_sha1(zip_bytes)

    # Atomic write to disk
    tmp_zip = BASE_PACK_FILENAME.with_suffix(".tmp.zip")
    tmp_zip.write_bytes(zip_bytes)
    tmp_zip.replace(BASE_PACK_FILENAME)
    BASE_PACK_SHA1_PATH.write_text(sha1, encoding="utf-8")

    meta = {
        "track_ids": included_ids,
        "built_at": int(time.time()),
        "sha1": sha1,
        "track_count": len(included_ids),
    }
    BASE_PACK_META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Successfully built Base Music Pack: {len(included_ids)} tracks, {len(zip_bytes)} bytes, sha1={sha1[:8]}")
    return zip_bytes, sha1, included_ids


def extract_cover_ref(tr) -> Optional[str]:
    img_id = getattr(tr, "image_file_id", None)
    if img_id:
        return img_id
    enr = tr.__dict__.get("enrichment")
    if enr and getattr(enr, "cover_url", None):
        return enr.cover_url
    return None


_scheduler_task: Optional[asyncio.Task] = None


async def _nightly_rebuild_loop():
    """Runs nightly check at 04:00 local time to rebuild Base Music Pack if new tracks exist."""
    import datetime
    while True:
        try:
            now = datetime.datetime.now()
            # Calculate next 04:00
            target = now.replace(hour=4, minute=0, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            delay = (target - now).total_seconds()
            logger.info(f"[MinecraftPack] Next nightly Base Pack check scheduled in {delay/3600:.2f}h (at {target.strftime('%Y-%m-%d %H:%M:%S')})")
            await asyncio.sleep(delay)

            logger.info("[MinecraftPack] Running scheduled nightly Base Pack consolidation (04:00)...")
            from shared.database import async_session
            from shared.models import Track, Playlist, PlaylistTrack
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            async with async_session() as db:
                meta = get_base_pack_meta()
                base_track_ids = set(meta.get("track_ids", []))
                stmt = (
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
                all_tracks = (await db.execute(stmt)).scalars().all()
                new_tracks = [t for t in all_tracks if t.id not in base_track_ids]

                if not new_tracks and BASE_PACK_FILENAME.exists() and get_base_pack_sha1():
                    logger.info("[MinecraftPack] Nightly check: 0 new tracks, Base Pack is already up to date.")
                    continue

                logger.info(f"[MinecraftPack] Nightly check: consolidating {len(new_tracks)} new tracks ({len(all_tracks)} total) into Base Pack...")
                track_items = [
                    {
                        "id": t.id,
                        "file_id": t.file_id,
                        "title": t.title or f"Track {t.id}",
                        "duration": t.duration,
                        "cover_ref": extract_cover_ref(t),
                    }
                    for t in all_tracks
                ]
                res = await build_base_pack(track_items)
                if res:
                    _, new_sha1, inc = res
                    logger.info(f"[MinecraftPack] Nightly Base Pack rebuild complete: {len(inc)} tracks, sha1={new_sha1[:8]}")
                else:
                    logger.warning("[MinecraftPack] Nightly Base Pack rebuild failed.")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[MinecraftPack] Error in nightly rebuild loop: {e}")
            await asyncio.sleep(300)


def start_minecraft_pack_scheduler():
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_nightly_rebuild_loop())
        logger.info("[MinecraftPack] Nightly 04:00 Base Pack scheduler task started.")


def stop_minecraft_pack_scheduler():
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
        logger.info("[MinecraftPack] Nightly Base Pack scheduler task stopped.")


