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

        tenant_ok = await self._within(redis, f"ratelimit:tenant:{tenant_id}", tenant_limit)
        user_ok = await self._within(redis, f"ratelimit:user:{user_id}", user_limit)
        if not tenant_ok or not user_ok:
            logger.warning(
                "rate_limit_exceeded",
                tenant_id=tenant_id,
                user_id=user_id,
                tenant_exceeded=not tenant_ok,
                user_exceeded=not user_ok,
            )
            raise RateLimitError("请求过于频繁，请稍后再试")

    async def check_bucket(self, bucket: str, key: str, limit: int) -> None:
        """按**任意**维度限流：``bucket`` 是维度名，``key`` 是该维度下的主体。

        为什么要有它：``check`` 只认 tenant + user 两个维度，而外部 Agent 的调用
        必须能按**客户端**与**安装实例**分别计数 —— 否则一个行为异常的客户端会
        占掉它所有用户的配额，而按租户根本看不出是哪个客户端干的（方案 §WP3）。

        Redis 不可用时与 ``check`` 同样的策略：非生产环境放行，生产环境拒绝。
        这里**不做**本地内存兜底计数 —— 多实例下每个实例各自计数，实际额度会变成
        "单实例上限 × 实例数"，那是一个看起来生效、实际没生效的限流。
        """
        await self.check_buckets([(bucket, key, limit)])

    async def check_buckets(self, checks: list[tuple[str, str, int]]) -> None:
        """Check several dimensions in one Redis pipeline round trip."""
        checks = [item for item in checks if item[2] > 0]
        if not checks:
            return
        redis = self.redis or await get_redis()
        if redis is None:
            if settings.rate_limit_fail_open and not settings.is_production:
                logger.info("rate_limit_passthrough", buckets=[item[0] for item in checks], reason="redis_unavailable")
                return
            raise RateLimitError("请求过于频繁，请稍后再试")
        now_ms = int(time.time() * 1000)
        window_start = now_ms - _WINDOW_SECONDS * 1000
        pipeline = redis.pipeline(transaction=True)
        for bucket, key, _limit in checks:
            redis_key = f"ratelimit:{bucket}:{key}"
            member = f"{time.time_ns()}:{uuid4().hex}"
            pipeline.zremrangebyscore(redis_key, 0, window_start)
            pipeline.zadd(redis_key, {member: now_ms})
            pipeline.expire(redis_key, _WINDOW_SECONDS + 5)
            pipeline.zcard(redis_key)
        results = await pipeline.execute()
        exceeded = [
            (bucket, key, limit)
            for index, (bucket, key, limit) in enumerate(checks)
            if int(results[index * 4 + 3]) > limit
        ]
        if exceeded:
            bucket, key, limit = exceeded[0]
            logger.warning("rate_limit_exceeded", bucket=bucket, exceeded_key=key, limit=limit)
            raise RateLimitError("请求过于频繁，请稍后再试")

    @staticmethod
    async def _within(redis: object, key: str, limit: int) -> bool:
        """滑动窗口计数：记录本次请求，然后判断窗口内是否超阈值。"""
        now_ms = int(time.time() * 1000)
        window_start = now_ms - _WINDOW_SECONDS * 1000
        member = f"{time.time_ns()}:{uuid4().hex}"
        await redis.zadd(key, {member: now_ms})  # type: ignore[attr-defined]
        await redis.zremrangebyscore(key, 0, window_start)  # type: ignore[attr-defined]
        await redis.expire(key, _WINDOW_SECONDS + 5)  # type: ignore[attr-defined]
        count = await redis.zcard(key)  # type: ignore[attr-defined]
        return int(count) <= limit
