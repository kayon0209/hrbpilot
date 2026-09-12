"""HRBP AI Workbench — request-level LLM degradation (PR-04).

When the primary provider for a request fails, try the remaining providers in
that request's ``fallback_order`` WITHOUT touching the global active provider
(review #11: concurrent requests must not observe each other's fallback).

Unlike the old implementation (which called ``set_active_provider``), this
module is side-effect-free on global state: every provider attempt constructs
its own client via the Model Router path and logs which provider served the
request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from app.rag.llm.model_router import ModelRequest, resolve_model_for
from app.rag.llm.provider_health import record_failure, record_success
from app.shared.logger import get_logger

logger = get_logger(__name__)


async def generate_with_fallback(
    messages: list[dict[str, str]],
    model_request: ModelRequest,
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> tuple[str, int | None, str]:
    """Generate using the request's provider; fall back per-request on failure.

    Returns (content, tokens_used, provider_used). Raises when every provider
    in the request's fallback order has failed (callers decide degradation).
    """
    from app.rag.llm.orchestrator import _client_for_provider

    errors: list[str] = []
    providers = [model_request.provider, *model_request.fallback_order]
    for provider in providers:
        # Each provider is called with ITS OWN model name — the primary
        # model is not portable across providers (P1-05).
        model = resolve_model_for(provider, model_request)
        try:
            client = _client_for_provider(provider)
            response = await client.chat.completions.create(
                model=model,
                messages=cast(Any, messages),
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
            )
            content = response.choices[0].message.content or ""
            tokens = response.usage.total_tokens if response.usage else None
            logger.info(
                "llm_request_generated",
                provider=provider,
                model=model,
                tokens=tokens,
                response_len=len(content),
            )
            # Observed health: a provider that just served a request is not
            # degraded. This is what surfaces on /api/ready, so it must reflect
            # real traffic only — never a synthetic probe.
            record_success(provider)
            return content, tokens, provider
        except Exception as e:
            errors.append(f"{provider}: {str(e)[:120]}")
            logger.warning(
                "llm_request_provider_failed",
                provider=provider,
                model=model,
                error=str(e)[:200],
            )
            # A 402 from the primary provider is invisible today: the request
            # still succeeds via the fallback. Recording it here is what makes
            # the degradation show up in readiness instead of only in logs.
            record_failure(provider)

    logger.error(
        "llm_request_all_providers_failed",
        providers=providers,
        errors=errors,
        scenario_id=model_request.scenario_id,
    )
    raise RuntimeError("all LLM providers failed for this request")


async def stream_with_fallback(
    messages: list[dict[str, str]],
    model_request: ModelRequest,
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> AsyncIterator[tuple[str, str]]:
    """Stream from the request's primary provider; fall back per-request.

    Yields (text_chunk, provider_used) so the caller can log which provider
    served each stream. Raises when every provider has failed.
    """
    from app.rag.llm.orchestrator import _client_for_provider

    errors: list[str] = []
    providers = [model_request.provider, *model_request.fallback_order]
    for provider in providers:
        # Per-provider model resolution — see generate_with_fallback.
        model = resolve_model_for(provider, model_request)
        try:
            client = _client_for_provider(provider)
            stream = await client.chat.completions.create(
                model=model,
                messages=cast(Any, messages),
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            collected = 0
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    collected += 1
                    yield content, provider
            logger.info(
                "llm_request_streamed",
                provider=provider,
                model=model,
                chunks=collected,
            )
            # Only a stream that ran to completion proves the provider is
            # healthy — a stream that dies midway raises and lands in the
            # except branch below instead.
            record_success(provider)
            return
        except Exception as e:
            errors.append(f"{provider}: {str(e)[:120]}")
            logger.warning(
                "llm_request_stream_provider_failed",
                provider=provider,
                model=model,
                error=str(e)[:200],
            )
            # See generate_with_fallback: the fallback may still succeed, which
            # is exactly the silent degradation this record exposes.
            record_failure(provider)

    logger.error(
        "llm_request_stream_all_providers_failed",
        providers=providers,
        errors=errors,
    )
    raise RuntimeError("all LLM providers failed for this stream")
