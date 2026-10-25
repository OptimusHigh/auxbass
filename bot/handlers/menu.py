"""
TG Player Bot - Clean Modern Menu Router

Handles:
  • Main menu (/start, /menu, /help)
  • WebApp / Browser login (/login, /web, /code)
  • Backup Channel management (/channel)
  • Library stats (/stats)
"""
import logging
from typing import Optional

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func

from shared.config import get_settings
from shared.database import get_session
from shared.models import (
    User,
    Track,
    Playlist,
    Album,
    ChannelMessage,
    ChannelMessageStatus,
    UserChannel,
    UserLibrary,
)

from bot.services import track_service, channel_service
from bot.services.delivery import (
    deliver_playlist_tracks,
    deliver_single_track,
    deliver_album_tracks,
)
from bot.handlers.menu_keyboards import (
    get_main_menu_keyboard,
    get_channel_not_connected_keyboard,
    get_channel_main_keyboard,
    get_channel_settings_keyboard,
    get_channel_disconnect_confirm_keyboard,
    get_channel_setup_waiting_keyboard,
    get_channel_operation_keyboard,
    get_channel_back_keyboard,
    get_stats_menu_keyboard,
    get_deep_link_keyboard,
    get_playlist_share_keyboard,
    get_album_share_keyboard,
)

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)


# ───────────────────────── FSM States ─────────────────────────

class MenuStates(StatesGroup):
    """FSM states for channel setup"""
    channel_waiting_forward = State()


# ───────────────────────── Helpers ─────────────────────────

async def _safe_edit_text(message: Message, text: str, **kwargs) -> bool:
    """Safely edit message text, suppressing 'message is not modified' Telegram errors.

    Returns:
        True if the message was updated, False if it was not modified.
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return False
        raise


async def _get_channel_status_text(user_id: int) -> tuple[str, Optional[object]]:
    """Build channel section text."""
    channel = await channel_service.get_user_channel(user_id)
    if not channel:
        return (
            "☁️ <b>Резервное хранилище (Канал)</b>\n\n"
            "Канал не подключён.\n\n"
            "Подключите приватный канал, чтобы бот автоматически "
            "сохранял копии всех ваших аудиофайлов с хэштегами.\n"
            "Музыка в Telegram хранится бессрочно и бесплатно.",
            None,
        )

    # Counts by status
    async with get_session() as session:
        rows = (
            await session.execute(
                select(ChannelMessage.status, func.count(ChannelMessage.id))
                .where(ChannelMessage.channel_id == channel.id)
                .group_by(ChannelMessage.status)
            )
        ).all()

    counts = {s: c for s, c in rows}
    sent = counts.get(ChannelMessageStatus.SENT, 0) + counts.get("sent", 0)
    pending = counts.get(ChannelMessageStatus.PENDING, 0) + counts.get("pending", 0)
    failed = counts.get(ChannelMessageStatus.FAILED, 0) + counts.get("failed", 0)

    status_lines = [
        f"☁️ <b>Резервное хранилище</b>\n",
        f"📢 Канал: <b>{channel.channel_title or 'Приватный канал'}</b>",
        f"✅ Статус: <b>Активен</b>\n",
        f"🎵 Сохранено треков: <b>{sent}</b>",
    ]
    if pending:
        status_lines.append(f"⏳ В очереди отправки: <b>{pending}</b>")
    if failed:
        status_lines.append(f"❌ Ошибок: <b>{failed}</b>")

    status_lines.append("\n💡 <i>Отправьте файл <code>result.json</code> из экспорта Telegram Desktop для быстрого импорта.</i>")

    return "\n".join(status_lines), channel


async def _get_stats_text(user_id: int) -> str:
    """Build statistics text."""
    stats = await track_service.get_library_stats(user_id)

    total = stats["total_tracks"]
    albums = stats["album_count"]
    dur_sec = stats.get("total_duration_seconds", 0)
    hours = dur_sec // 3600
    minutes = (dur_sec % 3600) // 60

    # Artist count
    async with get_session() as session:
        artist_count = (
            await session.scalar(
                select(func.count(func.distinct(Track.normalized_artist)))
                .join(UserLibrary, UserLibrary.track_id == Track.id)
                .where(
                    UserLibrary.user_id == user_id,
                    Track.normalized_artist.isnot(None),
                    Track.normalized_artist != "",
                )
            )
        ) or 0

    return (
        "📊 <b>Ваша библиотека</b>\n\n"
        f"🎵 Всего треков: <b>{total}</b>\n"
        f"💿 Альбомов: <b>{albums}</b>\n"
        f"👤 Исполнителей: <b>{artist_count}</b>\n"
        f"⏱ Общее время звучания: <b>{hours}ч {minutes}мин</b>\n\n"
        "Слушайте треки, создавайте плейлисты и настраивайте порядок в плеере."
    )


# ═══════════════════════════════════════════════════════════
#                    COMMANDS
# ═══════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject = None):
    """Welcome message + register user + main menu"""
    user = message.from_user
    async with get_session() as session:
        db_user = await session.get(User, user.id)
        if not db_user:
            db_user = User(
                id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            session.add(db_user)
        else:
            db_user.username = user.username
            db_user.first_name = user.first_name
            db_user.last_name = user.last_name
        await session.commit()

    await state.clear()

    if command and command.args:
        args = command.args.strip()
        if args == "open_player":
            await message.answer(
                f"🎵 <b>{settings.display_name}</b> — твоя персональная музыкальная библиотека.\n\n"
                "Нажми кнопку ниже, чтобы открыть плеер:",
                reply_markup=get_main_menu_keyboard()
            )
            return
        elif args.startswith("files_playlist_") or args.startswith("files_pl_"):
            try:
                pid = int(args.replace("files_playlist_", "").replace("files_pl_", ""))
                await deliver_playlist_tracks(message.bot, chat_id=message.chat.id, playlist_id=pid, user_id=user.id)
                return
            except Exception as e:
                logger.error(f"Failed to deliver playlist from start arg {args}: {e}")
        elif args.startswith("playlist_"):
            try:
                pid = int(args.replace("playlist_", ""))
                async with get_session() as session:
                    pl = await session.get(Playlist, pid)
                    if pl:
                        await message.answer(
                            f"👋 Привет, <b>{user.first_name}</b>!\n\n"
                            f"🎧 Вам отправили плейлист «<b>{pl.name}</b>» в <b>{settings.display_name}</b>.\n\n"
                            "Выберите действие:",
                            reply_markup=get_playlist_share_keyboard(pid),
                        )
                        return
            except Exception as e:
                logger.error(f"Failed to show playlist preview for {args}: {e}")
            await message.answer(
                f"👋 Привет, <b>{user.first_name}</b>!\n\n"
                f"🎧 Вам отправили плейлист в <b>{settings.display_name}</b>.\n\n"
                "Нажмите кнопку ниже, чтобы послушать его в плеере:",
                reply_markup=get_deep_link_keyboard(args, "🎧 Открыть плейлист"),
            )
            return
        elif args.startswith("files_track_") or args.startswith("track_"):
            try:
                tid = int(args.replace("files_track_", "").replace("track_", ""))
                sent = await deliver_single_track(message.bot, chat_id=message.chat.id, track_id=tid, user_id=user.id)
                if sent:
                    return
            except Exception as e:
                logger.error(f"Failed to send track from start arg {args}: {e}")
            await message.answer(
                f"👋 Привет, <b>{user.first_name}</b>!\n\n"
                f"🎵 Вам отправили трек в <b>{settings.display_name}</b>.\n\n"
                "Нажмите кнопку ниже, чтобы включить его в плеере:",
                reply_markup=get_deep_link_keyboard(args, "🎵 Слушать трек"),
            )
            return
        elif args.startswith("files_album_"):
            try:
                aid = int(args.replace("files_album_", ""))
                await deliver_album_tracks(message.bot, chat_id=message.chat.id, album_id=aid, user_id=user.id)
                return
            except Exception as e:
                logger.error(f"Failed to deliver album from start arg {args}: {e}")
        elif args.startswith("album_"):
            try:
                aid = int(args.replace("album_", ""))
                async with get_session() as session:
                    al = await session.get(Album, aid)
                    if al:
                        await message.answer(
                            f"👋 Привет, <b>{user.first_name}</b>!\n\n"
                            f"💿 Вам отправили альбом «<b>{al.name}</b>» ({al.artist or 'Неизвестен'}) в <b>{settings.display_name}</b>.\n\n"
                            "Выберите действие:",
                            reply_markup=get_album_share_keyboard(aid),
                        )
                        return
            except Exception as e:
                logger.error(f"Failed to show album preview for {args}: {e}")
            await message.answer(
                f"👋 Привет, <b>{user.first_name}</b>!\n\n"
                f"💿 Вам отправили альбом в <b>{settings.display_name}</b>.\n\n"
                "Нажмите кнопку ниже, чтобы послушать его в плеере:",
                reply_markup=get_deep_link_keyboard(args, "💿 Слушать альбом"),
            )
            return
        elif args.startswith("user_"):
            await message.answer(
                f"👋 Привет, <b>{user.first_name}</b>!\n\n"
                f"👤 Вам отправили профиль пользователя в <b>{settings.display_name}</b>.\n\n"
                "Нажмите кнопку ниже, чтобы открыть его медиатеку:",
                reply_markup=get_deep_link_keyboard(args, "👤 Открыть профиль"),
            )
            return

    await message.answer(
        f"👋 Привет, <b>{user.first_name}</b>!\n\n"
        f"🎵 <b>{settings.display_name}</b> — твоя персональная музыкальная библиотека.\n\n"
        "• Отправляй сюда любые аудиофайлы (по одному или пачками)\n"
        "• Бот сам подтянет обложки, альбомы и авторов\n"
        "• Слушай музыку прямо в Telegram или в браузере\n\n"
        "Нажми кнопку ниже, чтобы открыть плеер:",
        reply_markup=get_main_menu_keyboard(),
    )


@router.message(Command("menu", "help", "library", "player"))
async def cmd_menu(message: Message, state: FSMContext):
    """Show main menu"""
    await state.clear()
    await message.answer(
        f"🎵 <b>{settings.display_name} — Меню</b>\n\n"
        "Отправляй треки в чат для добавления в библиотеку.",
        reply_markup=get_main_menu_keyboard(),
    )


@router.message(Command("guide", "help_guide"))
async def cmd_guide(message: Message, state: FSMContext):
    """Show player guide"""
    await state.clear()
    from aiogram.types import WebAppInfo
    sep = "&" if "?" in settings.webapp_url else "?"
    guide_url = f"{settings.webapp_url}{sep}startapp=guide"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Открыть гайд по плееру", web_app=WebAppInfo(url=guide_url))]
    ])
    await message.answer(
        f"📖 <b>Гайдлайн по {settings.display_name}</b>\n\n"
        "Узнай, как настроить резервный канал, искать музыку по ссылкам и слушать треки в Hi-Fi качестве без интернета.",
        reply_markup=kb,
    )


@router.message(Command("channel"))
async def cmd_channel(message: Message, state: FSMContext):
    """Channel section"""
    await state.clear()
    await _show_channel_section(message, message.from_user.id)


@router.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext):
    """Stats section"""
    await state.clear()
    text = await _get_stats_text(message.from_user.id)
    await message.answer(text, reply_markup=get_stats_menu_keyboard())


@router.message(Command("login", "code", "web"))
async def cmd_login(message: Message):
    """Generate auth code for browser login"""
    import aiohttp

    user = message.from_user
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{settings.api_url}/api/auth/generate-code",
                params={
                    "user_id": user.id,
                    "first_name": user.first_name,
                    "last_name": user.last_name or "",
                    "username": user.username or "",
                },
                headers={"X-Bot-Secret": settings.secret_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    code = data["code"]
                    expires_in = data["expires_in"] // 60
                    await message.answer(
                        f"🔐 <b>Код для входа в браузере:</b>\n\n"
                        f"<code>{code}</code>\n\n"
                        f"⏱ Код действителен {expires_in} минут.\n\n"
                        f"🌐 Откройте <b>{settings.webapp_url}</b> в браузере "
                        f"и введите этот код.",
                    )
                else:
                    await message.answer("❌ Не удалось получить код. Попробуйте позже.")
    except Exception:
        await message.answer("❌ Ошибка подключения к серверу. Попробуйте позже.")


# ═══════════════════════════════════════════════════════════
#                 MAIN MENU  callbacks
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit_text(
        callback.message,
        "🎵 <b>TG Player — Меню</b>\n\n"
        "Отправляй треки в чат для добавления в библиотеку.",
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "lib:login")
async def cb_library_login(callback: CallbackQuery):
    """Generate login code from menu"""
    import aiohttp

    user = callback.from_user
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{settings.api_url}/api/auth/generate-code",
                params={
                    "user_id": user.id,
                    "first_name": user.first_name,
                    "last_name": user.last_name or "",
                    "username": user.username or "",
                },
                headers={"X-Bot-Secret": settings.secret_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    code = data["code"]
                    expires_in = data["expires_in"] // 60
                    await callback.message.edit_text(
                        f"🔐 <b>Код для входа в браузере:</b>\n\n"
                        f"<code>{code}</code>\n\n"
                        f"⏱ Действителен: {expires_in} минут.\n"
                        f"🌐 Откройте <b>{settings.webapp_url}</b> и введите код.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
                        ]),
                    )
                else:
                    await callback.answer("❌ Ошибка получения кода", show_alert=True)
    except Exception:
        await callback.answer("❌ Сервер недоступен", show_alert=True)


# ═══════════════════════════════════════════════════════════
#                 CHANNEL  section
# ═══════════════════════════════════════════════════════════

async def _show_channel_section(
    target: Message | CallbackQuery, user_id: int, *, edit: bool = False
):
    text, channel = await _get_channel_status_text(user_id)
    if channel:
        kb = get_channel_main_keyboard(channel.channel_username)
    else:
        kb = get_channel_not_connected_keyboard()

    if isinstance(target, CallbackQuery):
        await _safe_edit_text(target.message, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "menu:channel")
async def cb_channel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_channel_section(callback, callback.from_user.id, edit=True)
    await callback.answer()


@router.callback_query(F.data == "ch:setup")
async def cb_channel_setup(callback: CallbackQuery, state: FSMContext):
    bot_info = await callback.bot.get_me()
    await state.set_state(MenuStates.channel_waiting_forward)
    await callback.message.edit_text(
        "🔗 <b>Подключение резервного канала</b>\n\n"
        "1. Создайте приватный канал в Telegram\n"
        f"2. Добавьте бота @{bot_info.username} администратором (с правом публикации)\n"
        "3. Перешлите мне любое сообщение из этого канала",
        reply_markup=get_channel_setup_waiting_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ch:connect:"))
async def cb_channel_auto_connect(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    channel_id_str = callback.data.split(":", 2)[2]
    try:
        channel_id = int(channel_id_str)
        channel_obj = await channel_service.setup_channel(
            user_id=callback.from_user.id,
            channel_id=channel_id,
            bot=callback.bot,
        )
        if channel_obj:
            # Also reconcile tracks
            restored = await channel_service.reconcile_channel_tracks(callback.from_user.id)
            restored_str = f"\n\n♻️ Восстановлен доступ к <b>{restored}</b> трекам." if restored > 0 else ""
            await callback.message.edit_text(
                f"✅ <b>Канал подключён!</b>\n\n"
                f"📢 <b>{channel_obj.channel_title or 'Резервный канал'}</b>\n\n"
                f"Все новые аудиофайлы будут автоматически пересылаться в этот канал.{restored_str}",
                reply_markup=get_channel_back_keyboard(),
            )
        else:
            await callback.message.edit_text(
                "❌ Не удалось подключить канал. Убедитесь, что у бота есть права администратора.",
                reply_markup=get_channel_back_keyboard(),
            )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка подключения канала: {e}",
            reply_markup=get_channel_back_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "ch:setup_cancel")
async def cb_channel_setup_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_channel_section(callback, callback.from_user.id, edit=True)
    await callback.answer()


@router.message(MenuStates.channel_waiting_forward)
async def fsm_channel_forward(message: Message, state: FSMContext):
    """Receive forwarded message from user's channel"""
    if not message.forward_from_chat:
        await message.answer(
            "❌ Перешлите сообщение из вашего канала.\n"
            "Убедитесь, что бот назначен администратором.",
            reply_markup=get_channel_setup_waiting_keyboard(),
        )
        return

    chat = message.forward_from_chat
    if chat.type != "channel":
        await message.answer(
            "❌ Это не канал. Перешлите сообщение именно из канала.",
            reply_markup=get_channel_setup_waiting_keyboard(),
        )
        return

    try:
        channel_obj = await channel_service.setup_channel(
            user_id=message.from_user.id,
            channel_id=chat.id,
            channel_username=chat.username,
            channel_title=chat.title,
            bot=message.bot,
        )
        if channel_obj:
            await state.clear()
            await message.answer(
                f"✅ <b>Канал подключён!</b>\n\n"
                f"📢 <b>{chat.title}</b>\n\n"
                "Все новые аудиофайлы будут автоматически пересылаться в этот канал.",
                reply_markup=get_channel_back_keyboard(),
            )
        else:
            await message.answer(
                "❌ Не удалось подключить канал. Проверьте права администратора у бота.",
                reply_markup=get_channel_setup_waiting_keyboard(),
            )
    except Exception as e:
        await message.answer(
            f"❌ Ошибка: {e}",
            reply_markup=get_channel_setup_waiting_keyboard(),
        )


@router.callback_query(F.data == "ch:help")
async def cb_channel_help(callback: CallbackQuery):
    await _safe_edit_text(
        callback.message,
        "❓ <b>Зачем нужен канал?</b>\n\n"
        "Канал Telegram — это ваше бесплатное вечное хранилище музыки.\n\n"
        "• Все треки хранятся в вашем личном канале с хэштегами\n"
        "• Музыка останется с вами, даже если с ботом что-то случится\n"
        "• Доступ к трекам с любых устройств в Telegram\n\n"
        "<b>Как подключить:</b>\n"
        "1. Создайте канал\n"
        "2. Добавьте бота администратором\n"
        "3. Перешлите сообщение из канала боту",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:channel")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "ch:import_help")
async def cb_channel_import_help(callback: CallbackQuery):
    await _safe_edit_text(
        callback.message,
        "📥 <b>Быстрый импорт через экспорт чата</b>\n\n"
        "Если в вашем канале уже есть сотни или тысячи треков:\n\n"
        "1. Откройте <b>Telegram Desktop</b> на компьютере\n"
        "2. Зайдите в ваш музыкальный канал -> меню <b>три точки (⋮)</b>\n"
        "3. Выберите <b>«Экспорт истории чата»</b>\n"
        "4. Выберите формат <b>JSON</b> (галочку на скачивание самих файлов можно снять)\n"
        "5. Отправьте полученный файл <code>result.json</code> сюда в бота!\n\n"
        "⚡ Бот мгновенно добавит всю музыку в библиотеку без долгого сканирования.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Мой канал", callback_data="menu:channel")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "ch:restore")
async def cb_channel_restore(callback: CallbackQuery):
    """Sync tracks missing from channel"""
    user_id = callback.from_user.id

    if channel_service.is_sync_active(user_id):
        await callback.answer("Синхронизация уже запущена!", show_alert=True)
        return

    channel = await channel_service.get_user_channel(user_id)
    if not channel:
        await callback.answer("Канал не найден", show_alert=True)
        return

    stats = await channel_service.get_sync_stats(user_id)
    if stats.get("error"):
        await callback.answer(stats["error"], show_alert=True)
        return

    if stats["to_sync"] == 0:
        await callback.message.edit_text(
            f"✅ <b>Все треки на месте!</b>\n\n"
            f"📢 {stats['channel_title']}\n"
            f"🎵 В канале: <b>{stats['already_synced']}</b> из <b>{stats['total_tracks']}</b>",
            reply_markup=get_channel_back_keyboard(),
        )
        await callback.answer()
        return

    title = stats["channel_title"]
    to_sync = stats["to_sync"]

    await callback.message.edit_text(
        f"📤 <b>Отправка треков в канал...</b>\n\n"
        f"📢 {title}\n"
        f"⏳ 0/{to_sync}",
        reply_markup=get_channel_operation_keyboard("ch:op_cancel"),
    )
    await callback.answer()

    async def progress(current, total, synced):
        try:
            await callback.message.edit_text(
                f"📤 <b>Отправка треков в канал...</b>\n\n"
                f"📢 {title}\n"
                f"⏳ {synced}/{total}",
                reply_markup=get_channel_operation_keyboard("ch:op_cancel"),
            )
        except Exception:
            pass

    result = await channel_service.sync_all_tracks(
        user_id=user_id, bot=callback.bot, progress_callback=progress
    )

    if result.get("error"):
        await callback.message.edit_text(
            f"❌ Ошибка: {result['error']}",
            reply_markup=get_channel_back_keyboard(),
        )
        return

    if result.get("cancelled"):
        await callback.message.edit_text(
            f"⛔ <b>Прервано</b>\n\n"
            f"📤 Отправлено: <b>{result['synced']}</b>\n"
            f"⏭ Пропущено: <b>{result['skipped']}</b>",
            reply_markup=get_channel_back_keyboard(),
        )
        return

    await callback.message.edit_text(
        f"✅ <b>Синхронизация завершена!</b>\n\n"
        f"📤 Отправлено в канал: <b>{result['synced']}</b>\n"
        f"⏭ Уже было: <b>{result['skipped']}</b>\n"
        f"❌ Ошибок: <b>{result['failed']}</b>",
        reply_markup=get_channel_back_keyboard(),
    )


@router.callback_query(F.data == "ch:settings")
async def cb_channel_settings(callback: CallbackQuery):
    channel = await channel_service.get_user_channel(callback.from_user.id)
    if not channel:
        await callback.answer("Канал не найден", show_alert=True)
        return

    await _safe_edit_text(
        callback.message,
        f"⚙️ <b>Настройки канала</b>\n\n"
        f"📢 {channel.channel_title or 'Канал'}\n"
        f"#️⃣ Авто-хэштеги: {'Включены' if channel.include_hashtags else 'Выключены'}",
        reply_markup=get_channel_settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "ch:disconnect_confirm")
async def cb_channel_disconnect_confirm(callback: CallbackQuery):
    await _safe_edit_text(
        callback.message,
        "⚠️ <b>Отключить канал?</b>\n\n"
        "Треки останутся в канале, но новые не будут отправляться.",
        reply_markup=get_channel_disconnect_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "ch:disconnect")
async def cb_channel_disconnect(callback: CallbackQuery):
    await channel_service.disable_channel(callback.from_user.id)
    await _safe_edit_text(
        callback.message,
        "✅ Канал отключён.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
        ]),
    )
    await callback.answer("Канал отключён")


@router.callback_query(F.data == "ch:op_cancel")
async def cb_channel_op_cancel(callback: CallbackQuery):
    channel_service.request_cancel_sync(callback.from_user.id)
    await callback.answer("Прерывание...", show_alert=True)


# ═══════════════════════════════════════════════════════════
#                 STATISTICS  section
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:stats")
async def cb_stats(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = await _get_stats_text(callback.from_user.id)
    await _safe_edit_text(callback.message, text, reply_markup=get_stats_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "stats:refresh")
async def cb_stats_refresh(callback: CallbackQuery):
    text = await _get_stats_text(callback.from_user.id)
    modified = await _safe_edit_text(
        callback.message, text, reply_markup=get_stats_menu_keyboard()
    )
    if modified:
        await callback.answer("Обновлено!")
    else:
        await callback.answer("Данные актуальны!")


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit_text(callback.message, "❌ Отменено.")
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
