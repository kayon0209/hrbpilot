"""Lazy Redis client — shared across modules.

Creates a single ``redis.asyncio`` connection on first use. When Redis is
unreachable, callers get ``None`` and fall back to in-memory behaviour.
This avoids crashing the app at import time when Redis isn't running.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.settings import settings
from app.shared.logger import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

_client: Redis | None = None
_client_unavailable: bool = False


async def get_redis() -> Redis | None:
    """Return the shared Redis client, or None if Redis is unavailable."""
    global _client, _client_unavailable
    if _client is not None:
        return _client
    if _client_unavailable:
        return None
    try:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await _client.ping()
        logger.info("redis_connected", url=settings.redis_url)
        return _client
    except Exception as e:
        _client_unavailable = True
        _client = None
        logger.warning("redis_unavailable", error=str(e), msg="falling back to in-memory")
        return None
