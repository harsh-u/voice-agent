"""Singleton Redis async client and FastAPI dependency."""
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Request

from voicerag.config import settings

_redis_client: Optional[aioredis.Redis] = None


async def init_redis() -> aioredis.Redis:
    global _redis_client
    _redis_client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
    )
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None


def get_redis(request: Request) -> aioredis.Redis:
    """FastAPI dependency — returns Redis client from app.state."""
    return request.app.state.redis
