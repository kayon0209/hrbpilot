"""HRBP AI Workbench — Token budget monitoring.

Phase 15 spec: Track LLM token consumption and alert when
approaching monthly budget thresholds.
"""

import threading
from collections import defaultdict
from datetime import datetime

from app.shared.logger import get_logger

logger = get_logger(__name__)

# Default monthly token budget per tenant
DEFAULT_MONTHLY_BUDGET = 10_000_000  # 10M tokens

# Alert thresholds (percentage of budget)
WARNING_THRESHOLD = 0.75   # 75%
CRITICAL_THRESHOLD = 0.90  # 90%

# In-memory tracking (swap to Redis for persistence across restarts)
_monthly_usage: dict[str, dict] = defaultdict(
    lambda: {"total": 0, "by_model": defaultdict(int)}
)
_lock = threading.Lock()


def _current_month_key() -> str:
    """Return YYYY-MM key for the current month."""
    return datetime.now().strftime("%Y-%m")


def record_token_usage(
    tenant_id: str,
    tokens: int,
    model: str = "unknown",
    budget: int = DEFAULT_MONTHLY_BUDGET,
) -> dict:
    """Record token usage for a tenant and check against budget.

    Returns a status dict: {within_budget, usage_pct, alert_level}.
    """
    month_key = _current_month_key()

    with _lock:
        entry = _monthly_usage[month_key]
        entry["total"] += tokens
        entry["by_model"][model] += tokens
        total = entry["total"]

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


def get_monthly_usage(tenant_id: str | None = None) -> dict:
    """Return current month's token usage statistics.

    If tenant_id is None, return all tenants' usage.
    """
    month_key = _current_month_key()
    entry = _monthly_usage.get(month_key, {"total": 0, "by_model": {}})

    return {
        "month": month_key,
        "total_tokens": entry["total"],
        "by_model": dict(entry["by_model"]),
    }
