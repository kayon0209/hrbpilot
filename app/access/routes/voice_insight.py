"""HRBP AI Workbench — Voice Insight API routes.

POST /api/voice-insight/analyze → Start async analysis
GET  /api/voice-insight/progress/{task_id} → Poll task progress
GET  /api/voice-insight/report/{task_id} → Get completed report
GET  /api/voice-insight/history → Recent analysis history
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_db
from app.data.models.infra import AsyncTask
from app.scenarios.voice_insight.orchestrator import VoiceInsightOrchestrator
from app.scenarios.voice_insight.schemas import InsightReportResponse
from app.shared.errors import NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/voice-insight", tags=["voice-insight"])

orchestrator = VoiceInsightOrchestrator()


class AnalyzeRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list)
    content: str = Field("", description="直接提供文本内容（无需文档ID时）")


@router.post("/analyze")
@require_auth
@require_capability("voice_insight")
async def start_analysis(
    request: Request,
    body: AnalyzeRequest,
):
    """Start voice insight analysis — returns task_id."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")

    # Use inline content if provided, otherwise use document_ids
    if body.content and len(body.content) >= 50:
        docs = [{"id": "inline-001", "content": body.content}]
    elif body.document_ids:
        # Mock: convert IDs to document objects (replace with DB query in prod)
        docs = [{"id": did, "content": f"[文档 {did} 内容待加载]"} for did in body.document_ids]
    else:
        raise ValidationError("请提供待分析的文档内容或文档ID列表")

    task_id = await orchestrator.start_async_task(docs, tenant_id, user_id)
    return {"task_id": task_id, "status": "pending"}


@router.get("/progress/{task_id}")
@require_auth
@require_capability("voice_insight")
async def get_progress(task_id: str, request: Request):
    """Poll async task progress."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(
        tenant_id, request.state.user_id, request.state.user_role
    )
    status = await orchestrator.get_task_status(task_id, tenant_id, visible_user_ids)
    if not status:
        raise NotFoundError("Task", task_id)

    # Lazy staleness sweep: dead-worker tasks must surface as failed, not hang.
    if status.status in ("pending", "running"):
        from app.scenarios.tasks import expire_stale_tasks

        await expire_stale_tasks(tenant_id)
        status = await orchestrator.get_task_status(task_id, tenant_id, visible_user_ids)
        if not status:
            raise NotFoundError("Task", task_id)

    return {"task_id": task_id, "status": status.status, "error": status.error}


@router.get("/report/{task_id}")
@require_auth
@require_capability("voice_insight")
async def get_report(task_id: str, request: Request):
    """Get completed insight report."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(
        tenant_id, request.state.user_id, request.state.user_role
    )
    status = await orchestrator.get_task_status(task_id, tenant_id, visible_user_ids)
    if not status:
        raise NotFoundError("Task", task_id)
    if status.status != "completed":
        raise HTTPException(status_code=425, detail=f"任务尚未完成: {status.status}")
    return status.result


@router.get("/history")
@require_auth
@require_capability("voice_insight")
async def get_history(
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
):
    """Get recent voice insight analysis history from async task records."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(
        tenant_id, request.state.user_id, request.state.user_role
    )
    rows = (
        (
            await session.execute(
                select(AsyncTask)
                .where(
                    AsyncTask.tenant_id == tenant_id,
                    AsyncTask.type == "voice_insight",
                    AsyncTask.status == "completed",
                    AsyncTask.result_json.is_not(None),
                    AsyncTask.created_by.in_(visible_user_ids),
                )
                .order_by(AsyncTask.completed_at.desc().nullslast(), AsyncTask.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    reports = []
    for row in rows:
        result = None
        try:
            result = InsightReportResponse.model_validate_json(row.result_json or "")
        except Exception:
            result = None
        reports.append(
            {
                "task_id": row.id,
                "status": row.status,
                "result": result.model_dump() if result else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return {"reports": reports, "total": len(reports)}
