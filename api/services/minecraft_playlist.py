"""
Minecraft system playlist helpers.
"""
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Playlist, User

logger = logging.getLogger(__name__)

SYSTEM_PLAYLIST_NAME = "🎮 Minecraft Media"
SYSTEM_PLAYLIST_DESCRIPTION = "Треки для проигрывателя в Minecraft. Добавь сюда музыку — она появится в игре."


async def get_or_create_system_playlist(user_id: int, db: AsyncSession) -> Playlist:
    """Get or create the system playlist for a user. Idempotent."""
    result = await db.execute(
        select(Playlist).where(
            Playlist.owner_id == user_id,
            Playlist.is_system == True  # noqa: E712
        )
    )
    pl = result.scalar_one_or_none()
    if pl:
        return pl

    # Check if user exists, if not create minimal user (for ensure-user flow)
    # Caller should have ensured user exists, but be defensive
    user = await db.get(User, user_id)
    if not user:
        user = User(id=user_id, first_name="Player")
        db.add(user)
        await db.flush()

    pl = Playlist(
        owner_id=user_id,
        name=SYSTEM_PLAYLIST_NAME,
        description=SYSTEM_PLAYLIST_DESCRIPTION,
        is_public=False,
        is_system=True,
    )
    db.add(pl)
    await db.flush()
    await db.refresh(pl)
    logger.info(f"Created system playlist {pl.id} for user {user_id}")
    return pl


async def ensure_user_and_system_playlist(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    db: AsyncSession = None,
) -> tuple[User, Playlist]:
    """Ensure User exists and has system playlist. Returns (user, playlist)."""
    user = await db.get(User, telegram_id)
    if not user:
        user = User(
            id=telegram_id,
            username=username,
            first_name=first_name or "Player",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        logger.info(f"Created user {telegram_id} via minecraft ensure-user")
    else:
        # Update username if provided and changed
        if username and user.username != username:
            user.username = username
            await db.flush()

    pl = await get_or_create_system_playlist(user.id, db)
    return user, pl
