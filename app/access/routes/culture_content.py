"""HRBP AI Workbench — Culture Content API routes.

POST /api/culture-content/expand-keywords → Expand keywords
POST /api/culture-content/generate → Generate 4-channel content
GET  /api/culture-content/{content_id} → Get saved content
GET  /api/culture-content/history → Recent generation history
"""

import uuid

from fastapi import APIRouter, Request

from app.access.middleware.decorators import require_auth, require_role
from app.scenarios.culture_content.orchestrator import CultureContentOrchestrator
from app.scenarios.culture_content.schemas import GenerateContentRequest
from app.shared.errors import NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/culture-content", tags=["culture-content"])

orchestrator = CultureContentOrchestrator()


@router.post("/expand-keywords")
@require_auth
@require_role("hrbp")
async def expand_keywords(body: GenerateContentRequest, request: Request):
    """Expand keywords for culture content generation."""
    expansion = orchestrator.expand_keywords(body.keywords)
    return expansion


@router.post("/generate")
@require_auth
@require_role("hrbp")
async def generate_content(body: GenerateContentRequest, request: Request):
    """Generate 4-channel culture content from keywords."""
    tenant_id = getattr(request.state, "tenant_id", "default")
    user_id = getattr(request.state, "user_id", "unknown")

    keywords = body.keywords
    if body.expand_keywords:
        expansion = orchestrator.expand_keywords(keywords)
        keywords_for_gen = expansion.expanded[:10]
    else:
        keywords_for_gen = keywords

    result = await orchestrator.generate(
        keywords=keywords_for_gen,
        tenant_id=tenant_id,
        user_id=user_id,
        tone=body.tone,
    )

    content_id = str(uuid.uuid4())
    orchestrator.save_content(content_id, result)

    return {"content_id": content_id, "content": result}


@router.get("/{content_id}")
@require_auth
@require_role("hrbp")
async def get_content(content_id: str, request: Request):
    """Get saved culture content."""
    content = orchestrator.get_content(content_id)
    if not content:
        raise NotFoundError("Content", content_id)
    return content


@router.get("/history")
@require_auth
@require_role("hrbp")
async def get_history(request: Request, limit: int = 20):
    """Get recent culture content generation history."""
    return {"contents": [], "total": 0}
