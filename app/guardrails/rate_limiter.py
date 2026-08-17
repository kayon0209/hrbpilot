"""HRBP AI Workbench — Rate limiter.

Tenant-level + user-level rate limiting using Redis counters.
"""

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Redis-based rate limiting with sliding window."""

    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client  # Will be injected when Redis is available

    async def check(self, tenant_id: str, user_id: str) -> None:
        """Check rate limits. Raises RateLimitError if exceeded."""
        # TODO: Real Redis rate limiting with INCR + TTL
        # For now, just log and pass
        logger.info(
            "rate_limit_check",
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_limit=settings.rate_limit_tenant_per_minute,
            user_limit=settings.rate_limit_user_per_minute,
        )
