import asyncio
import ipaddress
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, HTTPException, Query
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


@router.get("/images/proxy")
async def proxy_external_image(url: str = Query(..., description="External image URL to proxy")):
    """Proxy external cover art (SoundCloud, Spotify, Deezer, etc.) to ensure availability in restricted regions."""
    url = url.strip()
    if not url or not _is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or disallowed image URL")

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
                # If YouTube thumbnail (e.g. maxresdefault.jpg or signed sqp URL) failed with 404, fallback to hqdefault.jpg
                if resp.status == 404 and "i.ytimg.com" in url:
                    m = re.search(r"/vi/([a-zA-Z0-9_\-]{11})/", url)
                    if m:
                        vid = m.group(1)
                        fallback_yt_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                        if fallback_yt_url != url:
                            try:
                                async with session.get(fallback_yt_url, headers=headers, timeout=timeout, proxy=proxy) as fb_resp:
                                    if fb_resp.status == 200:
                                        content = await fb_resp.read()
                                        try:
                                            content, ext = crop_image_to_square(content)
                                            fb_ct = "image/jpeg" if ext == "jpg" else "image/png"
                                        except Exception:
                                            fb_ct = "image/jpeg"
                                        return Response(
                                            content=content,
                                            media_type=fb_ct,
                                            headers={"Cache-Control": "public, max-age=604800, immutable"},
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

            content = await resp.read()
            # Auto-crop YouTube 16:9 / 4:3 frames to 1:1 square, trimming side pillarbox / letterbox bars
            if "i.ytimg.com" in url or "youtube.com" in url:
                try:
                    content, ext = crop_image_to_square(content)
                    content_type = "image/jpeg" if ext == "jpg" else "image/png"
                except Exception as crop_err:
                    logger.debug(f"Failed to auto-crop YouTube thumbnail: {crop_err}")
            # Enforce max 10MB limit
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Image exceeds size limit")

            return Response(
                content=content,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=604800, immutable"},  # Cache 7 days
            )
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout fetching external image")
    except Exception as e:
        logger.warning(f"Error proxying external image {url[:60]}: {e}")
        raise HTTPException(status_code=502, detail="Failed to proxy image")


@router.get("/images/{file_id}")
async def get_image(file_id: str):
    """Proxy image from Telegram"""
    # Validate file_id format (Telegram file_ids are base64-like strings, 30-200 chars)
    if not file_id or len(file_id) < 20 or len(file_id) > 200:
        raise HTTPException(status_code=400, detail="Invalid file ID")
    # Reject obviously invalid characters (Telegram file_ids are alphanumeric + - _ )
    import re
    if not re.match(r'^[A-Za-z0-9_\-]+$', file_id):
        raise HTTPException(status_code=400, detail="Invalid file ID format")
    
    bot = _get_bot()
    
    try:
        # Get file path
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path
        
        # Construct download URL
        base_url = settings.telegram_api_url.rstrip("/")
        url = f"{base_url}/file/bot{settings.bot_token}/{file_path}"
            
        # Download and serve using shared pooled session
        session = await get_http_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=404, detail="Image not found")
            
            content = await resp.read()
            
            # Determine content type
            content_type = "image/jpeg"
            if file_path.endswith(".png"):
                content_type = "image/png"
            elif file_path.endswith(".webp"):
                content_type = "image/webp"
            
            return Response(
                content=content, 
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"}  # Cache for 24h
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to proxy image {file_id[:30]}...: {e}")
        raise HTTPException(status_code=404, detail="Image not found")
