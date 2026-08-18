"""HRBP AI Workbench — Weekly Report API routes.

POST /api/weekly-report/generate → Generate weekly report draft
POST /api/weekly-report/save → Save/publish a report
GET  /api/weekly-report/{report_id} → Get saved report
GET  /api/weekly-report/history → Recent report history
"""

import uuid

from fastapi import APIRouter, Request

from app.access.middleware.decorators import require_auth, require_role
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.weekly_report.orchestrator import WeeklyReportOrchestrator
from app.scenarios.weekly_report.schemas import GenerateRequest, SaveRequest
from app.shared.errors import NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/weekly-report", tags=["weekly-report"])

orchestrator = WeeklyReportOrchestrator()


@router.post("/generate")
@require_auth
@require_role("hrbp")
async def generate_report(
    body: GenerateRequest,
    request: Request,
):
    """Generate a weekly report draft from multi-source data."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")

    # Use mock data if no source_ids provided
    source_data = [
        {"type": "interview_digest", "id": sid, "content": f"[访谈结果 {sid} 内容待加载]"}
        for sid in body.source_ids
    ]

    if not source_data:
        source_data = [
            {"type": "interview_digest", "id": "auto", "content": "本周3场访谈，2位员工表达薪酬诉求，1位有离职倾向"},
            {"type": "voice_insight", "id": "auto", "content": "声音洞察: 薪酬满意度下降趋势，加班疲劳情绪上升"},
        ]

    result = await orchestrator.generate(
        period=body.period,
        source_data=source_data,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    report_id = str(uuid.uuid4())
    orchestrator.save_report(report_id, result)

    return {"report_id": report_id, "report": result, "is_draft": body.draft_mode}


@router.post("/save")
@require_auth
@require_role("hrbp")
async def save_report(body: SaveRequest, request: Request):
    """Save or publish a weekly report."""
    report = orchestrator.get_report(body.report_id)
    if not report:
        raise NotFoundError("Report", body.report_id)

    if body.action == "publish":
        logger.info("weekly_report_published", report_id=body.report_id)
    else:
        logger.info("weekly_report_saved", report_id=body.report_id)

    return {"report_id": body.report_id, "action": body.action, "status": "saved"}


@router.get("/{report_id}")
@require_auth
@require_role("hrbp")
async def get_report(report_id: str, request: Request):
    """Get a saved weekly report."""
    report = orchestrator.get_report(report_id)
    if not report:
        raise NotFoundError("Report", report_id)
    return report


@router.get("/history")
@require_auth
@require_role("hrbp")
async def get_history(request: Request, limit: int = 20):
    """Get recent weekly report history."""
    return {"reports": [], "total": 0}
