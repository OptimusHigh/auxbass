"""
Tests for Telegram Inline mode formatting and Delivery keyboards.
"""
import re
import pytest
from bot.handlers.inline import _format_playlist_card, _format_album_card
from bot.services.delivery import get_track_player_button, get_playlist_player_button
from bot.handlers.menu_keyboards import get_playlist_share_keyboard, get_album_share_keyboard
from shared.models import Playlist, Album, Track, User
from shared.config import get_settings

settings = get_settings()


class DummyUser:
    display_name = "Test DJ"


class DummyPlaylist:
    id = 42
    name = "Synthwave Essentials"
    description = "Best retro beats for coding"
    owner = DummyUser()


class DummyAlbum:
    id = 7
    name = "Endless Summer"
    artist = "The Midnight"


class DummyTrack:
    id = 101
    title = "Days of Thunder"
    artist = "The Midnight"
    duration = 215
    file_id = "BAACAgIAAxkBAAI..."
    album_name = "Days of Thunder"


def test_format_playlist_card():
    playlist = DummyPlaylist()
    tracks = [DummyTrack(), DummyTrack()]
    bot_username = "test_player_bot"

    text, keyboard = _format_playlist_card(playlist, tracks, bot_username)

    assert "Synthwave Essentials" in text
    assert "Best retro beats for coding" in text
    assert "2" in text  # 2 tracks
    assert "Test DJ" in text
    assert settings.display_name in text

    # Check keyboard buttons
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    urls = [btn.url for btn in buttons if btn.url]

    assert any("startapp=playlist_42" in url for url in urls)
    assert any("start=files_playlist_42" in url for url in urls)


def test_format_album_card():
    album = DummyAlbum()
    tracks = [DummyTrack()]
    bot_username = "test_player_bot"

    text, keyboard = _format_album_card(album, tracks, bot_username)

    assert "Endless Summer" in text
    assert "The Midnight" in text

    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    urls = [btn.url for btn in buttons if btn.url]

    assert any("startapp=album_7" in url for url in urls)
    assert any("start=files_album_7" in url for url in urls)


def test_delivery_player_buttons():
    kb_track = get_track_player_button(99)
    btn_track = kb_track.inline_keyboard[0][0]
    assert "startapp=track_99" in btn_track.url

    kb_pl = get_playlist_player_button(55)
    btn_pl = kb_pl.inline_keyboard[0][0]
    assert "startapp=playlist_55" in btn_pl.url


def test_share_keyboards():
    kb_pl = get_playlist_share_keyboard(12)
    buttons = [btn for row in kb_pl.inline_keyboard for btn in row]
    callbacks = [btn.callback_data for btn in buttons if btn.callback_data]
    assert "download_playlist:12" in callbacks

    kb_al = get_album_share_keyboard(34)
    buttons_al = [btn for row in kb_al.inline_keyboard for btn in row]
    callbacks_al = [btn.callback_data for btn in buttons_al if btn.callback_data]
    assert "download_album:34" in callbacks_al


def test_inline_regex_parsing():
    pl_pattern = r'^(?:playlist|pl)[:_](\d+)$'
    assert re.match(pl_pattern, "playlist:123", re.IGNORECASE).group(1) == "123"
    assert re.match(pl_pattern, "pl:55", re.IGNORECASE).group(1) == "55"
    assert re.match(pl_pattern, "PL_77", re.IGNORECASE).group(1) == "77"

    tr_pattern = r'^(?:track|tr)[:_](\d+)$'
    assert re.match(tr_pattern, "track:999", re.IGNORECASE).group(1) == "999"
    assert re.match(tr_pattern, "tr:10", re.IGNORECASE).group(1) == "10"
    assert re.match(tr_pattern, "TR_4", re.IGNORECASE).group(1) == "4"

    al_pattern = r'^(?:album|al)[:_](\d+)$'
    assert re.match(al_pattern, "album:42", re.IGNORECASE).group(1) == "42"
    assert re.match(al_pattern, "al:1", re.IGNORECASE).group(1) == "1"


def test_track_album_name_property():
    """Verify that Track has album and album_name properties that don't raise AttributeError."""
    track = Track(title="Test Track", artist="Test Artist")
    assert track.album is None
    assert track.album_name is None

    from shared.models import TrackEnrichment
    enr = TrackEnrichment(album_name="Test Album")
    track.enrichment = enr
    assert track.album == "Test Album"
    assert track.album_name == "Test Album"


@pytest.mark.asyncio
async def test_deliver_single_track_with_album():
    from unittest.mock import AsyncMock, MagicMock, patch
    from bot.services.delivery import deliver_single_track
    from shared.models import TrackEnrichment

    bot = AsyncMock()
    track = Track(id=1, file_id="file123", title="Hero of My Story 3style3", artist="Bladee")
    track.enrichment = TrackEnrichment(album_name="333")

    session_mock = AsyncMock()
    session_mock.scalar = AsyncMock(return_value=track)

    class DummyContext:
        async def __aenter__(self):
            return session_mock
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("bot.services.delivery.get_session", return_value=DummyContext()):
        res = await deliver_single_track(bot=bot, chat_id=123, track_id=1)
        assert res is True
        bot.send_audio.assert_called_once()
        call_kwargs = bot.send_audio.call_args.kwargs
        assert "Bladee — Hero of My Story 3style3" in call_kwargs["caption"]
        assert "💿 <i>333</i>" in call_kwargs["caption"]


@pytest.mark.asyncio
async def test_deliver_single_track_wrong_file_identifier_marks_unavailable():
    from unittest.mock import AsyncMock, patch
    from aiogram.exceptions import TelegramBadRequest
    from bot.services.delivery import deliver_single_track

    bot = AsyncMock()
    bot.send_audio.side_effect = Exception("Telegram server says - Bad Request: wrong file identifier/HTTP URL specified")

    track = Track(id=3258, file_id="dead_file_id", title="Gtblessgo", artist="Thaiboy Digital", is_unavailable=False)

    session_mock = AsyncMock()
    session_mock.scalar = AsyncMock(return_value=track)
    session_mock.commit = AsyncMock()

    class DummyContext:
        async def __aenter__(self):
            return session_mock
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("bot.services.delivery.get_session", return_value=DummyContext()):
        res = await deliver_single_track(bot=bot, chat_id=123, track_id=3258, send_failure_message=False)
        assert res is False
        assert track.is_unavailable is True
        session_mock.commit.assert_called_once()
        bot.send_message.assert_not_called()


