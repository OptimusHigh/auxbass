"""
TG Player - Bot Factory
Creates aiogram Bot instances with proxy support and common defaults.
"""
from typing import Optional
import os
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from shared.config import get_settings, get_proxy_url


def create_bot(
    token: Optional[str] = None,
    parse_mode: ParseMode = ParseMode.HTML,
    session: Optional[AiohttpSession] = None,
) -> Bot:
    """Create a new Bot instance configured with proxy if available."""
    settings = get_settings()
    bot_token = token or settings.bot_token
    proxy = get_proxy_url()

    if session is None and proxy:
        session = AiohttpSession(proxy=proxy)

    return Bot(
        token=bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=parse_mode),
    )
