"""HRBP AI Workbench — LLM degradation strategy.

Phase 15 spec: When the primary LLM provider is unavailable,
automatically fall back to the next available provider in order:
  Primary (zhipu) → Fallback 1 (deepseek) → Fallback 2 (openai) → Error

The fallback is transparent to callers — they just call generate()
and get a response from whichever provider is available.
"""

from app.rag.llm.orchestrator import (
    get_active_provider,
    get_active_config,
    get_llm_client,
    get_active_model,
    _PROVIDER_REGISTRY,
    _CLIENTS,
    set_active_provider,
)
from app.shared.logger import get_logger

logger = get_logger(__name__)

# Provider fallback order (first available wins)
FALLBACK_ORDER = ["zhipu", "deepseek", "openai"]


def get_fallback_order() -> list[str]:
    """Return the ordered list of providers, starting with the active one."""
    active = get_active_provider()
    order = [active] + [p for p in FALLBACK_ORDER if p != active]
    # Filter to only providers that are actually configured
    return [p for p in order if p in _PROVIDER_REGISTRY]


async def try_generate_with_fallback(
    prompt_template: str,
    context: list[dict],
    query: str,
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> tuple[str, int | None, str]:
    """Generate response with automatic provider fallback.

    Tries each provider in fallback order. On success, returns
    (content, tokens_used, provider_used).
    On total failure, returns an error message.
    """
    from app.rag.llm.orchestrator import LLMOrchestrator

    original_provider = get_active_provider()
    errors: list[str] = []

    for provider in get_fallback_order():
        try:
            if provider != get_active_provider():
                logger.warning(
                    "llm_fallback_attempt",
                    from_provider=original_provider,
                    to_provider=provider,
                    errors=errors,
                )
                set_active_provider(provider)

            orchestrator = LLMOrchestrator()
            content, tokens = await orchestrator.generate(
                prompt_template=prompt_template,
                context=context,
                query=query,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            logger.info(
                "llm_generate_success",
                provider=provider,
                tokens=tokens,
            )
            return content, tokens, provider

        except Exception as e:
            errors.append(f"{provider}: {str(e)[:100]}")
            logger.error(
                "llm_provider_failed",
                provider=provider,
                error=str(e)[:200],
            )

    # All providers failed — return degraded response
    logger.critical("llm_all_providers_failed", errors=errors)
    return (
        "抱歉，当前所有 AI 服务均不可用，请稍后再试。",
        None,
        "none",
    )
