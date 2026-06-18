"""API key generation, hashing, and FastAPI dependency for runtime auth."""
import hashlib
import asyncio
from secrets import token_urlsafe
from datetime import datetime, UTC
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voicerag.config import settings
from voicerag.db.models import ApiKey, KnowledgeBase
from voicerag.db.session import get_session


def generate_api_key() -> str:
    """Generate a new full API key (shown once)."""
    return f"{settings.api_key_prefix}{token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    """Return sha256 hex digest — O(1) for runtime lookup."""
    return hashlib.sha256(key.encode()).hexdigest()


async def get_kb_from_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeBase:
    """
    Dependency for runtime (voice-agent) endpoints.
    Validates X-API-Key header and returns the associated KnowledgeBase.
    Uses Redis cache for O(1) lookup; falls back to Postgres on cache miss.
    Also enforces per-key rate limiting.
    """
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    hashed = hash_api_key(x_api_key)

    # Try to get redis from app.state
    redis = None
    if request and hasattr(request.app.state, "redis"):
        redis = request.app.state.redis

    api_key_id: Optional[str] = None
    kb_id: Optional[str] = None

    # 1. Check Redis cache
    if redis:
        cache_val = await redis.get(f"apikey:{hashed}")
        if cache_val:
            parts = cache_val.decode().split(":")
            if len(parts) == 2:
                kb_id, api_key_id = parts[0], parts[1]

    # 2. Cache miss — query Postgres
    if not kb_id:
        result = await session.execute(
            select(ApiKey).where(
                ApiKey.hashed_key == hashed,
                ApiKey.is_active == True,
            )
        )
        api_key_obj = result.scalar_one_or_none()
        if not api_key_obj:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API key")

        kb_id = api_key_obj.knowledge_base_id
        api_key_id = api_key_obj.id

        # Cache for TTL
        if redis:
            await redis.set(
                f"apikey:{hashed}",
                f"{kb_id}:{api_key_id}",
                ex=settings.api_key_cache_ttl_seconds,
            )
    else:
        # Verify the key is still active (from DB) — only if we served from cache
        # We do a lightweight check: trust the cache for TTL window
        pass

    # 3. Rate limiting — fixed window per key per minute
    if redis and api_key_id:
        epoch_minute = int(datetime.now(UTC).timestamp()) // 60
        rl_key = f"rl:{api_key_id}:{epoch_minute}"
        count = await redis.incr(rl_key)
        if count == 1:
            await redis.expire(rl_key, 60)
        if count > settings.rate_limit_per_minute:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # 4. Load KB
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    # 5. Update last_used_at lazily (fire-and-forget, throttle ~once/min via Redis)
    if redis and api_key_id:
        throttle_key = f"lastseen:{api_key_id}"
        was_set = await redis.set(throttle_key, "1", ex=60, nx=True)
        if was_set:
            asyncio.create_task(_update_last_used(api_key_id))

    # Attach api_key_id to request state for logging
    if request:
        request.state.api_key_id = api_key_id

    return kb


async def _update_last_used(api_key_id: str) -> None:
    """Fire-and-forget update of last_used_at using its own session."""
    from voicerag.db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            api_key_obj = await session.get(ApiKey, api_key_id)
            if api_key_obj:
                api_key_obj.last_used_at = datetime.now(UTC)
                await session.commit()
    except Exception:
        pass


async def invalidate_api_key_cache(hashed_key: str, request: Request) -> None:
    """Call on revocation to clear Redis cache entry."""
    if hasattr(request.app.state, "redis"):
        await request.app.state.redis.delete(f"apikey:{hashed_key}")
