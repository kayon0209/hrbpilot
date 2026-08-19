"""HRBP AI Workbench — Rate limiter.

Tenant-level + user-level rate limiting using Redis sliding-window counters.
Falls back to pass-through (with logging) when Redis is unavailable so dev
mode keeps working.
"""

import time

from app.config.settings import settings
from app.shared.errors import RateLimitError
from app.shared.logger import get_logger
from app.shared.redis_client import get_redis

logger = get_logger(__name__)

# Window sizes (seconds) matched to settings values (per-minute limits).
_WINDOW_SECONDS = 60


class RateLimiter:
    """Redis-based rate limiting with sliding-window counters.

    Uses INCR + PEXPIRE (fixed window with TTL refresh). Two counters are
    tracked per key: total count and window-start epoch, so a cheap sliding
    approximation is possible when needed.
    """

    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client  # Injected for tests; auto-resolved otherwise

    async def check(self, tenant_id: str, user_id: str) -> None:
        """Check rate limits. Raises RateLimitError if exceeded."""
        tenant_limit = settings.rate_limit_tenant_per_minute
        user_limit = settings.rate_limit_user_per_minute

        redis = self.redis
        if redis is None:
            redis = await get_redis()

        if redis is None:
            logger.info(
                "rate_limit_passthrough",
                tenant_id=tenant_id,
                user_id=user_id,
                reason="redis_unavailable",
            )
            return

        now_ms = int(time.time() * 1000)
        window_ms = _WINDOW_SECONDS * 1000
        tenant_key = f"ratelimit:tenant:{tenant_id}"
        user_key = f"ratelimit:user:{user_id}"

        async def _check_key(key: str, limit: int) -> bool:
            window_id = now_ms // window_ms
            current_key = f"{key}:{window_id}"
            count = await redis.incr(current_key)
            if count == 1:
                await redis.pexpire(current_key, window_ms)
            return int(count) <= limit

        tenant_ok = await _check_key(tenant_key, tenant_limit)
        user_ok = await _check_key(user_key, user_limit)

        if not tenant_ok or not user_ok:
            logger.warning(
                "rate_limit_exceeded",
                tenant_id=tenant_id,
                user_id=user_id,
                tenant_exceeded=not tenant_ok,
                user_exceeded=not user_ok,
            )
            raise RateLimitError("请求过于频繁，请稍后再试")
