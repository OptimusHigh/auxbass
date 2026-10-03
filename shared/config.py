"""
TG Player - Shared Configuration
"""
import os
import warnings
from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings"""
    
    # Bot
    bot_token: str
    bot_username: str = ""  # For Telegram Login Widget
    app_name: str = "AuxBass"
    bot_name: str = ""
    
    @property
    def display_name(self) -> str:
        return self.bot_name or self.app_name or "AuxBass"
    
    @field_validator("bot_token", mode="before")
    @classmethod
    def clean_bot_token(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v
        
    @field_validator("bot_username", mode="before")
    @classmethod
    def clean_bot_username(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            return ""
        cleaned = v.strip().lstrip("@")
        lowered = cleaned.lower()
        if lowered in ("", "your_bot_username", "your_bot_username_here", "enter_your_bot_username", "none", "null") or lowered.startswith("your_bot") or "enter_your" in lowered:
            return ""
        return cleaned
    
    # Telegram Bot API URL (use local server to bypass 20MB limit)
    # Default: https://api.telegram.org (20MB download limit)
    # Local server: http://localhost:8081 (no limit)
    telegram_api_url: str = "https://api.telegram.org"
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./tg_player.db"
    
    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_url: str = "http://localhost:8000"
    
    # Mini App
    webapp_url: str = "http://localhost:5173"
    
    # Security
    # ВАЖНО: SECRET_KEY должен быть одинаковым между перезапусками!
    # Иначе все JWT токены станут невалидными и пользователей выкинет из аккаунтов.
    secret_key: str = "dev-secret-key"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 30  # JWT token expiration
    
    # Last.fm API (for artist images)
    lastfm_api_key: str = ""
    
    # Proxy & Network settings (for YouTube / SoundCloud scraping where restricted)
    proxy_url: str = ""  # e.g. "http://user:pass@host:port" or "socks5://host:port"
    ytdlp_timeout: int = 15  # Network socket timeout in seconds
    
    # Scanner buffer chat ID (private group where bot forwards messages for scanning)
    # If set, scan_channel will forward to this chat instead of user's DM — no spam.
    # Create a private group, add the bot as admin, set the group's chat_id here.
    scanner_buffer_chat_id: int = 0
    
    class Config:
        env_file = (".env", ".env.local")
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    
    # Предупреждение о дефолтном SECRET_KEY в production
    if settings.secret_key == "dev-secret-key":
        # Проверяем, запущено ли в Docker (признак production)
        if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
            warnings.warn(
                "\n⚠️  ВНИМАНИЕ: Используется дефолтный SECRET_KEY!\n"
                "   Это приведёт к выбросу пользователей из аккаунтов при перезапуске контейнера.\n"
                "   Установите SECRET_KEY в .env файле с постоянным случайным значением.\n"
                "   Пример: SECRET_KEY=$(openssl rand -hex 32)\n",
                UserWarning,
                stacklevel=2
            )
    
    if settings.proxy_url:
        p = settings.proxy_url.strip()
        os.environ.setdefault("HTTP_PROXY", p)
        os.environ.setdefault("HTTPS_PROXY", p)
        os.environ.setdefault("http_proxy", p)
        os.environ.setdefault("https_proxy", p)
    
    return settings
