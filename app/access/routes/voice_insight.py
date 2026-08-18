"""HRBP AI Workbench — Voice Insight API routes.

POST /api/voice-insight/analyze → Start async analysis
GET  /api/voice-insight/progress/{task_id} → Poll task progress
GET  /api/voice-insight/report/{task_id} → Get completed report
GET  /api/voice-insight/history → Recent analysis history
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.access.middleware.decorators import require_auth, require_role
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.voice_insight.orchestrator import VoiceInsightOrchestrator
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
@require_role("hrbp")
async def start_analysis(
    request: Request,
    body: AnalyzeRequest,
):
    """Start voice insight analysis — returns task_id."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")

    # Use inline content if provided, otherwise use document_ids
    if body.content and len(body.content) > 50:
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
@require_role("hrbp")
async def get_progress(task_id: str, request: Request):
    """Poll async task progress."""
    status = orchestrator.get_task_status(task_id)
    if not status:
        raise NotFoundError("Task", task_id)
    return {"task_id": task_id, "status": status.status, "progress": status.progress}


@router.get("/report/{task_id}")
@require_auth
@require_role("hrbp")
async def get_report(task_id: str, request: Request):
    """Get completed insight report."""
    status = orchestrator.get_task_status(task_id)
    if not status:
        raise NotFoundError("Task", task_id)
    if status.status != "completed":
        raise HTTPException(status_code=425, detail=f"任务尚未完成: {status.status}")
    return status.result


@router.get("/history")
@require_auth
@require_role("hrbp")
async def get_history(request: Request, limit: int = 20):
    """Get recent voice insight analysis history."""
    return {"reports": [], "total": 0}
