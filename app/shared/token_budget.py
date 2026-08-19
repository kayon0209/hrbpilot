"""HRBP AI Workbench — Token budget monitoring.

Phase 15 spec: Track LLM token consumption and alert when
approaching monthly budget thresholds.

Primary store is Redis (survives restarts, shared across workers). Falls back
to in-memory tracking when Redis is unavailable so dev mode keeps working.
"""

import threading
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.shared.logger import get_logger
from app.shared.redis_client import get_redis

logger = get_logger(__name__)

# Default monthly token budget per tenant
DEFAULT_MONTHLY_BUDGET = 10_000_000  # 10M tokens

# Alert thresholds (percentage of budget)
WARNING_THRESHOLD = 0.75   # 75%
CRITICAL_THRESHOLD = 0.90  # 90%

# In-memory fallback (used only when Redis is unavailable)
_monthly_usage: dict[str, dict] = defaultdict(
    lambda: {"total": 0, "by_model": defaultdict(int)}
)
_lock = threading.Lock()


def _current_month_key() -> str:
    """Return YYYY-MM key for the current month."""
    return datetime.now(UTC).strftime("%Y-%m")


def _usage_key(tenant_id: str, month_key: str) -> str:
    return f"{tenant_id}:{month_key}"


def _record_in_memory(tenant_id: str, month_key: str, tokens: int, model: str) -> int:
    with _lock:
        entry = _monthly_usage[_usage_key(tenant_id, month_key)]
        entry["total"] += tokens
        entry["by_model"][model] += tokens
        return entry["total"]


async def record_token_usage(
    tenant_id: str,
    tokens: int,
    model: str = "unknown",
    budget: int = DEFAULT_MONTHLY_BUDGET,
) -> dict:
    """Record token usage for a tenant and check against budget.

    Returns a status dict: {within_budget, usage_pct, alert_level}.
    """
    month_key = _current_month_key()
    total: int

    redis = await get_redis()
    if redis is not None:
        try:
            pipe = redis.pipeline()
            pipe.hincrby(f"token_usage:{_usage_key(tenant_id, month_key)}:meta", "total", tokens)
            pipe.hincrby(f"token_usage:{_usage_key(tenant_id, month_key)}:by_model", model, tokens)
            pipe.expire(f"token_usage:{_usage_key(tenant_id, month_key)}:meta", 90 * 24 * 3600)
            pipe.expire(f"token_usage:{_usage_key(tenant_id, month_key)}:by_model", 90 * 24 * 3600)
            results = await pipe.execute()
            total = int(results[0])
        except Exception as exc:
            logger.warning("token_budget_redis_fallback", error=str(exc), tenant_id=tenant_id)
            total = _record_in_memory(tenant_id, month_key, tokens, model)
    else:
        total = _record_in_memory(tenant_id, month_key, tokens, model)

    usage_pct = total / budget
    alert_level = "ok"

    if usage_pct >= CRITICAL_THRESHOLD:
        alert_level = "critical"
        logger.warning(
            "token_budget_critical",
            tenant_id=tenant_id,
            month=month_key,
            tokens_used=total,
            budget=budget,
            usage_pct=round(usage_pct * 100, 1),
        )
    elif usage_pct >= WARNING_THRESHOLD:
        alert_level = "warning"
        logger.info(
            "token_budget_warning",
            tenant_id=tenant_id,
            month=month_key,
            tokens_used=total,
            budget=budget,
            usage_pct=round(usage_pct * 100, 1),
        )

    return {
        "within_budget": usage_pct < 1.0,
        "usage_pct": round(usage_pct * 100, 1),
        "alert_level": alert_level,
        "tokens_used": total,
        "budget": budget,
    }


async def get_monthly_usage(tenant_id: str | None = None) -> dict:
    """Return current month's token usage statistics.

    If tenant_id is None, return all tenants' usage.
    """
    month_key = _current_month_key()

    redis = await get_redis()
    if redis is not None:
        try:
            if tenant_id is None:
                total = 0
                by_model: dict[str, int] = {}
                async for meta_key in redis.scan_iter(match=f"token_usage:*:{month_key}:meta"):
                    scan_total = await redis.hget(meta_key, "total")
                    total += int(scan_total or 0)
                    model_key = meta_key.replace(":meta", ":by_model")
                    model_counts = await redis.hgetall(model_key)
                    for m, c in model_counts.items():
                        by_model[m] = by_model.get(m, 0) + int(c)
                return {"month": month_key, "total_tokens": total, "by_model": by_model}
            total = await redis.hget(f"token_usage:{_usage_key(tenant_id, month_key)}:meta", "total")
            model_counts = await redis.hgetall(f"token_usage:{_usage_key(tenant_id, month_key)}:by_model")
            return {
                "month": month_key,
                "total_tokens": int(total or 0),
                "by_model": {m: int(c) for m, c in model_counts.items()},
            }
        except Exception as exc:
            logger.warning("token_budget_redis_read_fallback", error=str(exc), tenant_id=tenant_id)

    if tenant_id is None:
        totals: dict[str, Any] = {"total": 0, "by_model": defaultdict(int)}
        for key, entry in _monthly_usage.items():
            if key.endswith(f":{month_key}"):
                totals["total"] += entry["total"]
                for model, count in entry["by_model"].items():
                    totals["by_model"][model] += count
        entry = totals
    else:
        entry = _monthly_usage.get(_usage_key(tenant_id, month_key), {"total": 0, "by_model": {}})

    return {
        "month": month_key,
        "total_tokens": entry["total"],
        "by_model": dict(entry["by_model"]),
    }
