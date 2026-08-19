"""HRBP AI Workbench — Weekly Report API routes.

POST /api/weekly-report/generate → Generate weekly report draft
POST /api/weekly-report/save → Save/publish a report
GET  /api/weekly-report/{report_id} → Get saved report
GET  /api/weekly-report/history → Recent report history
"""

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_role
from app.access.middleware.tenant import require_tenant_id
from app.data.database import get_db
from app.data.models.scenarios import WeeklyReport
from app.scenarios.weekly_report.orchestrator import WeeklyReportOrchestrator
from app.scenarios.weekly_report.schemas import GenerateRequest, PlanItem, Priority, ProgressItem, RiskItem, SaveRequest, Severity, TaskStatus, WeeklyReportResponse
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
            "report_id": "",
            "report": {
                "period": body.period,
                "summary": "未收到任何多源数据，无法生成周报。请先上传面谈纪要或员工声音数据。",
            },
            "is_draft": body.draft_mode,
        }

    result = await orchestrator.generate(
        period=body.period,
        source_data=source_data,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    report_id = await orchestrator._store_report(tenant_id=tenant_id, user_id=user_id, report=result, source_data=source_data)

    return {"report_id": report_id, "report": result, "is_draft": body.draft_mode}


@router.post("/save")
@require_auth
@require_role("hrbp")
async def save_report(body: SaveRequest, request: Request, session: AsyncSession = Depends(get_db)):
    """Save or publish a weekly report."""
    tenant_id = require_tenant_id(request)
    row = (
        (
            await session.execute(
                select(WeeklyReport)
                .where(WeeklyReport.tenant_id == tenant_id, WeeklyReport.id == body.report_id)
            )
        )
        .scalars()
        .first()
    )
    if not row:
        raise NotFoundError("Report", body.report_id)

    if body.action == "publish":
        logger.info("weekly_report_published", report_id=body.report_id)
    else:
        logger.info("weekly_report_saved", report_id=body.report_id)

    return {"report_id": body.report_id, "action": body.action, "status": "saved"}


@router.get("/history")
@require_auth
@require_role("hrbp")
async def get_history(
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
):
    """Get recent weekly report history from database records."""
    tenant_id = require_tenant_id(request)
    rows = (
        (
            await session.execute(
                select(WeeklyReport)
                .where(WeeklyReport.tenant_id == tenant_id)
                .order_by(WeeklyReport.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    reports = []
    for row in rows:
        reports.append(
            {
                "report_id": row.id,
                "period": row.period,
                "summary": row.summary,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )

    return {"reports": reports, "total": len(reports)}


@router.get("/{report_id}")
@require_auth
@require_role("hrbp")
async def get_report(report_id: str, request: Request, session: AsyncSession = Depends(get_db)):
    """Get a saved weekly report by ID."""
    tenant_id = require_tenant_id(request)
    row = (
        (
            await session.execute(
                select(WeeklyReport)
                .where(WeeklyReport.tenant_id == tenant_id, WeeklyReport.id == report_id)
            )
        )
        .scalars()
        .first()
    )
    if not row:
        raise NotFoundError("Report", report_id)

    def _loads(text: str):
        try:
            return json.loads(text or "[]")
        except Exception:
            return []

    progress_items = [ProgressItem.model_validate(item) for item in _loads(row.progress_json)]
    risk_items = [RiskItem.model_validate(item) for item in _loads(row.risks_json)]
    plan_items = [PlanItem.model_validate(item) for item in _loads(row.plan_json)]

    # Historical records do not persist calibrated confidence or evidence flags.
    # Return the factual shape instead of fabricating certainty.
    return WeeklyReportResponse(
        period=row.period,
        summary=row.summary,
        progress=progress_items,
        risks=risk_items,
        plan=plan_items,
        data_sources=_loads(row.data_sources_json),
    )
