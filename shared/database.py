"""
TG Player - Database Session Manager v2

Supports both SQLite and PostgreSQL with proper connection pooling.
"""
import logging
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from .config import get_settings
from .models import Base

logger = logging.getLogger(__name__)
settings = get_settings()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_database_url() -> str:
    """
    Get database URL, converting to async driver if needed.
    Also ensures relative SQLite paths are anchored to project root.
    
    Supports:
    - sqlite:// -> sqlite+aiosqlite://
    - postgresql:// -> postgresql+asyncpg://
    - postgres:// -> postgresql+asyncpg://
    """
    url = settings.database_url
    
    # Convert to async drivers
    if url.startswith("sqlite://"):
        url = url.replace("sqlite://", "sqlite+aiosqlite://")
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://")
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://")

    # Anchor relative SQLite paths to project root so current working directory doesn't matter
    if "sqlite" in url:
        for prefix in ("sqlite+aiosqlite:///./", "sqlite+aiosqlite:////./", "sqlite:///./"):
            if url.startswith(prefix):
                rel_path = url[len(prefix):]
                abs_db_path = (PROJECT_ROOT / rel_path).resolve()
                url = f"sqlite+aiosqlite:///{abs_db_path.as_posix()}"
                break
    
    return url


# Determine database type and pool settings
database_url = get_database_url()
is_sqlite = "sqlite" in database_url

# Build engine kwargs based on database type
engine_kwargs = {
    "echo": False,  # Set True for SQL debugging
}

if is_sqlite:
    # SQLite: use NullPool with 60s timeout for write locks
    engine_kwargs["poolclass"] = NullPool
    engine_kwargs["connect_args"] = {
        "timeout": 60,
        "check_same_thread": False,
    }
else:
    # PostgreSQL: use connection pooling for better performance
    engine_kwargs.update({
        "pool_size": 10,           # Base pool size
        "max_overflow": 20,        # Extra connections when busy
        "pool_timeout": 30,        # Seconds to wait for connection
        "pool_recycle": 1800,      # Recycle connections after 30 min
        "pool_pre_ping": True,     # Test connections before use
    })

# Create async engine
engine = create_async_engine(database_url, **engine_kwargs)

if is_sqlite:
    # Configure SQLite PRAGMAs for high-concurrency (WAL mode + 60s busy timeout)
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def _ensure_sqlite_columns(conn):
    """Ensure missing columns in SQLite tables are added and constraints fixed"""
    # 1. Check if channel_messages has message_id NOT NULL and fix it
    res_cm = await conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='channel_messages';"
    )
    if res_cm.scalar():
        cm_info = await conn.exec_driver_sql("PRAGMA table_info(channel_messages);")
        cm_cols = {row[1]: {"notnull": row[3]} for row in cm_info.fetchall()}
        if cm_cols.get("message_id", {}).get("notnull") == 1:
            logger.info("Migrating channel_messages to make message_id nullable...")
            await conn.exec_driver_sql("PRAGMA foreign_keys=OFF;")
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS channel_messages_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL REFERENCES user_channels(id) ON DELETE CASCADE,
                    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                    status TEXT DEFAULT 'pending',
                    message_id BIGINT,
                    hashtags TEXT,
                    retry_count INTEGER DEFAULT 0,
                    last_error VARCHAR(500),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(channel_id, track_id)
                );
            """)
            await conn.exec_driver_sql("""
                INSERT OR IGNORE INTO channel_messages_new (id, channel_id, track_id, status, message_id, hashtags, retry_count, last_error, created_at, updated_at)
                SELECT id, channel_id, track_id, COALESCE(status, 'sent'), message_id, hashtags, COALESCE(retry_count, 0), last_error, created_at, updated_at FROM channel_messages;
            """)
            await conn.exec_driver_sql("DROP TABLE channel_messages;")
            await conn.exec_driver_sql("ALTER TABLE channel_messages_new RENAME TO channel_messages;")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON;")

    # 2. Add missing columns
    schema_definitions = {
        "channel_messages": [
            ("status", "TEXT DEFAULT 'sent'"),
            ("retry_count", "INTEGER DEFAULT 0"),
            ("last_error", "TEXT"),
            ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ],
        "tracks": [
            ("file_name", "TEXT"),
            ("normalized_artist", "TEXT"),
            ("is_public", "INTEGER DEFAULT 1"),
            ("is_unavailable", "INTEGER DEFAULT 0"),
            ("is_chunk", "INTEGER DEFAULT 0"),
            ("source_url", "TEXT"),
            ("play_count", "INTEGER DEFAULT 0"),
            ("last_played_at", "TIMESTAMP"),
            ("forward_source_type", "TEXT"),
            ("forward_source_id", "INTEGER"),
            ("forward_source_name", "TEXT"),
            ("forward_source_username", "TEXT"),
            ("uploader_id", "INTEGER"),
        ],
        "users": [
            ("photo_url", "TEXT"),
            ("custom_nickname", "TEXT"),
            ("custom_avatar_url", "TEXT"),
            ("hide_from_search", "INTEGER DEFAULT 0"),
            ("hide_profile", "INTEGER DEFAULT 0"),
            ("hide_telegram_id", "INTEGER DEFAULT 0"),
            ("notify_subscription", "INTEGER DEFAULT 1"),
        ],
        "user_channels": [
            ("auto_sync", "INTEGER DEFAULT 1"),
        ],
        "playlists": [
            ("custom_cover_url", "TEXT"),
            ("pending_cover_url", "TEXT"),
            ("pending_cover_expires_at", "TIMESTAMP"),
            ("is_system", "INTEGER DEFAULT 0"),
        ],
        "user_library": [
            ("is_disliked", "INTEGER DEFAULT 0"),
            ("disliked_at", "TIMESTAMP"),
        ],
        "user_external_accounts": [
            ("show_on_profile", "INTEGER DEFAULT 1"),
            ("show_playlists", "INTEGER DEFAULT 1"),
            ("show_tracks", "INTEGER DEFAULT 1"),
        ],
    }
    
    for table_name, columns in schema_definitions.items():
        res = await conn.exec_driver_sql(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
        )
        if not res.scalar():
            continue
        
        info = await conn.exec_driver_sql(f"PRAGMA table_info({table_name});")
        existing_cols = {row[1] for row in info.fetchall()}
        
        for col_name, col_def in columns:
            if col_name not in existing_cols:
                try:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def};"
                    )
                except Exception:
                    pass

    # Ensure indexes
    try:
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_channel_message_track ON channel_messages(track_id);"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_channel_message_status ON channel_messages(channel_id, status);"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_tracks_normalized_artist ON tracks(normalized_artist);"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_album_tracks_track_id ON album_tracks(track_id);"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_user_library_disliked ON user_library(user_id, is_disliked);"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_tracks_public ON tracks(is_public);"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_tracks_is_chunk ON tracks(is_chunk);"
        )
    except Exception:
        pass


async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        if is_sqlite:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            await conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            await conn.exec_driver_sql("PRAGMA busy_timeout=60000;")
        await conn.run_sync(Base.metadata.create_all)
        if is_sqlite:
            await _ensure_sqlite_columns(conn)


async def drop_db():
    """Drop all database tables (use with caution!)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def close_db():
    """Close database connections"""
    await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session context manager.
    Automatically commits on success, rollbacks on exception.
    
    Usage:
        async with get_session() as session:
            result = await session.execute(query)
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI.
    
    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ============== Transaction Helpers ==============

@asynccontextmanager
async def transaction():
    """
    Explicit transaction context.
    Commit is manual - you need to call session.commit().
    
    Usage:
        async with transaction() as session:
            session.add(obj1)
            session.add(obj2)
            await session.commit()  # Commits both
    """
    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
