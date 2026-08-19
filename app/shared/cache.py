"""HRBP AI Workbench — Cache strategy using Redis.

Phase 15 spec:
  - Embedding cache: 15-minute TTL
  - User context cache: 5-minute TTL
  - Knowledge base metadata: 30-minute TTL

Provides a typed cache interface backed by Redis when available, falling
back gracefully to an in-memory store when Redis is unreachable.
"""

import functools
import hashlib
import json
from collections.abc import Callable
from typing import Any

from app.shared.logger import get_logger
from app.shared.redis_client import get_redis

logger = get_logger(__name__)

# Cache TTLs (seconds) — matches Phase 15 spec
EMBEDDING_CACHE_TTL = 900  # 15 min
USER_CONTEXT_CACHE_TTL = 300  # 5 min
KB_METADATA_CACHE_TTL = 1800  # 30 min

# Simple in-memory fallback when Redis is unavailable
_in_memory_cache: dict[str, dict] = {}


def _cache_key(prefix: str, *args: Any) -> str:
    """Build a deterministic cache key from arguments."""
    raw = json.dumps(args, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _json_serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class CacheClient:
    """Async cache interface backed by Redis, with in-memory fallback."""

    async def get(self, key: str) -> Any | None:
        """Get a cached value. Returns None on miss."""
        redis = await get_redis()
        if redis is not None:
            raw = await redis.get(f"cache:{key}")
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("cache_deserialize_failed", key=key)
                await redis.delete(f"cache:{key}")
                return None

        entry = _in_memory_cache.get(key)
        if entry is None:
            return None

        import time

        if time.time() > entry["expires_at"]:
            del _in_memory_cache[key]
            return None
        return entry["value"]

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """Set a cached value with TTL (seconds)."""
        redis = await get_redis()
        if redis is not None:
            await redis.set(f"cache:{key}", _json_serialize(value), ex=ttl)
            return

        import time

        _in_memory_cache[key] = {
            "value": value,
            "expires_at": time.time() + ttl,
        }

    async def delete(self, key: str) -> None:
        redis = await get_redis()
        if redis is not None:
            await redis.delete(f"cache:{key}")
        _in_memory_cache.pop(key, None)

    async def clear_prefix(self, prefix: str) -> None:
        redis = await get_redis()
        if redis is not None:
            async for k in redis.scan_iter(match=f"cache:{prefix}:*"):
                await redis.delete(k)
        keys_to_delete = [k for k in _in_memory_cache if k.startswith(prefix)]
        for k in keys_to_delete:
            del _in_memory_cache[k]


# Singleton
cache = CacheClient()


def cached(prefix: str, ttl: int = 300):
    """Decorator: cache the return value of an async function.

    Usage:
        @cached("embedding", ttl=EMBEDDING_CACHE_TTL)
        async def get_embedding(text: str) -> list[float]:
            ...
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key = _cache_key(prefix, args, kwargs)
            cached_val = await cache.get(key)
            if cached_val is not None:
                return cached_val
            result = await func(*args, **kwargs)
            await cache.set(key, result, ttl=ttl)
            return result

        return wrapper

    return decorator
