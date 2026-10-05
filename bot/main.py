"""
TG Player Bot v2 - Entry Point

Uses new modular service architecture.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import ExceptionTypeFilter

from shared.config import get_settings, get_proxy_url
from shared.bot_factory import create_bot
from shared.database import init_db, close_db

from bot.handlers.menu import router as menu_router
from bot.handlers.inline import router as inline_router
from bot.handlers.ingestion import router as ingestion_router
from bot.handlers.audio import router as audio_router
from bot.handlers.download import router as download_router
from bot.handlers.channel_pins import router as channel_pins_router
from bot.handlers.channel_events import router as channel_events_router

from bot.services.enrichment import enrichment_worker
from bot.services.channels import init_channel_service, start_channel_service, stop_channel_service


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point"""
    settings = get_settings()
    proxy = get_proxy_url()
    if proxy:
        logger.info(f"🌐 Прокси активен: {proxy.split('@')[-1] if '@' in proxy else proxy}")
    else:
        logger.warning("⚠️ Прокси не настроен! Сервисы Spotify, YouTube и Last.fm могут быть недоступны в РФ. Добавьте PROXY_URL в .env")
    
    # Initialize database
    logger.info("Initializing database...")
    await init_db()
    
    # Start enrichment worker
    logger.info("Starting enrichment worker...")
    await enrichment_worker.start(
        idle_interval=60,   # Check every 60s when no pending tracks
        busy_interval=5     # Check every 5s when processing
    )
    
    # Initialize bot
    bot = create_bot()

    # Detect bot username if not set
    try:
        me = await bot.get_me()
        if me.username:
            settings.bot_username = me.username
        if me.first_name:
            settings.bot_name = me.first_name
        logger.info(f"Bot connected: {settings.display_name} (@{settings.bot_username or me.username}, ID: {me.id})")
    except Exception as e:
        logger.warning(f"Failed to fetch bot user info: {e}")
    
    # Initialize channel service with bot
    init_channel_service(bot)
    
    # Start channel forward queue worker
    logger.info("Starting channel forward queue worker...")
    await start_channel_service()
    
    # Initialize dispatcher with FSM storage
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Safely suppress harmless Telegram "message is not modified" errors
    @dp.error(ExceptionTypeFilter(TelegramBadRequest))
    async def handle_telegram_bad_request(event: ErrorEvent):
        if "message is not modified" in str(event.exception).lower():
            if event.update.callback_query:
                try:
                    await event.update.callback_query.answer()
                except Exception:
                    pass
            return True
        return False
    
    # Register routers — menu_router first (handles /start, /menu, all menu callbacks)
    dp.include_router(menu_router)
    dp.include_router(inline_router)
    dp.include_router(ingestion_router)
    dp.include_router(audio_router)
    dp.include_router(download_router)
    dp.include_router(channel_pins_router)
    dp.include_router(channel_events_router)
    
    # Start polling
    logger.info("Starting bot v2...")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down...")
        await stop_channel_service()
        await enrichment_worker.stop()
        await close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
