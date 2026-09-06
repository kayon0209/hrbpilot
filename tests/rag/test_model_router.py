"""PR-04 Model Router tests.

Locks the request-level routing contract:

  - ModelRequest is immutable and explicit (provider/model/timeout/retries)
  - routing never mutates the global active provider
  - route() maps scenario / risk / latency / context / tenant / cost
    dimensions onto an actual configured provider+model, degrading honestly
  - per-request fallback tries the request's own provider list without
    touching global state
"""

from dataclasses import FrozenInstanceError

import pytest

from app.rag.llm.model_router import ModelRequest, ModelRouter, _downshift


def test_model_request_is_immutable():
    req = ModelRequest(provider="deepseek", model="deepseek-chat")
    with pytest.raises(FrozenInstanceError):
        req.provider = "openai"  # type: ignore[misc]
    assert req.provider == "deepseek"
    assert req.log_metadata()["fallback_order"] == []


def test_downshift_moves_economy_stays():
    assert _downshift("economy") == "economy"
    assert _downshift("standard") == "economy"
    assert _downshift("quality") == "standard"


def test_router_never_mutates_global_provider(monkeypatch):
    from app.rag.llm import orchestrator as orch

    monkeypatch.setattr(orch, "_ACTIVE_PROVIDER", "deepseek")
    monkeypatch.setattr(
        orch,
        "_PROVIDER_REGISTRY",
        {
            "deepseek": {"api_key": "k", "model": "deepseek-chat", "base_url": "https://x"},
            "openai": {"api_key": "k", "model": "gpt-4o", "base_url": "https://x"},
        },
    )

    router = ModelRouter()
    req = router.route(scenario_id="policy_qa")
    assert req.provider in {"deepseek", "openai"}
    # Routing must not switch the global provider.
    assert orch._ACTIVE_PROVIDER == "deepseek"
    assert orch.get_active_provider() == "deepseek"


def test_router_quality_tier_prefers_openai(monkeypatch):
    from app.rag.llm import orchestrator as orch

    monkeypatch.setattr(
        orch,
        "_PROVIDER_REGISTRY",
        {
            "deepseek": {"api_key": "k", "model": "deepseek-chat", "base_url": "https://x"},
            "openai": {"api_key": "k", "model": "gpt-4o", "base_url": "https://x"},
        },
    )

    router = ModelRouter()
    req = router.route(scenario_id="hr_case_agent", risk_level="HIGH")
    assert req.provider == "openai"
    assert req.cost_tier == "quality"
    assert req.model == "gpt-4o"


def test_router_economy_prefers_deepseek(monkeypatch):
    from app.rag.llm import orchestrator as orch

    monkeypatch.setattr(
        orch,
        "_PROVIDER_REGISTRY",
        {
            "deepseek": {"api_key": "k", "model": "deepseek-chat", "base_url": "https://x"},
            "openai": {"api_key": "k", "model": "gpt-4o", "base_url": "https://x"},
        },
    )

    router = ModelRouter()
    req = router.route(scenario_id="culture_content", cost_tier="economy")
    assert req.provider == "deepseek"
    assert req.model == "deepseek-chat"
    assert req.cost_tier == "economy"


def test_router_latency_downshifts_unless_high_risk(monkeypatch):
    from app.rag.llm import orchestrator as orch

    monkeypatch.setattr(
        orch,
        "_PROVIDER_REGISTRY",
        {
            "deepseek": {"api_key": "k", "model": "deepseek-chat", "base_url": "https://x"},
            "openai": {"api_key": "k", "model": "gpt-4o", "base_url": "https://x"},
        },
    )

    router = ModelRouter()
    normal = router.route(scenario_id="policy_qa", latency_sensitive=True)
    assert normal.cost_tier == "economy"

    high_risk = router.route(scenario_id="policy_qa", risk_level="HIGH", latency_sensitive=True)
    assert high_risk.cost_tier == "quality"
    assert high_risk.provider == "openai"


def test_router_long_context_upgrades_to_quality(monkeypatch):
    from app.rag.llm import orchestrator as orch

    monkeypatch.setattr(
        orch,
        "_PROVIDER_REGISTRY",
        {
            "deepseek": {"api_key": "k", "model": "deepseek-chat", "base_url": "https://x"},
            "openai": {"api_key": "k", "model": "gpt-4o", "base_url": "https://x"},
        },
    )

    router = ModelRouter()
    req = router.route(scenario_id="policy_qa", estimated_context_tokens=12000)
    assert req.cost_tier == "quality"
    assert req.provider == "openai"


def test_router_honest_degrades_when_preferred_missing(monkeypatch):
    """Only one provider configured: every tier must resolve to it."""
    from app.rag.llm import orchestrator as orch

    monkeypatch.setattr(
        orch,
        "_PROVIDER_REGISTRY",
        {"deepseek": {"api_key": "k", "model": "deepseek-chat", "base_url": "https://x"}},
    )

    router = ModelRouter()
    for scenario_id, cost_tier, risk_level in (
        ("culture_content", "economy", None),
        ("hr_case_agent", None, "HIGH"),
    ):
        req = router.route(
            scenario_id=scenario_id,
            cost_tier=cost_tier,
            risk_level=risk_level or "LOW",
        )
        assert req.provider == "deepseek"
        assert req.model == "deepseek-chat"


def test_fallback_order_contains_configured_providers_once(monkeypatch):
    from app.rag.llm import orchestrator as orch

    monkeypatch.setattr(
        orch,
        "_PROVIDER_REGISTRY",
        {
            "deepseek": {"api_key": "k", "model": "deepseek-chat", "base_url": "https://x"},
            "openai": {"api_key": "k", "model": "gpt-4o", "base_url": "https://x"},
            "zhipu": {"api_key": "k", "model": "glm-4", "base_url": "https://x"},
        },
    )

    router = ModelRouter()
    req = router.route(scenario_id="policy_qa", cost_tier="standard")
    # Fallback order lists every configured provider exactly once (minus primary).
    all_providers = {"deepseek", "openai", "zhipu"}
    assert len(set(req.fallback_order)) == len(req.fallback_order)
    assert set(req.fallback_order) == all_providers - {req.provider}


async def test_fallback_generate_tries_primary_then_backup(monkeypatch):
    """Per-request fallback must not switch the global active provider."""
    from app.rag.llm import fallback as fallback_module
    from app.rag.llm import orchestrator as orch

    calls: list[str] = []

    class _FakeClient:
        def __init__(self, provider):
            self.provider = provider
            self.chat = type("Chat", (), {"completions": self})()
            self.chat.completions = type("Completions", (), {"create": self._create})()

        async def _create(self, **kwargs):
            calls.append(self.provider)
            if self.provider == "deepseek":
                raise ConnectionError("deepseek down")
            return type(
                "Resp",
                (),
                {
                    "choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()],
                    "usage": type("U", (), {"total_tokens": 7})(),
                },
            )()

    monkeypatch.setattr(orch, "_client_for_provider", lambda pid: _FakeClient(pid))
    monkeypatch.setattr(orch, "_PROVIDER_REGISTRY", {"deepseek": {}, "openai": {}})
    # Earlier tests can leave ``_ACTIVE_PROVIDER`` initialized through background
    # eval tasks that escape per-test fixtures; the contract under test is that
    # fallback does not *mutate* the global, so pin the starting value here.
    monkeypatch.setattr(orch, "_ACTIVE_PROVIDER", "")

    req = ModelRequest(
        provider="deepseek",
        model="deepseek-chat",
        fallback_order=("openai",),
        scenario_id="policy_qa",
    )
    content, _tokens, used = await fallback_module.generate_with_fallback([{"role": "user", "content": "hi"}], req)
    assert used == "openai"
    assert content == "ok"
    assert calls == ["deepseek", "openai"]
    # Global provider untouched by request-level fallback.
    assert orch._ACTIVE_PROVIDER == ""


def test_route_metadata_is_content_free():
    req = ModelRequest(provider="deepseek", model="deepseek-chat", scenario_id="policy_qa")
    meta = req.log_metadata()
    assert "question" not in meta
    assert "content" not in meta
