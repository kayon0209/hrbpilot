from app.shared import token_budget


def test_usage_is_partitioned_by_tenant(monkeypatch) -> None:
    monkeypatch.setattr(token_budget, "_current_month_key", lambda: "2026-08")
    token_budget._monthly_usage.clear()
    token_budget.record_token_usage("tenant-a", 10)
    token_budget.record_token_usage("tenant-b", 20)

    assert token_budget.get_monthly_usage("tenant-a")["total_tokens"] == 10
    assert token_budget.get_monthly_usage("tenant-b")["total_tokens"] == 20
    assert token_budget.get_monthly_usage()["total_tokens"] == 30
