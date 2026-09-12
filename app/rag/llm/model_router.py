"""HRBP AI Workbench — Request-level Model Router (PR-04).

Builds an immutable :class:`ModelRequest` per call from routing dimensions
(scenario, risk, latency, context length, tenant policy, cost budget).

Design rules (upgrade plan §7.3 / review #11):

  - a ``ModelRequest`` is frozen and explicit about
    provider / model / timeout / retries / fallback order
  - routing NEVER reads or writes the global ``_ACTIVE_PROVIDER`` — the
    global switch remains only as the *default provider* used when a caller
    does not route explicitly
  - provider tiering degrades honestly: if a preferred provider is not
    configured, the router falls back to the default provider/model
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.llm import orchestrator as _orch

# Cost tiers — from cheapest to most capable.
COST_TIERS = ("economy", "standard", "quality")
_COST_INDEX = {tier: i for i, tier in enumerate(COST_TIERS)}

# Scenario → default cost tier. Long-context / high-stakes scenarios default
# higher; bulk-content scenarios default economy.
SCENARIO_DEFAULT_TIER = {
    "policy_qa": "standard",
    "interview_digest": "standard",
    "voice_insight": "standard",
    "culture_content": "economy",
    "weekly_report": "economy",
    "hr_case_agent": "standard",
    "rag_pipeline": "standard",
    "default": "standard",
}

# Context-length threshold (estimated input tokens) above which a larger
# window is preferred.
LONG_CONTEXT_TOKEN_THRESHOLD = 8000

# Risk escalation: HIGH-risk calls must not be downgraded by latency demands.
_HIGH_RISK_TIER = "quality"


@dataclass(frozen=True)
class ModelRequest:
    """Immutable per-request model selection.

    ``provider`` and ``model`` name the exact endpoint; ``fallback_order`` is
    the ordered list of providers to try in this request when the primary
    fails. ``fallback_models`` carries a per-provider model name so each
    fallback attempt uses a model that actually exists on that provider —
    model names are NOT portable across providers (P1-05). No field may be
    mutated after construction.
    """

    provider: str
    model: str
    timeout: float = 60.0
    max_retries: int = 2
    fallback_order: tuple[str, ...] = ()
    fallback_models: tuple[tuple[str, str], ...] = ()
    scenario_id: str = "default"
    risk_level: str = "LOW"
    latency_sensitive: bool = False
    estimated_context_tokens: int = 0
    tenant_policy: str | None = None
    cost_tier: str = "standard"

    def log_metadata(self) -> dict:
        """Metadata-only view for structured logs (never content)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "scenario_id": self.scenario_id,
            "risk_level": self.risk_level,
            "latency_sensitive": self.latency_sensitive,
            "estimated_context_tokens": self.estimated_context_tokens,
            "tenant_policy": self.tenant_policy,
            "cost_tier": self.cost_tier,
            "fallback_order": list(self.fallback_order),
            "fallback_models": {p: m for p, m in self.fallback_models},
        }


def resolve_model_for(provider: str, model_request: ModelRequest) -> str:
    """Resolve the model name for ``provider`` within this request.

    Order: the request's per-provider mapping → the provider's registry
    model → the primary model name. The registry lookup is what makes
    cross-provider fallback viable: ``deepseek-chat`` does not exist on
    zhipu/openai endpoints, so reusing the primary name across providers
    made every fallback attempt fail before this mapping existed.
    """
    mapping = dict(model_request.fallback_models)
    if provider in mapping:
        return mapping[provider]
    registry_model = _orch._PROVIDER_REGISTRY.get(provider, {}).get("model")
    if registry_model:
        return str(registry_model)
    return model_request.model


class ModelRouter:
    """Resolve routing dimensions into an immutable ModelRequest."""

    def __init__(self) -> None:
        self._registry: dict[str, dict] | None = None

    def _providers(self) -> dict[str, dict]:
        if self._registry is None:
            self._registry = dict(_orch._PROVIDER_REGISTRY)
        return self._registry

    # -- tier resolution -------------------------------------------------

    def _tier_for(
        self,
        *,
        scenario_id: str,
        risk_level: str,
        latency_sensitive: bool,
        estimated_context_tokens: int,
        cost_tier: str | None,
    ) -> str:
        base = SCENARIO_DEFAULT_TIER.get(scenario_id, SCENARIO_DEFAULT_TIER["default"])
        tier = base

        if risk_level and str(risk_level).upper() == "HIGH":
            tier = _HIGH_RISK_TIER
        elif latency_sensitive:
            # Prefer a cheaper/faster model unless risk already escalated.
            tier = _downshift(tier)
        if estimated_context_tokens and estimated_context_tokens > LONG_CONTEXT_TOKEN_THRESHOLD:
            tier = "quality"

        if cost_tier is not None:
            tier = cost_tier if cost_tier in COST_TIERS else tier
        return tier

    def _select_provider_model(self, tier: str, default_provider: str) -> tuple[str, str]:
        """Map a tier to (provider, model) using what is actually configured.

        economy  → deepseek (flash-class) when available, else default
        standard → the default provider/model
        quality  → openai (gpt-4o-class) when available, else default

        Honest degradation: if the preferred provider is not configured, fall
        back to the default provider/model — never invent a fake endpoint.
        """
        providers = self._providers()
        default_config = providers.get(default_provider) or _orch.get_active_config()
        default_model = str((default_config or {}).get("model", "gpt-4o"))

        if tier == "economy":
            for pid in ("deepseek", "zhipu"):
                if pid in providers:
                    return pid, str(providers[pid].get("model", default_model))
        if tier == "quality":
            for pid in ("openai", "zhipu"):
                if pid in providers:
                    return pid, str(providers[pid].get("model", default_model))
        return default_provider, default_model

    # -- public API ------------------------------------------------------

    def route(
        self,
        *,
        scenario_id: str = "default",
        risk_level: str = "LOW",
        latency_sensitive: bool = False,
        estimated_context_tokens: int = 0,
        tenant_policy: str | None = None,
        cost_tier: str | None = None,
        default_provider: str | None = None,
    ) -> ModelRequest:
        """Resolve one immutable ModelRequest for this call.

        ``default_provider`` defaults to the global active provider (read-only
        here — this method never switches it). Routing never mutates global
        state.
        """
        if default_provider is None:
            default_provider = _orch.get_active_provider()

        providers = self._providers()
        if default_provider not in providers:
            default_provider = next(iter(providers), "")

        tier = self._tier_for(
            scenario_id=scenario_id,
            risk_level=risk_level,
            latency_sensitive=latency_sensitive,
            estimated_context_tokens=estimated_context_tokens,
            cost_tier=cost_tier,
        )
        provider, model = self._select_provider_model(tier, default_provider)

        # Fallback order: primary first, then every configured provider once.
        fallback = [p for p in providers if p != provider]
        fallback_models = tuple(
            (pid, str(providers[pid].get("model", model))) for pid in [provider, *fallback] if pid in providers
        )
        return ModelRequest(
            provider=provider,
            model=model,
            scenario_id=scenario_id,
            risk_level=risk_level,
            latency_sensitive=latency_sensitive,
            estimated_context_tokens=estimated_context_tokens,
            tenant_policy=tenant_policy,
            cost_tier=tier,
            fallback_order=tuple(fallback),
            fallback_models=fallback_models,
        )


def _downshift(tier: str) -> str:
    idx = _COST_INDEX.get(tier, 1)
    return COST_TIERS[max(0, idx - 1)] if idx > 0 else tier
