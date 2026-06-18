"""API key generation, hashing, and FastAPI dependency for ingest auth."""
import hashlib
import asyncio
from secrets import token_urlsafe
from datetime import datetime, UTC
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voxscope.config import settings
from voxscope.db.models import ApiKey, Project
from voxscope.db.session import get_session

bearer_scheme = HTTPBearer(auto_error=False)


def generate_api_key() -> str:
    """Generate a new full API key (shown once)."""
    return f"{settings.api_key_prefix}{token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    """Return sha256 hex digest — O(1) for runtime lookup."""
    return hashlib.sha256(key.encode()).hexdigest()


async def get_project_from_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Project:
    """
    Dependency for ingest endpoints.
    Validates Bearer vsk_... token and returns the associated Project.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_key = credentials.credentials
    hashed = hash_api_key(raw_key)

    result = await session.execute(
        select(ApiKey).where(
            ApiKey.hashed_key == hashed,
            ApiKey.is_active == True,
        )
    )
    api_key_obj = result.scalar_one_or_none()
    if not api_key_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
        )

    project = await session.get(Project, api_key_obj.project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # Update last_used_at lazily
    asyncio.create_task(_update_last_used(api_key_obj.id))

    return project


async def _update_last_used(api_key_id: str) -> None:
    """Fire-and-forget update of last_used_at using its own session."""
    from voxscope.db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            api_key_obj = await session.get(ApiKey, api_key_id)
            if api_key_obj:
                api_key_obj.last_used_at = datetime.now(UTC)
                await session.commit()
    except Exception:
        pass
