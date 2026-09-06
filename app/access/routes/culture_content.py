"""HRBP AI Workbench — Culture Content API routes.

POST /api/culture-content/expand-keywords → Expand keywords
POST /api/culture-content/generate → Generate 4-channel content
GET  /api/culture-content/{content_id} → Get saved content
GET  /api/culture-content/history → Recent generation history
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_db
from app.data.models.scenarios import CultureContent
from app.scenarios.culture_content.orchestrator import CultureContentOrchestrator
from app.scenarios.culture_content.schemas import CultureContentResponse, GenerateContentRequest
from app.shared.errors import DatabaseError, NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/culture-content", tags=["culture-content"])

orchestrator = CultureContentOrchestrator()


@router.post("/expand-keywords")
@require_auth
@require_capability("culture_content")
async def expand_keywords(body: GenerateContentRequest, request: Request):
    """Expand keywords for culture content generation."""
    expansion = orchestrator.expand_keywords(body.keywords)
    return expansion


@router.post("/generate")
@require_auth
@require_capability("culture_content")
async def generate_content(body: GenerateContentRequest, request: Request):
    """Generate 4-channel culture content from keywords."""
    tenant_id = require_tenant_id(request)
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

    content_id = await orchestrator._store_content(tenant_id=tenant_id, user_id=user_id, content=result)
    if not content_id:
        raise DatabaseError("文化内容未能保存，请稍后重试")

    return {"content_id": content_id, "content": result}


@router.get("/history")
@require_auth
@require_capability("culture_content")
async def get_history(
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
):
    """Get recent culture content generation history from database records."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    user_role = getattr(request.state, "user_role", "employee")
    visible_user_ids = await resolve_visible_user_ids(tenant_id, user_id, user_role)
    if not visible_user_ids:
        return {"contents": [], "total": 0}
    rows = (
        (
            await session.execute(
                select(CultureContent)
                .where(
                    CultureContent.tenant_id == tenant_id,
                    CultureContent.created_by.in_(visible_user_ids),
                )
                .order_by(CultureContent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    contents = []
    for row in rows:
        contents.append(
            {
                "content_id": row.id,
                "tone": row.tone,
                "news_article": row.news_article,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )

    return {"contents": contents, "total": len(contents)}


@router.get("/{content_id}")
@require_auth
@require_capability("culture_content")
async def get_content(content_id: str, request: Request):
    """Get saved culture content."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    user_role = getattr(request.state, "user_role", "employee")
    visible_user_ids = await resolve_visible_user_ids(tenant_id, user_id, user_role)
    async for session in get_db(request):
        row = (
            (
                await session.execute(
                    select(CultureContent).where(
                        CultureContent.tenant_id == tenant_id,
                        CultureContent.id == content_id,
                        CultureContent.created_by.in_(visible_user_ids),
                    )
                )
            )
            .scalars()
            .first()
        )
    if not row:
        raise NotFoundError("Content", content_id)
    return CultureContentResponse(
        news_article=row.news_article,
        group_notice=row.group_notice,
        employee_story=row.employee_story,
        event_copy=row.event_copy,
        keywords_used=[],
        tone=row.tone,
    )
