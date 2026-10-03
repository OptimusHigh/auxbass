"""
TG Player API - Shared Bot & HTTP Session Helpers

Singleton factories for Bot instance and aiohttp session,
used across images, player, playlists, and auth routers.
"""
from typing import Optional
import logging

import aiohttp
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from shared.config import get_settings
from shared.bot_factory import create_bot

logger = logging.getLogger(__name__)
settings = get_settings()


# ============== Shared Bot Instance ==============

_image_bot: Optional[Bot] = None


def get_bot() -> Bot:
    """Get or create shared bot instance for image/file proxy operations."""
    global _image_bot
    if _image_bot is None:
        _image_bot = create_bot()
    return _image_bot


async def close_bot():
    """Close shared bot session on shutdown."""
    global _image_bot
    if _image_bot is not None:
        try:
            await _image_bot.session.close()
        except Exception as e:
            logger.warning(f"Error closing image bot session: {e}")
        _image_bot = None


# ============== Global HTTP Session Pool ==============
# Reuses TCP connections instead of creating new ones per request
# This saves ~100-200ms per request on TLS handshake

_http_session: Optional[aiohttp.ClientSession] = None


async def get_http_session() -> aiohttp.ClientSession:
    """Get or create global aiohttp session with connection pooling"""
    global _http_session
    if _http_session is None or _http_session.closed:
        # Connection pool: keep up to 100 connections, 10 per host
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=10,
            ttl_dns_cache=300,  # Cache DNS for 5 minutes
            keepalive_timeout=60,  # Keep connections alive for 60s
        )
        # Timeouts:
        # - total=None: No limit on total request time (needed for large file streaming)
        # - connect=10: 10 seconds to establish connection
        # - sock_read=60: 60 seconds max between data chunks (detects stalled connections)
        timeout = aiohttp.ClientTimeout(
            total=None,       # No total limit - files can be large!
            connect=10,       # Connection timeout
            sock_read=60,     # Read timeout between chunks
        )
        _http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=True,
        )
    return _http_session


async def close_http_session():
    """Close global HTTP session (call on app shutdown)"""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None
