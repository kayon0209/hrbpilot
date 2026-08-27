"""Lazy Redis client — shared across modules.

Creates a single ``redis.asyncio`` connection on first use. When Redis is
unreachable, callers get ``None`` and fall back to in-memory behaviour.
This avoids crashing the app at import time when Redis isn't running.

Implementation notes (Phase 5 fix):
  - The client is cached PER EVENT LOOP. A cached redis.asyncio client is
    bound to the loop that created its transport; reusing it on another
    loop (TestClient portals, script asyncio.run calls) fails with
    "Event loop is closed" or silent transport errors. We key the cache on
    the running loop and rebuild when the loop changes.
  - ``_client_unavailable`` short-circuits only while the loop that failed
    is still running; a new loop gets a fresh connection attempt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.settings import settings
from app.shared.logger import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

_client: Redis | None = None
_client_loop: object | None = None
_client_unavailable: bool = False


def _current_loop() -> object | None:
    try:
        import asyncio

        return asyncio.get_running_loop()
    except RuntimeError:
        return None


async def get_redis() -> Redis | None:
    """Return the shared Redis client, or None if Redis is unavailable."""
    global _client, _client_loop, _client_unavailable
    loop = _current_loop()

    if _client is not None:
        if loop is not None and _client_loop is not None and loop is not _client_loop:
            # Client is bound to a dead loop — rebuild on this one.
            try:
                await _client.aclose()
            except Exception:
                pass
            _client = None
            _client_unavailable = False
        else:
            try:
                await _client.ping()
                return _client
            except Exception as e:
                logger.warning("redis_cached_client_unavailable", error=str(e), msg="falling back to in-memory")
                try:
                    await _client.aclose()
                except Exception:
                    pass
                _client = None
                _client_unavailable = True
                return None
    if _client_unavailable and loop is not None and _client_loop is not None and loop is _client_loop:
        # Already failed on THIS loop; don't hammer a down server.
        return None
    if _client_unavailable and _client_loop is None:
        # Previous failure had no loop context (sync context); allow retry.
        _client_unavailable = False
    try:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
        _client_loop = loop
        await _client.ping()
        logger.info("redis_connected", url=settings.redis_url)
        return _client
    except Exception as e:
        _client_unavailable = True
        if _client is not None:
            # Shut the failed connection down cleanly, otherwise its
            # transport error surfaces later as an orphaned
            # "Future exception was never retrieved" warning.
            try:
                await _client.aclose()
            except Exception:
                pass
            _client = None
        logger.warning("redis_unavailable", error=str(e), msg="falling back to in-memory")
        return None
