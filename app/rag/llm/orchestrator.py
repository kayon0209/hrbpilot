"""HRBP AI Workbench — LLM orchestration with multi-provider support.

Providers:
  - zhipu:    GLM-4,      base_url=https://open.bigmodel.cn/api/paas/v4
  - deepseek:  deepseek-chat, base_url=https://api.deepseek.com/v1
  - openai:    gpt-4o,      base_url=https://api.openai.com/v1

All providers use OpenAI-compatible API format via the openai SDK.
Active provider can be switched at runtime via the /api/settings/llm-provider endpoint.
"""

import re
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)


# ---- Provider registry ----


def _label_for(provider_id: str, model: str, base_url: str) -> str:
    """Derive a display label that reflects the actual backend endpoint."""
    if "gitee" in base_url:
        return f"Gitee AI · {model}"
    if provider_id == "zhipu":
        return f"智谱 · {model}"
    if provider_id == "deepseek":
        return f"DeepSeek · {model}"
    if provider_id == "openai":
        return f"OpenAI · {model}"
    return model


def _build_provider_registry() -> dict[str, dict]:
    """Build the provider config registry from settings."""
    registry = {}

    # Zhipu (primary)
    if settings.llm_api_key and settings.llm_api_key != "change-me":
        registry["zhipu"] = {
            "api_key": settings.llm_api_key,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "label": _label_for("zhipu", settings.llm_model, settings.llm_base_url),
        }

    # DeepSeek (fallback)
    if settings.deepseek_api_key:
        registry["deepseek"] = {
            "api_key": settings.deepseek_api_key,
            "model": settings.deepseek_model,
            "base_url": settings.deepseek_base_url,
            "label": _label_for("deepseek", settings.deepseek_model, settings.deepseek_base_url),
        }

    # OpenAI (fallback)
    if settings.openai_api_key:
        registry["openai"] = {
            "api_key": settings.openai_api_key,
            "model": settings.openai_model,
            "base_url": settings.openai_base_url,
            "label": _label_for("openai", settings.openai_model, settings.openai_base_url),
        }

    return registry


_PROVIDER_REGISTRY: dict[str, dict] = {}
_ACTIVE_PROVIDER: str = ""
_REDIS_PROVIDER_KEY = "llm:active_provider"
_SYNC_REDIS = None
_SYNC_REDIS_TRIED = False
# Per-provider client cache (lazy init)
_CLIENTS: dict[str, AsyncOpenAI] = {}


def _get_sync_redis():
    """Return a synchronous Redis client, or None if Redis is unavailable."""
    global _SYNC_REDIS, _SYNC_REDIS_TRIED
    if _SYNC_REDIS_TRIED:
        return _SYNC_REDIS
    _SYNC_REDIS_TRIED = True
    try:
        import redis as sync_redis

        _SYNC_REDIS = sync_redis.from_url(settings.redis_url, decode_responses=True)
        _SYNC_REDIS.ping()
        logger.info("sync_redis_connected_for_llm_provider")
    except Exception:
        _SYNC_REDIS = None
        logger.warning("sync_redis_unavailable_provider_falls_back_to_module_var")
    return _SYNC_REDIS


def _persist_active_provider(provider: str) -> None:
    """Persist the active provider to Redis so other workers can pick it up."""
    r = _get_sync_redis()
    if r is not None:
        try:
            r.set(_REDIS_PROVIDER_KEY, provider)
        except Exception as e:
            logger.warning("redis_provider_persist_failed", error=str(e))


def _load_active_provider() -> str | None:
    """Load the active provider from Redis; returns None if not set/unavailable."""
    r = _get_sync_redis()
    if r is None:
        return None
    try:
        value = r.get(_REDIS_PROVIDER_KEY)
        return value if isinstance(value, str) else None
    except Exception as e:
        logger.warning("redis_provider_load_failed", error=str(e))
        return None


def _init_providers():
    """Initialize the provider registry on first use."""
    global _PROVIDER_REGISTRY, _ACTIVE_PROVIDER
    if not _PROVIDER_REGISTRY:
        _PROVIDER_REGISTRY = _build_provider_registry()
        default = (
            settings.llm_provider
            if settings.llm_provider in _PROVIDER_REGISTRY
            else (next(iter(_PROVIDER_REGISTRY)) if _PROVIDER_REGISTRY else "zhipu")
        )
        cached = _load_active_provider()
        if cached is not None and cached in _PROVIDER_REGISTRY:
            _ACTIVE_PROVIDER = cached
        else:
            _ACTIVE_PROVIDER = default
        logger.info(
            "llm_providers_initialized",
            providers=list(_PROVIDER_REGISTRY.keys()),
            active=_ACTIVE_PROVIDER,
        )


def get_available_providers() -> list[dict]:
    """Return list of available providers with their config (keys masked)."""
    _init_providers()
    return [
        {
            "id": pid,
            "label": p["label"],
            "model": p["model"],
            "active": pid == _ACTIVE_PROVIDER,
            "api_key_masked": p["api_key"][:8] + "..." if p["api_key"] else "",
        }
        for pid, p in _PROVIDER_REGISTRY.items()
    ]


def get_active_provider() -> str:
    """Return the currently active provider ID."""
    _init_providers()
    return _ACTIVE_PROVIDER


def set_active_provider(provider_id: str) -> bool:
    """Switch the active LLM provider at runtime. Returns True if successful.

    Persists to Redis so all worker processes see the same active provider.
    """
    global _ACTIVE_PROVIDER
    _init_providers()
    if provider_id in _PROVIDER_REGISTRY:
        old = _ACTIVE_PROVIDER
        _ACTIVE_PROVIDER = provider_id
        _persist_active_provider(provider_id)
        logger.info("llm_provider_switched", old=old, new=provider_id)
        return True
    return False


def get_active_config() -> dict:
    """Get the config for the currently active provider."""
    _init_providers()
    return _PROVIDER_REGISTRY.get(_ACTIVE_PROVIDER, {})


def get_llm_client() -> AsyncOpenAI:
    """Get or create the LLM client for the active provider."""
    _init_providers()
    config = get_active_config()
    if not config:
        raise ValueError("No LLM provider configured. Set DEEPSEEK_API_KEY or LLM_API_KEY in .env")

    provider = _ACTIVE_PROVIDER
    if provider not in _CLIENTS:
        _CLIENTS[provider] = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            # Fail fast instead of hanging the request when the LLM is slow or down.
            timeout=60.0,
            max_retries=2,
        )
    return _CLIENTS[provider]


def get_active_model() -> str:
    """Get the model name for the active provider."""
    return str(get_active_config().get("model", "gpt-4"))


# ---- Prompt building ----


def _build_system_prompt(prompt_template: str, context: list[dict]) -> str:
    """Build system prompt from template and RAG context."""
    if not prompt_template:
        return "你是一位专业的HR助手。请根据提供的上下文回答用户问题。"

    context_lines = []
    for i, chunk in enumerate(context, 1):
        source = chunk.get("source", "unknown")
        section = chunk.get("section", "unknown")
        content = chunk.get("content", "")
        context_lines.append(f"---\n[片段 {i}] 来源: {source} | 章节: {section}\n{content}\n---")

    context_block = "\n".join(context_lines) if context_lines else "（无相关制度文档片段）"

    try:
        from jinja2 import Template

        rendered = Template(prompt_template).render(context=context, query="", content=context_block)
        return str(rendered).strip()
    except Exception:
        system_prompt = prompt_template
        for_pattern = r"\{%\s*for\s+\w+\s+in\s+\w+\s*%\}.*?\{%\s*endfor\s*%\}"
        system_prompt = re.sub(for_pattern, context_block, system_prompt, flags=re.DOTALL)
        system_prompt = re.sub(r"\{\{.*?\}\}", "", system_prompt)
        return system_prompt.strip()


# ---- LLM Orchestrator ----


class LLMOrchestrator:
    """Generate LLM responses with context-aware prompts."""

    async def generate(
        self,
        prompt_template: str,
        context: list[dict],
        query: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> tuple[str, int | None]:
        """Build prompt from template + context, call LLM, return response."""
        system_prompt = _build_system_prompt(prompt_template, context)

        client = get_llm_client()
        model = get_active_model()

        logger.info(
            "llm_call_starting",
            model=model,
            provider=get_active_provider(),
            query_len=len(query),
            context_chunks=len(context),
        )

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
            )

            content = response.choices[0].message.content or ""
            tokens = response.usage.total_tokens if response.usage else None

            logger.info(
                "llm_call_completed",
                model=model,
                provider=get_active_provider(),
                tokens=tokens,
                response_len=len(content),
            )

            return content, tokens

        except Exception as e:
            logger.error("llm_call_failed", error=str(e), model=model, provider=get_active_provider())
            raise

    async def generate_stream(
        self,
        prompt_template: str,
        context: list[dict],
        query: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """Stream LLM response chunk by chunk (for SSE)."""
        system_prompt = _build_system_prompt(prompt_template, context)

        client = get_llm_client()
        model = get_active_model()

        logger.info(
            "llm_stream_starting",
            model=model,
            provider=get_active_provider(),
            query_len=len(query),
        )

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )

            collected_chunks = 0
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    collected_chunks += 1
                    yield content

            logger.info(
                "llm_stream_completed",
                model=model,
                provider=get_active_provider(),
                chunks=collected_chunks,
            )

        except Exception as e:
            logger.error("llm_stream_failed", error=str(e), model=model, provider=get_active_provider())
            raise
