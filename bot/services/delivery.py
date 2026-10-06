"""
TG Player Bot - Delivery Service

Handles sending audio files (tracks, playlists, albums) to Telegram chats.
Includes batching (max 10 media per media group), delay to respect rate limits,
and fallback to individual send_audio.
"""
import asyncio
import logging
from typing import Optional, List
from aiogram import Bot
from aiogram.types import (
    Message,
    InputMediaAudio,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.database import get_session
from shared.models import Track, Playlist, PlaylistTrack, Album, AlbumTrack
from shared.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_track_player_button(track_id: int) -> InlineKeyboardMarkup:
    """Button to open specific track in player"""
    bot_user = settings.bot_username or "tg_player_bot"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎵 Слушать в плеере",
                url=f"https://t.me/{bot_user}?startapp=track_{track_id}"
            )
        ]
    ])


def get_playlist_player_button(playlist_id: int) -> InlineKeyboardMarkup:
    """Button to open specific playlist in player"""
    bot_user = settings.bot_username or "tg_player_bot"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎵 Открыть в плеере",
                url=f"https://t.me/{bot_user}?startapp=playlist_{playlist_id}"
            )
        ]
    ])


async def _ensure_deliverable_file_id(track: Track, session) -> Optional[str]:
    """Ensure track has a valid Telegram file_id, resolving lazy references on-demand."""
    if not track or not track.file_id:
        return None
    if track.file_id.startswith("lazy:") or track.file_id.startswith("pending:"):
        from api.routers.player import refresh_file_id_from_channel
        fresh_file_id, err, _ = await refresh_file_id_from_channel(track.id, session)
        if fresh_file_id:
            track.file_id = fresh_file_id
            return fresh_file_id
        logger.warning(f"Failed to resolve lazy file_id for track {track.id}: {err}")
        return None
    return track.file_id


async def deliver_single_track(
    bot: Bot,
    chat_id: int,
    track_id: int,
    user_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None,
    send_failure_message: bool = True,
) -> bool:
    """Send a single audio track to chat using Telegram file_id"""
    async with get_session() as session:
        track = await session.scalar(
            select(Track)
            .options(selectinload(Track.enrichment))
            .where(Track.id == track_id)
        )
        valid_file_id = await _ensure_deliverable_file_id(track, session)
        if not valid_file_id:
            logger.warning(f"Track {track_id} not found or missing file_id")
            if send_failure_message:
                await bot.send_message(chat_id=chat_id, text="❌ Трек не найден или недоступен.")
            return False

        caption = f"🎧 <b>{track.artist or 'Неизвестен'} — {track.title or 'Без названия'}</b>"
        album_name = None
        if track.enrichment and track.enrichment.album_name:
            album_name = track.enrichment.album_name
        elif getattr(track, "album_name", None):
            album_name = track.album_name
        elif getattr(track, "album", None):
            album_name = track.album

        if album_name:
            caption += f"\n💿 <i>{album_name}</i>"
        if getattr(track, "is_chunk", False):
            caption += "\n✂️ <i>[Превью 30 сек]</i>"
        caption += f"\n\n🎵 <i>{settings.display_name}</i>"

        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=track.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                duration=track.duration or 0,
                performer=track.artist,
                title=track.title,
                reply_to_message_id=reply_to_message_id,
                reply_markup=get_track_player_button(track.id)
            )
            return True
        except Exception as e:
            err_msg = str(e).lower()
            logger.error(f"Failed to send track {track_id} to chat {chat_id}: {e}")
            if "wrong file identifier" in err_msg or "wrong remote file identifier" in err_msg:
                logger.warning(f"Marking track {track_id} unavailable due to invalid/foreign file_id")
                track.is_unavailable = True
                await session.commit()
            if send_failure_message:
                await bot.send_message(chat_id=chat_id, text="❌ Не удалось отправить аудиофайл.")
            return False


async def deliver_playlist_tracks(
    bot: Bot,
    chat_id: int,
    playlist_id: int,
    user_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None
) -> int:
    """
    Send all tracks of a playlist to chat.
    Uses send_media_group in batches of 10, falls back to individual send_audio.
    Returns number of successfully sent tracks.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Playlist)
            .options(selectinload(Playlist.tracks).selectinload(PlaylistTrack.track))
            .where(Playlist.id == playlist_id)
        )
        playlist = result.scalar()

        if not playlist:
            await bot.send_message(chat_id=chat_id, text="❌ Плейлист не найден.")
            return 0

        resolved_tracks = []
        for pt in playlist.tracks:
            if pt.track and pt.track.file_id:
                fid = await _ensure_deliverable_file_id(pt.track, session)
                if fid:
                    resolved_tracks.append(pt.track)
        tracks = resolved_tracks

        if not tracks:
            await bot.send_message(
                chat_id=chat_id,
                text=f"📁 Плейлист «<b>{playlist.name}</b>» пуст или треки недоступны."
            )
            return 0

        total_batches = (len(tracks) + 9) // 10
        header_text = (
            f"📁 <b>Плейлист: {playlist.name}</b>\n"
            f"🎵 Всего треков: {len(tracks)}"
        )
        if total_batches > 1:
            header_text += f"\n📦 Будет отправлено частями ({total_batches} сообщений)..."

        await bot.send_message(
            chat_id=chat_id,
            text=header_text,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=reply_to_message_id
        )

        batch_size = 10
        total_sent = 0

        for i in range(0, len(tracks), batch_size):
            batch = tracks[i:i + batch_size]
            media_group = [
                InputMediaAudio(
                    media=t.file_id,
                    performer=t.artist,
                    title=t.title,
                    duration=t.duration or 0
                )
                for t in batch
            ]

            try:
                await bot.send_media_group(chat_id=chat_id, media=media_group)
                total_sent += len(batch)
                if i + batch_size < len(tracks):
                    await asyncio.sleep(1.0)
            except Exception as e:
                logger.warning(f"send_media_group failed for playlist {playlist_id} batch, falling back to send_audio: {e}")
                for t in batch:
                    try:
                        await bot.send_audio(
                            chat_id=chat_id,
                            audio=t.file_id,
                            performer=t.artist,
                            title=t.title,
                            duration=t.duration or 0
                        )
                        total_sent += 1
                        await asyncio.sleep(0.3)
                    except Exception as err:
                        logger.error(f"Failed to send individual audio {t.id}: {err}")

        # Send completion summary with button to open in player
        summary_text = f"✅ Отправлено <b>{total_sent} из {len(tracks)}</b> треков плейлиста «{playlist.name}»."
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=summary_text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_playlist_player_button(playlist.id)
            )
        except Exception:
            pass

        return total_sent


async def deliver_album_tracks(
    bot: Bot,
    chat_id: int,
    album_id: int,
    user_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None
) -> int:
    """Send all tracks of an album to chat."""
    async with get_session() as session:
        result = await session.execute(
            select(Album)
            .options(selectinload(Album.tracks).selectinload(AlbumTrack.track))
            .where(Album.id == album_id)
        )
        album = result.scalar()

        if not album:
            await bot.send_message(chat_id=chat_id, text="❌ Альбом не найден.")
            return 0

        resolved_tracks = []
        for at in album.tracks:
            if at.track and at.track.file_id:
                fid = await _ensure_deliverable_file_id(at.track, session)
                if fid:
                    resolved_tracks.append(at.track)
        tracks = resolved_tracks

        if not tracks:
            await bot.send_message(
                chat_id=chat_id,
                text=f"💿 Альбом «<b>{album.name}</b>» пуст или треки недоступны."
            )
            return 0

        await bot.send_message(
            chat_id=chat_id,
            text=f"💿 <b>Альбом: {album.name}</b> ({album.artist or 'Неизвестен'})\n🎵 Отправляю {len(tracks)} треков...",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=reply_to_message_id
        )

        batch_size = 10
        total_sent = 0

        for i in range(0, len(tracks), batch_size):
            batch = tracks[i:i + batch_size]
            media_group = [
                InputMediaAudio(
                    media=t.file_id,
                    performer=t.artist,
                    title=t.title,
                    duration=t.duration or 0
                )
                for t in batch
            ]

            try:
                await bot.send_media_group(chat_id=chat_id, media=media_group)
                total_sent += len(batch)
                if i + batch_size < len(tracks):
                    await asyncio.sleep(1.0)
            except Exception as e:
                logger.warning(f"send_media_group failed for album {album_id}, fallback: {e}")
                for t in batch:
                    try:
                        await bot.send_audio(
                            chat_id=chat_id,
                            audio=t.file_id,
                            performer=t.artist,
                            title=t.title,
                            duration=t.duration or 0
                        )
                        total_sent += 1
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass

        bot_user = settings.bot_username or "tg_player_bot"
        album_markup = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎵 Слушать альбом в плеере",
                    url=f"https://t.me/{bot_user}?startapp=album_{album.id}"
                )
            ]
        ])
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Отправлено <b>{total_sent} из {len(tracks)}</b> треков альбома «{album.name}».",
            parse_mode=ParseMode.HTML,
            reply_markup=album_markup
        )
        return total_sent
