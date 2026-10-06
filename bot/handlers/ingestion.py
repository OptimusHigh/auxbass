"""
TG Player Bot - External Music Ingestion Handler
Handles URL messages from supported music platforms (SoundCloud, etc.)
"""
import re
import time
import logging
import asyncio
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from shared.database import get_session
from shared.models import User, Track
from shared.config import get_settings
from bot.services.tracks import track_service
from bot.services.ingestion import (
    provider_registry,
    job_manager,
    IngestionPipeline,
    EntityType,
    JobStatus,
    IngestionJob,
)
from bot.handlers.menu_keyboards import get_webapp_keyboard, get_deep_link_keyboard
from bot.services.delivery import deliver_single_track, get_track_player_button

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()

URL_EXTRACT_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


async def _ensure_user_exists(user_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str]):
    """Ensure user record exists in DB."""
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            user = User(
                id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            session.add(user)
            await session.commit()


@router.message(F.text, F.text.regexp(r"https?://(?:(?:m|www|music)\.)?(?:soundcloud\.com|on\.soundcloud\.com|open\.spotify\.com|spotify\.link|youtube\.com|youtu\.be)/\S+"))
async def handle_music_url_message(message: Message):
    """Detect and process external music URLs sent to the bot."""
    match = URL_EXTRACT_PATTERN.search(message.text)
    if not match:
        return

    url = match.group(0).strip()
    provider = provider_registry.find_provider(url)
    if not provider:
        return

    user = message.from_user
    if not user:
        return

    await _ensure_user_exists(user.id, user.username, user.first_name, user.last_name)

    status_msg = await message.reply(
        f"🔍 <i>Анализирую ссылку {provider.name.title()}...</i>",
        parse_mode="HTML"
    )

    try:
        entity = await provider.resolve_entity(url)
    except Exception as e:
        logger.warning(f"Failed to resolve URL {url}: {e}")
        await status_msg.edit_text(
            f"❌ Не удалось получить информацию по ссылке: {str(e)[:100]}",
            parse_mode="HTML"
        )
        return

    # Create background job
    job = await job_manager.create_job(
        user_id=user.id,
        url=url,
        provider_name=provider.name,
        entity_type=entity.entity_type.value,
        title=entity.title,
        total_tracks=entity.track_count,
        author=entity.author,
        cover_url=entity.cover_url,
        raw_data=entity.raw_data,
    )

    # 1. Single track flow
    if entity.entity_type == EntityType.TRACK:
        await status_msg.edit_text(
            f"⏳ <b>Скачиваю:</b> {entity.author} — {entity.title}...\n"
            f"<i>Загрузка в высоком качестве и добавление в библиотеку Auxbass</i>",
            parse_mode="HTML"
        )

        pipeline = IngestionPipeline(message.bot)
        task = asyncio.create_task(pipeline.execute_job(job))
        job_manager.register_task(job.id, task)

        # Wait for track import
        try:
            await task
        except Exception as e:
            logger.error(f"Track ingestion task crashed: {e}")

        if job.status == JobStatus.COMPLETED and job.imported_track_ids:
            track_id = job.imported_track_ids[0]

            # If audio was uploaded directly to this chat during pipeline,
            # attach caption and player markup to that message to avoid sending duplicates
            if job.uploaded_chat_id == message.chat.id and job.uploaded_message_id:
                try:
                    caption = (
                        f"🎧 <b>{entity.author} — {entity.title}</b>\n\n"
                        f"☁️ <i>{provider.name.title()}</i>"
                    )
                    await message.bot.edit_message_caption(
                        chat_id=message.chat.id,
                        message_id=job.uploaded_message_id,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=get_track_player_button(track_id),
                    )
                except Exception as e:
                    logger.debug(f"Could not edit caption on uploaded audio: {e}")

                try:
                    await status_msg.delete()
                except Exception:
                    pass
            else:
                # Track was either deduplicated from existing DB or uploaded to buffer chat.
                # Send audio directly to the user's chat!
                delivered = await deliver_single_track(
                    bot=message.bot,
                    chat_id=message.chat.id,
                    track_id=track_id,
                    user_id=user.id,
                    reply_to_message_id=message.message_id,
                    send_failure_message=False,
                )
                if delivered:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                else:
                    # Check if the existing track had an invalid/dead Telegram file_id
                    re_sourced = False
                    try:
                        async with get_session() as s:
                            t = await s.get(Track, track_id)
                            is_dead = bool(t and t.is_unavailable)

                        if is_dead:
                            logger.info(f"[Ingestion] Existing track {track_id} had dead file_id. Auto re-sourcing audio stream...")
                            await status_msg.edit_text(
                                f"⏳ <b>Восстанавливаю аудиопоток:</b> {entity.author} — {entity.title}...\n"
                                f"<i>Старая ссылка Telegram устарела, скачиваю свежий поток</i>",
                                parse_mode="HTML"
                            )
                            fixed_track = await track_service.re_source_audio(
                                bot=message.bot,
                                track_id=track_id,
                                user_id=user.id,
                                target_chat_id=message.chat.id,
                            )
                            if fixed_track:
                                re_sourced = True
                    except Exception as re_err:
                        logger.error(f"[Ingestion] Auto re-source failed for track {track_id}: {re_err}")

                    if re_sourced:
                        try:
                            await status_msg.delete()
                        except Exception:
                            pass
                    else:
                        await status_msg.edit_text(
                            f"✅ <b>Трек добавлен в библиотеку!</b>\n\n"
                            f"🎵 <b>{entity.author} — {entity.title}</b>\n"
                            f"☁️ Источник: <i>{provider.name.title()}</i>",
                            reply_markup=get_deep_link_keyboard(f"track_{track_id}", "▶️ Слушать в плеере"),
                            parse_mode="HTML"
                        )
        else:
            err = job.error_message or "Не удалось обработать аудиофайл"
            await status_msg.edit_text(
                f"❌ Ошибка при импорте трека: {err}",
                parse_mode="HTML"
            )
        return

    # 2. Playlist / Album flow
    is_album = (entity.entity_type == EntityType.ALBUM)
    type_label = "Альбом" if is_album else "Плейлист"
    type_icon = "💿" if is_album else "📁"

    await status_msg.edit_text(
        f"{type_icon} <b>{entity.title}</b>\n"
        f"👤 Автор: <b>{entity.author}</b>\n"
        f"🎵 Найдено треков: <b>{entity.track_count}</b>\n\n"
        f"⏳ <i>Начинаю импорт {type_label.lower()}а в библиотеку Auxbass...</i>",
        parse_mode="HTML"
    )

    last_edit_time = 0

    async def on_progress(updated_job: IngestionJob):
        nonlocal last_edit_time
        now = time.time()
        # Debounce message edits to avoid Telegram FloodWait (every 3 seconds max)
        if now - last_edit_time < 3.0 and updated_job.status == JobStatus.IN_PROGRESS:
            return

        last_edit_time = now
        pct = updated_job.progress_percent
        cur = updated_job.current_track_title or ""

        text = (
            f"{type_icon} <b>Импорт {type_label.lower()}а: {entity.title}</b>\n"
            f"📊 Прогресс: <b>{updated_job.processed_tracks}/{updated_job.total_tracks}</b> ({pct}%)\n"
        )
        if cur:
            text += f"🎵 Текущий: <i>{cur[:50]}</i>\n"

        try:
            await status_msg.edit_text(text, parse_mode="HTML")
        except TelegramBadRequest:
            pass

    pipeline = IngestionPipeline(message.bot)
    task = asyncio.create_task(pipeline.execute_job(job, progress_callback=on_progress))
    job_manager.register_task(job.id, task)

    # Let it run in background so bot doesn't block long playlists
    async def wait_and_finish():
        try:
            await task
        except Exception as e:
            logger.error(f"Playlist ingestion error: {e}")

        if job.status == JobStatus.COMPLETED:
            kb_rows = []
            bot_user = settings.bot_username or "tg_player_bot"

            if job.album_id:
                kb_rows.append([
                    InlineKeyboardButton(
                        text="💿 Открыть альбом",
                        url=f"https://t.me/{bot_user}?startapp=album_{job.album_id}"
                    )
                ])

            if job.playlist_id:
                kb_rows.append([
                    InlineKeyboardButton(
                        text="📁 Открыть плейлист",
                        url=f"https://t.me/{bot_user}?startapp=playlist_{job.playlist_id}"
                    )
                ])
                kb_rows.append([
                    InlineKeyboardButton(
                        text="📥 Скачать треки в чат",
                        callback_data=f"download_playlist:{job.playlist_id}"
                    )
                ])
            elif not job.album_id:
                kb_rows = get_webapp_keyboard().inline_keyboard

            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
            try:
                await status_msg.edit_text(
                    f"✅ <b>{type_label} успешно импортирован!</b>\n\n"
                    f"{type_icon} <b>{entity.title}</b>\n"
                    f"👤 Автор: <b>{entity.author}</b>\n"
                    f"🎵 Добавлено треков: <b>{len(job.imported_track_ids)}</b> из {job.total_tracks}\n"
                    f"☁️ Источник: <i>{provider.name.title()}</i>",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            except TelegramBadRequest:
                pass
        else:
            try:
                await status_msg.edit_text(
                    f"⚠️ Импорт завершён с ошибками.\n"
                    f"Успешно: {job.processed_tracks}/{job.total_tracks}\n"
                    f"Ошибка: {job.error_message or 'Неизвестная ошибка'}",
                    parse_mode="HTML"
                )
            except TelegramBadRequest:
                pass

    asyncio.create_task(wait_and_finish())
