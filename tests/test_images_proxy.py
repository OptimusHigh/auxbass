"""
Tests for image proxy endpoint and SSRF validation.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from api.routers.images import _is_safe_url, proxy_external_image
from fastapi import HTTPException


class TestSafeUrlValidation:
    """Tests for _is_safe_url SSRF guard."""

    def test_valid_https_url(self):
        assert _is_safe_url("https://i1.sndcdn.com/artworks-000123-large.jpg") is True
        assert _is_safe_url("https://i.scdn.co/image/ab67616d0000b2731234") is True
        assert _is_safe_url("http://example.com/cover.png") is True

    def test_disallow_non_http_schemes(self):
        assert _is_safe_url("file:///etc/passwd") is False
        assert _is_safe_url("ftp://ftp.example.com/image.jpg") is False
        assert _is_safe_url("javascript:alert(1)") is False
        assert _is_safe_url("data:image/png;base64,iVBORw0KGgo=") is False

    def test_disallow_localhost_and_zero(self):
        assert _is_safe_url("http://localhost/image.jpg") is False
        assert _is_safe_url("http://localhost:8000/image.jpg") is False
        assert _is_safe_url("http://0.0.0.0/image.jpg") is False

    def test_disallow_private_ip_ranges(self):
        assert _is_safe_url("http://127.0.0.1/cover.jpg") is False
        assert _is_safe_url("http://10.0.0.1/cover.jpg") is False
        assert _is_safe_url("http://192.168.1.1/cover.jpg") is False
        assert _is_safe_url("http://172.16.0.1/cover.jpg") is False
        assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_disallow_empty_or_malformed(self):
        assert _is_safe_url("") is False
        assert _is_safe_url("not a url") is False
        assert _is_safe_url("http://") is False


@pytest.mark.asyncio
async def test_proxy_external_image_rejects_unsafe_url():
    with pytest.raises(HTTPException) as exc_info:
        await proxy_external_image("http://127.0.0.1/secret.jpg")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_proxy_external_image_success():
    """Test successful image proxying returns image data and Cache-Control."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Type": "image/jpeg"}
    mock_resp.read = AsyncMock(return_value=b"\xff\xd8\xff\xe0fake_jpeg_content")

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("api.routers.images.get_http_session", AsyncMock(return_value=mock_session)):
        res = await proxy_external_image("https://i1.sndcdn.com/artworks-123-t500x500.jpg")
        assert res.status_code == 200
        assert res.media_type == "image/jpeg"
        assert res.body == b"\xff\xd8\xff\xe0fake_jpeg_content"
        assert "max-age=604800" in res.headers["Cache-Control"]


@pytest.mark.asyncio
async def test_get_image_caching_and_deduplication():
    """Verify that get_image caches in RAM and deduplicates in-flight calls."""
    from api.routers.images import get_image, _image_cache, _file_path_cache

    test_file_id = "AgACAgIAAxkBAAEB1234567890abcdefghijklmnopqrstuvwxyz"
    _image_cache.pop(test_file_id, None)
    _file_path_cache.pop(test_file_id, None)

    mock_bot = MagicMock()
    mock_file = MagicMock()
    mock_file.file_path = "photos/test_cover.jpg"
    mock_bot.get_file = AsyncMock(return_value=mock_file)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=b"fake_telegram_image_bytes")

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("api.routers.images._get_bot", return_value=mock_bot), \
         patch("api.routers.images.get_http_session", AsyncMock(return_value=mock_session)):

        # Concurrent calls: should deduplicate to 1 get_file and 1 session.get
        res1, res2 = await asyncio.gather(
            get_image(test_file_id),
            get_image(test_file_id)
        )

        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res1.body == b"fake_telegram_image_bytes"
        assert res2.body == b"fake_telegram_image_bytes"
        assert mock_bot.get_file.call_count == 1
        assert mock_session.get.call_count == 1

        # Third call: should hit LRU cache without hitting bot or session at all
        res3 = await get_image(test_file_id)
        assert res3.status_code == 200
        assert res3.body == b"fake_telegram_image_bytes"
        assert mock_bot.get_file.call_count == 1
        assert mock_session.get.call_count == 1


@pytest.mark.asyncio
async def test_get_image_client_disconnect():
    """Verify that get_image returns 499 if client disconnected early."""
    from api.routers.images import get_image

    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=True)

    res = await get_image("AgACAgIAAxkBAAEB_disconnect_test_12345678901234567890", request=mock_request)
    assert res.status_code == 499


@pytest.mark.asyncio
async def test_get_image_dedup_one_disconnects_other_succeeds():
    """Verify that if Request 1 disconnects, in-flight task continues for Request 2."""
    from api.routers.images import get_image, _image_cache, _file_path_cache

    test_file_id = "AgACAgIAAxkBAAEB_concurrent_disconnect_test_1234567890"
    _image_cache.pop(test_file_id, None)
    _file_path_cache.pop(test_file_id, None)

    mock_bot = MagicMock()
    mock_file = MagicMock()
    mock_file.file_path = "photos/test_cover2.jpg"
    mock_bot.get_file = AsyncMock(return_value=mock_file)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=b"concurrent_image_bytes")

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    req1 = MagicMock()
    # req1 is disconnected
    req1.is_disconnected = AsyncMock(return_value=True)

    req2 = MagicMock()
    # req2 stays connected
    req2.is_disconnected = AsyncMock(return_value=False)

    with patch("api.routers.images._get_bot", return_value=mock_bot), \
         patch("api.routers.images.get_http_session", AsyncMock(return_value=mock_session)):

        res1, res2 = await asyncio.gather(
            get_image(test_file_id, request=req1),
            get_image(test_file_id, request=req2)
        )

        assert res1.status_code == 499
        assert res2.status_code == 200
        assert res2.body == b"concurrent_image_bytes"


