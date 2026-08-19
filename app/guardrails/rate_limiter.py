"""HRBP AI Workbench — Rate limiter."""

from __future__ import annotations

import time
from uuid import uuid4

from app.config.settings import settings
from app.shared.errors import RateLimitError
from app.shared.logger import get_logger
from app.shared.redis_client import get_redis

logger = get_logger(__name__)

_WINDOW_SECONDS = 60


class RateLimiter:
    """Redis-based rate limiting with sliding-window counters."""

    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client

    async def check(self, tenant_id: str, user_id: str) -> None:
        tenant_limit = settings.rate_limit_tenant_per_minute
        user_limit = settings.rate_limit_user_per_minute

        redis = self.redis or await get_redis()
        if redis is None:
            if settings.rate_limit_fail_open and not settings.is_production:
                logger.info("rate_limit_passthrough", tenant_id=tenant_id, user_id=user_id, reason="redis_unavailable")
                return
            raise RateLimitError("请求过于频繁，请稍后再试")

        now_ms = int(time.time() * 1000)
        window_ms = _WINDOW_SECONDS * 1000
        tenant_key = f"ratelimit:tenant:{tenant_id}"
        user_key = f"ratelimit:user:{user_id}"
        window_start = now_ms - window_ms

        async def _check_key(key: str, limit: int) -> bool:
            member = f"{time.time_ns()}:{uuid4().hex}"
            await redis.zremrangebyscore(key, 0, window_start)
            await redis.zadd(key, {member: now_ms})
            await redis.zremrangebyscore(key, 0, window_start)
            await redis.expire(key, _WINDOW_SECONDS + 5)
            count = await redis.zcard(key)
            return int(count) <= limit

        tenant_ok = await _check_key(tenant_key, tenant_limit)
        user_ok = await _check_key(user_key, user_limit)
        if not tenant_ok or not user_ok:
            logger.warning("rate_limit_exceeded", tenant_id=tenant_id, user_id=user_id, tenant_exceeded=not tenant_ok, user_exceeded=not user_ok)
            raise RateLimitError("请求过于频繁，请稍后再试")
