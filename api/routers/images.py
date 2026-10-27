import asyncio
from collections import OrderedDict, defaultdict
import hashlib
import ipaddress
import logging
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from shared.config import get_settings, get_proxy_url
from shared.images import crop_image_to_square
from api.utils.bot_helpers import get_bot as _get_bot, close_bot as close_image_bot, get_http_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Images"])
settings = get_settings()

# Private / reserved network blocks for SSRF protection
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# ============== In-Memory RAM Caches & Dedup ==============
# 1. LRU cache for decoded image content: file_id -> (content_bytes, content_type)
_IMAGE_CACHE_MAX_ITEMS = 500
_image_cache: OrderedDict[str, Tuple[bytes, str]] = OrderedDict()
_image_cache_lock = asyncio.Lock()

# 2. TTL cache for file_id -> (file_path, expire_timestamp)
# Telegram file paths stay valid for at least 1 hour; we cache for 45 min
_FILE_PATH_TTL_SECONDS = 45 * 60
_file_path_cache: dict[str, Tuple[str, float]] = {}
_file_path_lock = asyncio.Lock()

# 3. In-flight requests deduplication: file_id -> Task returning (bytes, str)
_inflight_image_tasks: dict[str, asyncio.Task] = {}
_inflight_subscribers: dict[str, set] = defaultdict(set)
_inflight_lock = asyncio.Lock()


def _is_safe_url(url_str: str) -> bool:
    """Validate external image URL against SSRF and invalid protocols."""
    try:
        parsed = urlparse(url_str)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "0.0.0.0"):
            return False
        # If hostname is an IP, check against private networks
        try:
            ip = ipaddress.ip_address(hostname)
            for net in _PRIVATE_NETWORKS:
                if ip in net:
                    return False
        except ValueError:
            # Hostname is a domain name, not a raw IP
            pass
        return True
    except Exception:
        return False


def _is_etag_matched(request: Optional[Request], etag: str) -> bool:
    """Robust ETag matching supporting weak tags (W/), quotes, and wildcard (*)"""
    if not request:
        return False
    inm = request.headers.get("if-none-match")
    if not inm:
        return False
    inm_clean = inm.strip()
    if inm_clean == "*":
        return True
    inm_unquoted = inm_clean.removeprefix("W/").strip('"')
    etag_unquoted = etag.strip().removeprefix("W/").strip('"')
    return inm_unquoted == etag_unquoted


@router.get("/images/proxy")
async def proxy_external_image(
    request: Request,
    url: str = Query(..., description="External image URL to proxy"),
):
    """Proxy external cover art (SoundCloud, Spotify, Deezer, etc.) to ensure availability in restricted regions."""
    url = url.strip()
    if not url or not _is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or disallowed image URL")

    # Early disconnect check
    if request and await request.is_disconnected():
        return Response(status_code=499)

    session = await get_http_session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }

    proxy = get_proxy_url()
    try:
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        async with session.get(url, headers=headers, timeout=timeout, proxy=proxy) as resp:
            if resp.status != 200:
                # If YouTube thumbnail failed with 404, fallback to hqdefault.jpg
                if resp.status == 404 and "i.ytimg.com" in url:
                    m = re.search(r"/vi/([a-zA-Z0-9_\-]{11})/", url)
                    if m:
                        vid = m.group(1)
                        fallback_yt_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                        if fallback_yt_url != url:
                            try:
                                if request and await request.is_disconnected():
                                    return Response(status_code=499)
                                async with session.get(fallback_yt_url, headers=headers, timeout=timeout, proxy=proxy) as fb_resp:
                                    if fb_resp.status == 200:
                                        content = await fb_resp.read()
                                        try:
                                            content, ext = crop_image_to_square(content)
                                            fb_ct = "image/jpeg" if ext == "jpg" else "image/png"
                                        except Exception:
                                            fb_ct = "image/jpeg"
                                        etag = f'"{hashlib.md5(content).hexdigest()}"'
                                        if _is_etag_matched(request, etag):
                                            return Response(
                                                status_code=304,
                                                headers={
                                                    "ETag": etag,
                                                    "Cache-Control": "public, max-age=604800, immutable",
                                                },
                                            )
                                        return Response(
                                            content=content,
                                            media_type=fb_ct,
                                            headers={
                                                "ETag": etag,
                                                "Cache-Control": "public, max-age=604800, immutable",
                                            },
                                        )
                            except Exception as fb_err:
                                logger.debug(f"YouTube thumbnail fallback failed: {fb_err}")

                logger.warning(f"External image proxy failed ({resp.status}) for URL {url[:60]}")
                raise HTTPException(status_code=resp.status, detail="Failed to fetch external image")

            raw_ct = resp.headers.get("Content-Type", "").lower()
            if raw_ct.startswith("image/"):
                content_type = raw_ct.split(";")[0].strip()
            else:
                path_lower = urlparse(url).path.lower()
                if path_lower.endswith(".png"):
                    content_type = "image/png"
                elif path_lower.endswith(".webp"):
                    content_type = "image/webp"
                elif path_lower.endswith(".gif"):
                    content_type = "image/gif"
                else:
                    content_type = "image/jpeg"

            # Stream chunks with disconnect checking (fallback to read() for mocks/small streams)
            if isinstance(getattr(resp, "content", None), aiohttp.StreamReader):
                chunks = []
                total_read = 0
                async for chunk in resp.content.iter_chunked(16384):
                    if request and await request.is_disconnected():
                        resp.close()
                        return Response(status_code=499)
                    chunks.append(chunk)
                    total_read += len(chunk)
                    if total_read > 10 * 1024 * 1024:
                        resp.close()
                        raise HTTPException(status_code=400, detail="Image exceeds size limit")
                content = b"".join(chunks)
            else:
                content = await resp.read()

            # Auto-crop YouTube 16:9 / 4:3 frames to 1:1 square
            if "i.ytimg.com" in url or "youtube.com" in url:
                try:
                    content, ext = crop_image_to_square(content)
                    content_type = "image/jpeg" if ext == "jpg" else "image/png"
                except Exception as crop_err:
                    logger.debug(f"Failed to auto-crop YouTube thumbnail: {crop_err}")

            etag = f'"{hashlib.md5(content).hexdigest()}"'
            if _is_etag_matched(request, etag):
                return Response(
                    status_code=304,
                    headers={
                        "ETag": etag,
                        "Cache-Control": "public, max-age=604800, immutable",
                    },
                )

            return Response(
                content=content,
                media_type=content_type,
                headers={
                    "ETag": etag,
                    "Cache-Control": "public, max-age=604800, immutable",
                },
            )
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout fetching external image")
    except Exception as e:
        logger.warning(f"Error proxying external image {url[:60]}: {e}")
        raise HTTPException(status_code=502, detail="Failed to proxy image")


async def _all_subscribers_disconnected(file_id: str) -> bool:
    """Check if all clients waiting for this in-flight image have disconnected."""
    async with _inflight_lock:
        subs = _inflight_subscribers.get(file_id)
        if not subs:
            return False
        disconnected = []
        for req in list(subs):
            if req is not None and await req.is_disconnected():
                disconnected.append(req)
        for req in disconnected:
            subs.discard(req)
        return len(subs) == 0


async def _fetch_and_cache_telegram_image(file_id: str) -> Tuple[bytes, str]:
    """Fetch image from Telegram with TTL file_path cache and streaming abort support."""
    # 1. Resolve file_path (use TTL cache if valid)
    now = time.time()
    file_path = None
    async with _file_path_lock:
        if file_id in _file_path_cache:
            cached_path, expire_at = _file_path_cache[file_id]
            if now < expire_at:
                file_path = cached_path
            else:
                del _file_path_cache[file_id]

    if not file_path:
        # Check if all subscribers already disconnected before hitting Telegram Bot API
        if await _all_subscribers_disconnected(file_id):
            raise asyncio.CancelledError("All clients disconnected before getFile")

        bot = _get_bot()
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path

        # Cache file_path with TTL
        async with _file_path_lock:
            _file_path_cache[file_id] = (file_path, time.time() + _FILE_PATH_TTL_SECONDS)

    # Check disconnect before downloading binary content
    if await _all_subscribers_disconnected(file_id):
        raise asyncio.CancelledError("All clients disconnected before file download")

    # 2. Download binary content from Telegram CDN
    base_url = settings.telegram_api_url.rstrip("/")
    file_clean = file_path.lstrip("/")
    url = f"{base_url}/file/bot{settings.bot_token}/{file_clean}"

    session = await get_http_session()
    async with session.get(url) as resp:
        if resp.status != 200:
            logger.warning(f"Failed to fetch Telegram image {file_id[:20]} status={resp.status} url={url}")
            raise HTTPException(status_code=404, detail="Image not found")

        # Stream chunks and check client disconnect to free pooled TCP socket immediately
        if isinstance(getattr(resp, "content", None), aiohttp.StreamReader):
            chunks = []
            async for chunk in resp.content.iter_chunked(16384):
                if await _all_subscribers_disconnected(file_id):
                    resp.close()
                    raise asyncio.CancelledError("All clients disconnected during download")
                chunks.append(chunk)
            content = b"".join(chunks)
        else:
            content = await resp.read()

    # 3. Determine content type
    content_type = "image/jpeg"
    if file_path.endswith(".png"):
        content_type = "image/png"
    elif file_path.endswith(".webp"):
        content_type = "image/webp"

    # 4. Save into in-memory LRU cache
    async with _image_cache_lock:
        _image_cache[file_id] = (content, content_type)
        if len(_image_cache) > _IMAGE_CACHE_MAX_ITEMS:
            _image_cache.popitem(last=False)

    return content, content_type


@router.get("/images/{file_id}")
async def get_image(file_id: str, request: Request):
    """Proxy image from Telegram with in-memory LRU cache, deduplication, and disconnect checking."""
    # Validate file_id format
    if not file_id or len(file_id) < 20 or len(file_id) > 200:
        raise HTTPException(status_code=400, detail="Invalid file ID")
    if not re.match(r'^[A-Za-z0-9_\-]+$', file_id):
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    # 1. Quick check: In-Memory LRU Cache hit
    async with _image_cache_lock:
        if file_id in _image_cache:
            _image_cache.move_to_end(file_id)
            content, content_type = _image_cache[file_id]
            etag = f'"{file_id}"'
            if _is_etag_matched(request, etag):
                return Response(
                    status_code=304,
                    headers={
                        "ETag": etag,
                        "Cache-Control": "public, max-age=604800, immutable",
                    },
                )
            return Response(
                content=content,
                media_type=content_type,
                headers={
                    "ETag": etag,
                    "Cache-Control": "public, max-age=604800, immutable",
                },
            )

    # 2. Check early client disconnect
    if request and await request.is_disconnected():
        return Response(status_code=499)

    # 3. In-flight request deduplication
    async with _inflight_lock:
        if request:
            _inflight_subscribers[file_id].add(request)
        task = _inflight_image_tasks.get(file_id)
        if task is None or task.done():
            task = asyncio.create_task(_fetch_and_cache_telegram_image(file_id))
            _inflight_image_tasks[file_id] = task

    try:
        content, content_type = await asyncio.shield(task)

        # Check disconnect right before sending response
        if request and await request.is_disconnected():
            return Response(status_code=499)

        etag = f'"{file_id}"'
        if _is_etag_matched(request, etag):
            return Response(
                status_code=304,
                headers={
                    "ETag": etag,
                    "Cache-Control": "public, max-age=604800, immutable",
                },
            )

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=604800, immutable",
            },
        )
    except asyncio.CancelledError:
        return Response(status_code=499)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to proxy image {file_id[:30]}...: {e}")
        raise HTTPException(status_code=404, detail="Image not found")
    finally:
        async with _inflight_lock:
            if request:
                subs = _inflight_subscribers.get(file_id)
                if subs:
                    subs.discard(request)
            # Clean up task & subscribers map only when task is done and no subscribers remain
            subs = _inflight_subscribers.get(file_id)
            if task.done() and (not subs or len(subs) == 0):
                if _inflight_image_tasks.get(file_id) is task:
                    _inflight_image_tasks.pop(file_id, None)
                    _inflight_subscribers.pop(file_id, None)
