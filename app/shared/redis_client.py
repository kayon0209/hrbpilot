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
  - ``_client_unavailable`` short-circuits only **for a cooldown window**,
    then a retry is allowed (2026-09-10 fix, found by the fault-injection
    matrix). The previous version short-circuited for the whole life of the
    loop: once Redis blipped, every later call returned ``None`` even after
    Redis came back, so ``RateLimiter`` stayed fail-closed and **every
    authenticated request returned 429 until the process was restarted**.
    Worst part: a PostgreSQL outage was then reported as rate limiting.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from app.config.settings import settings
from app.shared.logger import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

# 依赖故障后多久才允许再次尝试连接。目的是"别猛砸一个已经挂掉的服务"，
# 但不能是"永远不重试" —— 那会让一次抖动变成永久降级（见模块 docstring）。
RETRY_COOLDOWN_SECONDS = float(os.environ.get("REDIS_RETRY_COOLDOWN_SECONDS", "10"))

_client: Redis | None = None
_client_loop: object | None = None
_client_unavailable: bool = False
_client_failed_at: float = 0.0


def _mark_unavailable() -> None:
    """记录"这次失败了"，并在冷却期内短路后续调用。"""
    global _client_unavailable, _client_failed_at
    _client_unavailable = True
    _client_failed_at = time.monotonic()


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
                _mark_unavailable()
                return None
    if _client_unavailable and loop is not None and _client_loop is not None and loop is _client_loop:
        if time.monotonic() - _client_failed_at < RETRY_COOLDOWN_SECONDS:
            # 冷却期内：别猛砸一个刚挂掉的服务。
            return None
        # 冷却结束：允许重试，否则一次抖动会让本进程永久降级。
        logger.info("redis_retry_after_cooldown", cooldown=RETRY_COOLDOWN_SECONDS)
        _client_unavailable = False
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
        _mark_unavailable()
        logger.warning("redis_unavailable", error=str(e), msg="falling back to in-memory")
        return None


async def close_redis() -> None:
    """Close the cached client at script shutdown.

    Without this, the connection's StreamWriter is garbage-collected after
    the event loop closes and surfaces as a noisy
    "RuntimeError: Event loop is closed" traceback on exit.
    """
    global _client, _client_loop, _client_unavailable, _client_failed_at
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None
    _client_loop = None
    _client_unavailable = False
    _client_failed_at = 0.0
