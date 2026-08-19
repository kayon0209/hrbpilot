"""HRBP AI Workbench — Weekly Report API routes.

POST /api/weekly-report/generate → Generate weekly report draft
POST /api/weekly-report/save → Save/publish a report
GET  /api/weekly-report/{report_id} → Get saved report
GET  /api/weekly-report/history → Recent report history
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_role
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db
from app.data.models.infra import AsyncTask
from app.scenarios.weekly_report.orchestrator import WeeklyReportOrchestrator
from app.scenarios.weekly_report.schemas import GenerateRequest, SaveRequest, WeeklyReportResponse
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

    source_data = [
        {"type": "interview_digest", "id": sid, "content": f"[访谈结果 {sid} 内容待加载]"}
        for sid in body.source_ids
    ]

    if not source_data:
        return {
            "report_id": str(uuid.uuid4()),
            "report": {
                "period": body.period,
                "summary": "未收到任何多源数据，无法生成周报。请先上传面谈纪要或员工声音数据。",
                "has_evidence": False,
                "confidence": 0.0,
            },
            "is_draft": body.draft_mode,
        }

    result = await orchestrator.generate(
        period=body.period,
        source_data=source_data,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    report_id = str(uuid.uuid4())
    orchestrator.save_report(report_id, result)
    await orchestrator._store_report(tenant_id=tenant_id, user_id=user_id, report=result, source_data=source_data)

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
async def get_history(
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
):
    """Get recent weekly report history from async task records."""
    tenant_id = require_tenant_id(request)
    rows = (
        (
            await session.execute(
                select(AsyncTask)
                .where(
                    AsyncTask.tenant_id == tenant_id,
                    AsyncTask.type == "weekly_report",
                    AsyncTask.status == "completed",
                    AsyncTask.result_json.is_not(None),
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
            result = WeeklyReportResponse.model_validate_json(row.result_json or "")
        except Exception:
            result = None
        reports.append(
            {
                "task_id": row.id,
                "status": row.status,
                "progress": row.progress / 100.0 if row.progress else 0.0,
                "result": result.model_dump() if result else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return {"reports": reports, "total": len(reports)}
