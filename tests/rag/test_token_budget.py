import pytest

from app.shared import token_budget


@pytest.mark.asyncio
async def test_usage_is_partitioned_by_tenant(monkeypatch) -> None:
    async def fake_get_redis():
        return None

    monkeypatch.setattr(token_budget, "_current_month_key", lambda: "2026-08")
    monkeypatch.setattr(token_budget, "get_redis", fake_get_redis)
    token_budget._monthly_usage.clear()
    await token_budget.record_token_usage("tenant-a", 10)
    await token_budget.record_token_usage("tenant-b", 20)

    usage_a = await token_budget.get_monthly_usage("tenant-a")
    usage_b = await token_budget.get_monthly_usage("tenant-b")
    usage_all = await token_budget.get_monthly_usage()

    assert usage_a["total_tokens"] == 10
    assert usage_b["total_tokens"] == 20
    assert usage_all["total_tokens"] >= 30
