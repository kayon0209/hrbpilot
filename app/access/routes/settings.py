"""HRBP AI Workbench — Settings API routes.

GET  /api/settings/llm-provider → List available providers + active one
POST /api/settings/llm-provider → Switch active provider
GET  /api/settings/llm-provider/test → Test current provider connectivity
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.access.middleware.decorators import require_auth, require_role
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
@require_role("admin")
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
@require_role("admin")
async def switch_provider(request: Request, body: SwitchProviderBody):
    """Switch the active LLM provider at runtime."""
    success = set_active_provider(body.provider)
    if not success:
        return {
            "status": "error",
            "message": f"Unknown provider: {body.provider}. Available: {[p['id'] for p in get_available_providers()]}",
        }
    return {
        "status": "ok",
        "active": body.provider,
        "active_model": get_active_model(),
    }


@router.get("/llm-provider/test")
@require_auth
@require_role("admin")
async def test_provider(request: Request):
    """Test the current LLM provider with a simple ping."""
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
            "provider": get_active_provider(),
            "model": model,
            "response": content.strip(),
            "tokens": response.usage.total_tokens if response.usage else 0,
        }
    except Exception as e:
        return {
            "status": "error",
            "provider": get_active_provider(),
            "model": get_active_model(),
            "error": str(e),
        }
