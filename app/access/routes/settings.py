"""HRBP AI Workbench — Settings API routes.

GET  /api/settings/llm-provider → List available providers + active one
POST /api/settings/llm-provider → Switch active provider
GET  /api/settings/llm-provider/test → Test current provider connectivity
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.access.middleware.decorators import require_auth, require_capability
from app.rag.llm.orchestrator import (
    get_active_model,
    get_active_provider,
    get_available_providers,
    get_llm_client,
    set_active_provider,
)
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SwitchProviderBody(BaseModel):
    provider: str  # zhipu | deepseek | openai


@router.get("/llm-provider")
@require_auth
@require_capability("settings")
async def list_providers(request: Request):
    """List all configured LLM providers and the currently active one."""
    providers = get_available_providers()
    active = get_active_provider()
    return {
        "providers": providers,
        "active": active,
        "active_model": get_active_model(),
    }


@router.post("/llm-provider")
@require_auth
@require_capability("settings")
async def switch_provider(request: Request, body: SwitchProviderBody):
    """Switch the active LLM provider at runtime."""
    success = set_active_provider(body.provider)
    if not success:
        available = [p["id"] for p in get_available_providers()]
        return {
            "status": "error",
            "message": f"未知的模型服务：{body.provider}。可选：{'、'.join(available)}",
        }
    return {
        "status": "ok",
        "active": body.provider,
        "active_model": get_active_model(),
    }


def _provider_label_map() -> dict[str, str]:
    return {p["id"]: p.get("label") or p["id"] for p in get_available_providers()}


def _friendly_llm_error(e: Exception) -> str:
    """Translate provider exceptions into user-readable Chinese messages."""
    text = str(e)
    lowered = text.lower()
    if "insufficient" in lowered or "balance" in lowered or "quota" in lowered or "余额" in text:
        return "服务账户余额不足，请联系管理员充值后重试。"
    if "401" in text or "unauthorized" in lowered or "api key" in lowered or "invalid_api_key" in lowered:
        return "凭据无效或已过期，请检查后端配置。"
    if "timeout" in lowered or "timed out" in lowered:
        return "连接超时，请稍后重试。"
    if "connect" in lowered or "unreachable" in lowered:
        return "无法连接到模型服务，请检查网络或服务地址。"
    return "模型服务暂时不可用，请稍后重试。"


@router.get("/llm-provider/test")
@require_auth
@require_capability("settings")
async def test_provider(request: Request):
    """Test the current LLM provider with a simple ping."""
    provider = get_active_provider()
    provider_label = _provider_label_map().get(provider, provider)
    try:
        client = get_llm_client()
        model = get_active_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a test assistant. Reply with 'OK'."},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=10,
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
        return {
            "status": "ok",
            "provider": provider,
            "provider_label": provider_label,
            "model": model,
            "response": content.strip(),
            "tokens": response.usage.total_tokens if response.usage else 0,
        }
    except Exception as e:
        logger.warning("llm_provider_test_failed", provider=provider, error=str(e))
        return {
            "status": "error",
            "provider": provider,
            "provider_label": provider_label,
            "model": get_active_model(),
            "error": _friendly_llm_error(e),
        }
