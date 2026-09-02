"""HRBP AI Workbench — Weekly Report API routes.

POST /api/weekly-report/generate → Generate weekly report draft
POST /api/weekly-report/save → Save/publish a report
GET  /api/weekly-report/sources → Completed scenario results usable as sources
GET  /api/weekly-report/history → Recent report history
"""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_db
from app.data.models.infra import AsyncTask
from app.data.models.scenarios import WeeklyReport
from app.scenarios.interview_digest.schemas import InterviewDigestResponse
from app.scenarios.voice_insight.schemas import InsightReportResponse
from app.scenarios.weekly_report.orchestrator import WeeklyReportOrchestrator
from app.scenarios.weekly_report.schemas import GenerateRequest, SaveRequest
from app.shared.audit import append_security_audit_event
from app.shared.errors import DatabaseError, NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/weekly-report", tags=["weekly-report"])

orchestrator = WeeklyReportOrchestrator()

SOURCE_TYPES = ("interview_digest", "voice_insight")


def _source_text(row: AsyncTask) -> str:
    """Render a completed scenario result as plain text for report generation."""
    if not row.result_json:
        return ""
    if row.type == "interview_digest":
        try:
            digest = InterviewDigestResponse.model_validate_json(row.result_json)
        except Exception:
            return ""
        lines = [f"面谈纪要摘要：{digest.summary}", f"风险等级：{digest.risk_level.value}"]
        if digest.employee_demands:
            lines.append(
                "员工诉求："
                + "；".join(f"{d.demand}（{d.category}，紧急度{d.urgency.value}）" for d in digest.employee_demands)
            )
        if digest.risk_signals:
            lines.append("风险信号：" + "；".join(digest.risk_signals))
        if digest.action_items:
            lines.append(
                "后续行动：" + "；".join(f"{a.action}（负责人：{a.owner or '待定'}）" for a in digest.action_items)
            )
        return "\n".join(line for line in lines if line)
    try:
        insight = InsightReportResponse.model_validate_json(row.result_json)
    except Exception:
        return ""
    lines = [f"员工声音摘要：{insight.summary}"]
    if insight.clusters:
        lines.append("主题聚类：" + "；".join(f"{c.label}（{c.demand_count} 条）" for c in insight.clusters))
    if insight.risk_signals:
        lines.append("风险信号：" + "；".join(signal.signal for signal in insight.risk_signals))
    if insight.trends:
        lines.append("趋势观察：" + "；".join(f"{t.topic}（{t.direction.value}）" for t in insight.trends))
    return "\n".join(line for line in lines if line)


@router.get("/sources")
@require_auth
@require_capability("weekly_report")
async def list_sources(
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
):
    """List completed interview/voice analyses that can feed a weekly report."""
    tenant_id = require_tenant_id(request)
    actor_id = request.state.user_id
    actor_role = request.state.user_role
    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    rows = (
        (
            await session.execute(
                select(AsyncTask)
                .where(
                    AsyncTask.tenant_id == tenant_id,
                    AsyncTask.type.in_(SOURCE_TYPES),
                    AsyncTask.status == "completed",
                    AsyncTask.result_json.is_not(None),
                    AsyncTask.created_by.in_(visible_user_ids),
                )
                .order_by(AsyncTask.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    sources = []
    for row in rows:
        text = _source_text(row)
        if not text:
            continue
        sources.append(
            {
                "id": row.id,
                "type": row.type,
                "kind": "面谈纪要" if row.type == "interview_digest" else "员工声音",
                "label": text.split("\n", 1)[0][:48],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"sources": sources, "total": len(sources)}


@router.post("/generate")
@require_auth
@require_capability("weekly_report")
async def generate_report(
    body: GenerateRequest,
    request: Request,
):
    """Generate a weekly report draft from selected multi-source data."""
    tenant_id = require_tenant_id(request)
    user_id = request.state.user_id
    visible_user_ids = await resolve_visible_user_ids(tenant_id, user_id, request.state.user_role)

    if not body.source_ids:
        raise ValidationError("未选择任何数据来源。请先完成面谈纪要或员工声音分析，再回到本页生成周报。")

    async for session in get_db(request):
        rows = (
            (
                await session.execute(
                    select(AsyncTask).where(
                        AsyncTask.tenant_id == tenant_id,
                        AsyncTask.id.in_(body.source_ids),
                        AsyncTask.status == "completed",
                        AsyncTask.created_by.in_(visible_user_ids),
                    )
                )
            )
            .scalars()
            .all()
        )

    source_data = []
    for row in rows:
        content = _source_text(row)
        if content:
            source_data.append({"type": row.type, "id": row.id, "content": content})

    if not source_data:
        raise ValidationError("所选数据来源不可用（可能已被删除或尚未完成），请刷新后重新选择。")

    result = await orchestrator.generate(
        period=body.period,
        source_data=source_data,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    report_id = await orchestrator._store_report(
        tenant_id=tenant_id, user_id=user_id, report=result, source_data=source_data
    )
    # _store_report swallows persistence failures and returns "" — returning a
    # 200 with an empty report_id would fake success (users lose the draft).
    if not report_id:
        raise DatabaseError("周报未能保存，请稍后重试")

    return {"report_id": report_id, "report": result.model_dump(), "is_draft": body.draft_mode}


@router.post("/save")
@require_auth
@require_capability("weekly_report")
async def save_report(body: SaveRequest, request: Request):
    """Save or publish a weekly report, persisting any user edits."""
    tenant_id = require_tenant_id(request)
    actor_id = request.state.user_id
    if not body.report_id:
        raise ValidationError("缺少周报标识，无法保存。请重新生成后再保存。")

    summary = ""
    async for session in get_db(request):
        row = (
            (
                await session.execute(
                    select(WeeklyReport).where(
                        WeeklyReport.tenant_id == tenant_id,
                        WeeklyReport.id == body.report_id,
                        WeeklyReport.created_by == actor_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if not row:
            raise NotFoundError("Report", body.report_id)

        edits = body.edits or {}
        if isinstance(edits.get("summary"), str) and edits["summary"].strip():
            row.summary = edits["summary"].strip()
        for key, column in (("progress", "progress_json"), ("risks", "risks_json"), ("plan", "plan_json")):
            if isinstance(edits.get(key), list):
                setattr(row, column, json.dumps(edits[key], ensure_ascii=False))
        if body.action == "publish":
            row.published_at = datetime.now(UTC)
        summary = row.summary
        await append_security_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="weekly_report.published" if body.action == "publish" else "weekly_report.saved",
            object_type="weekly_report",
            object_id=row.id,
            details={"action": body.action},
        )

    if body.action == "publish":
        logger.info("weekly_report_published", report_id=body.report_id)
    else:
        logger.info("weekly_report_saved", report_id=body.report_id)

    return {"report_id": body.report_id, "action": body.action, "status": "saved", "summary": summary}


@router.get("/history")
@require_auth
@require_capability("weekly_report")
async def get_history(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """Get recent weekly report history from database records."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(
        tenant_id,
        request.state.user_id,
        request.state.user_role,
    )
    rows = (
        (
            await session.execute(
                select(WeeklyReport)
                .where(
                    WeeklyReport.tenant_id == tenant_id,
                    WeeklyReport.created_by.in_(visible_user_ids),
                )
                .order_by(WeeklyReport.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    reports = []
    for row in rows:
        def _parse_list(raw: str | None) -> list:
            if not raw:
                return []
            try:
                value = json.loads(raw)
                return value if isinstance(value, list) else []
            except Exception:
                return []

        reports.append(
            {
                "report_id": row.id,
                "period": row.period,
                "summary": row.summary,
                "progress": _parse_list(row.progress_json),
                "risks": _parse_list(row.risks_json),
                "plan": _parse_list(row.plan_json),
                "data_sources": _parse_list(row.data_sources_json),
                "published": row.published_at is not None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )

    return {"reports": reports, "total": len(reports)}
