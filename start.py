"""
TG Player - Unified Application Runner
Запускает одновременно FastAPI (API + WebApp статика) и Telegram Bot (aiogram)
в одном процессе. Идеально для Pterodactyl, Docker и одиночных контейнеров.
"""
import os
# Prevent SIGSEGV on Python 3.11 in container environments with incompatible precompiled C-extensions
os.environ.setdefault("PROPCACHE_NO_EXTENSIONS", "1")
os.environ.setdefault("YARL_NO_EXTENSIONS", "1")
os.environ.setdefault("MULTIDICT_NO_EXTENSIONS", "1")
os.environ.setdefault("FROZENLIST_NO_EXTENSIONS", "1")
os.environ.setdefault("AIOHTTP_NO_EXTENSIONS", "1")

import asyncio
import logging
import signal
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from shared.config import get_settings
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
from api.main import app as fastapi_app

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("tg_player.runner")


async def run_server(host: str, port: int):
    """Запуск Uvicorn сервера с FastAPI"""
    config = uvicorn.Config(
        app=fastapi_app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    logger.info(f"🌐 WebApp & API запускаются на http://{host}:{port}")
    await server.serve()


async def run_bot():
    """Запуск Telegram бота с авто-перезапуском при сетевых сбоях"""
    settings = get_settings()
    
    # Изначально бот ещё не в сети
    fastapi_app.state.bot_online = False
    
    while True:
        bot = None
        try:
            bot = create_bot()
            
            try:
                me = await bot.get_me()
                if me.username:
                    settings.bot_username = me.username
                if me.first_name:
                    settings.bot_name = me.first_name
                logger.info(f"🤖 Telegram Bot connected: {settings.display_name} (@{settings.bot_username or me.username}, ID: {me.id})")
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch bot info: {e}")
            
            init_channel_service(bot)
            await start_channel_service()
            
            storage = MemoryStorage()
            dp = Dispatcher(storage=storage)
            
            dp.include_router(menu_router)
            dp.include_router(inline_router)
            dp.include_router(ingestion_router)
            dp.include_router(audio_router)
            dp.include_router(download_router)
            dp.include_router(channel_pins_router)
            dp.include_router(channel_events_router)
            
            logger.info("🤖 Telegram Bot запускается (long polling)...")
            fastapi_app.state.bot_online = True
            await dp.start_polling(bot)
        except asyncio.CancelledError:
            logger.info("🤖 Остановка Telegram Bot...")
            break
        except Exception as e:
            fastapi_app.state.bot_online = False
            logger.error(f"⚠️ Ошибка в Telegram Bot: {e}. Перезапуск через 5 сек...")
            await asyncio.sleep(5)
        finally:
            fastapi_app.state.bot_online = False
            if bot:
                try:
                    await bot.session.close()
                except Exception:
                    pass


async def main():
    settings = get_settings()
    
    # Порт для Pterodactyl (переменные SERVER_PORT или PORT)
    port = int(os.environ.get("SERVER_PORT") or os.environ.get("PORT") or settings.api_port)
    host = os.environ.get("SERVER_HOST") or settings.api_host
    
    logger.info("==========================================")
    logger.info("       TG Player - Universal Runner       ")
    logger.info("==========================================")
    logger.info(f"Домен: {settings.webapp_url}")
    logger.info(f"База данных: {settings.database_url}")
    logger.info(f"Порт: {port}")
    if settings.proxy_url:
        logger.info(f"🌐 Прокси: {settings.proxy_url.split('@')[-1] if '@' in settings.proxy_url else settings.proxy_url}")
    
    # Инициализация состояния приложения
    fastapi_app.state.bot_online = False
    
    # Инициализация БД и фоновых воркеров
    await init_db()
    
    logger.info("Запуск enrichment worker...")
    await enrichment_worker.start(idle_interval=60, busy_interval=5)
    
    # Запускаем задачи: веб-сервер и бот
    server_task = asyncio.create_task(run_server(host, port))
    bot_task = asyncio.create_task(run_bot())
    
    try:
        # Сервер определяет время жизни контейнера
        await server_task
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Получен сигнал завершения...")
    finally:
        logger.info("Остановка сервисов...")
        if not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
        if not server_task.done():
            server_task.cancel()
        await stop_channel_service()
        await enrichment_worker.stop()
        await close_db()
        logger.info("Сервисы успешно остановлены.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
