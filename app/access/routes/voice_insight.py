"""HRBP AI Workbench — Voice Insight API routes.

POST /api/voice-insight/analyze → Start async analysis (+ material entry)
GET  /api/voice-insight/progress/{task_id} → Poll task progress
GET  /api/voice-insight/report/{task_id} → Get completed report
GET  /api/voice-insight/history → Recent analysis history (legacy contract, kept for compat)
GET  /api/voice-insight/entries → Material list: summary rows, cursor pagination, search
GET  /api/voice-insight/entries/{entry_id} → Full entry detail incl. complete report
"""

import base64
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_db, get_session_factory
from app.data.models.infra import AsyncTask
from app.data.models.material import Employee, VoiceEntry
from app.scenarios.voice_insight.orchestrator import VoiceInsightOrchestrator
from app.scenarios.voice_insight.schemas import InsightReportResponse, Severity
from app.shared.errors import NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/voice-insight", tags=["voice-insight"])

orchestrator = VoiceInsightOrchestrator()

_SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}


class AnalyzeRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list)
    content: str = Field("", description="直接提供文本内容（无需文档ID时）")
    employee_name: str = Field("", description="反馈员工姓名；匿名调研留空")
    channel: str = Field("survey", description="来源渠道：survey | inbox | townhall | interview")


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

    await _persist_voice_entry(
        tenant_id,
        user_id,
        content=body.content if body.content else " ".join(f"[文档 {d}]" for d in body.document_ids),
        task_id=task_id,
        employee_name=body.employee_name,
        channel=body.channel,
    )

    return {"task_id": task_id, "status": "pending"}


async def _get_or_create_employee(
    tenant_id: str, user_id: str, name: str
) -> Employee | None:
    cleaned = name.strip()
    if not cleaned:
        return None
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            await db.execute(
                select(Employee).where(Employee.tenant_id == tenant_id, Employee.name == cleaned)
            )
        ).scalar_one_or_none()
        if row is None:
            row = Employee(tenant_id=tenant_id, name=cleaned, created_by=user_id)
            db.add(row)
            await db.commit()
        return row


async def _persist_voice_entry(
    tenant_id: str,
    user_id: str,
    *,
    content: str,
    task_id: str,
    employee_name: str,
    channel: str,
) -> None:
    """Best-effort raw-corpus persistence — must not break analysis start."""
    try:
        employee = await _get_or_create_employee(tenant_id, user_id, employee_name)
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            db.add(
                VoiceEntry(
                    tenant_id=tenant_id,
                    employee_id=employee.id if employee else None,
                    employee_name=employee.name if employee else None,
                    channel=(channel.strip() or "survey"),
                    raw_text=content,
                    raw_text_length=len(content),
                    digest_task_id=task_id,
                    status="analyzing",
                    created_by=user_id,
                )
            )
            await db.commit()
    except Exception as exc:
        logger.warning("voice_entry_persist_failed", task_id=task_id, error=str(exc))


@router.get("/progress/{task_id}")
@require_auth
@require_capability("voice_insight")
async def get_progress(task_id: str, request: Request):
    """Poll async task progress."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
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
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
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
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
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
        except Exception as exc:
            # Same rule as the interview digest list: degrade this row, not the
            # response — but leave a trace so the empty card is explainable.
            logger.warning("voice_insight_result_unreadable", task_id=row.id, error=str(exc))
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


# --- material library endpoints (design: docs/data-scale-design-2026-09-09.md) ---


def _encode_cursor(created_at: datetime, entry_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{entry_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_iso, entry_id = raw.rsplit("|", 1)
        return datetime.fromisoformat(created_iso), entry_id
    except Exception as exc:
        raise ValidationError("无效的分页游标，请清除筛选后重试") from exc


def _summarize_entry(entry: VoiceEntry, task: AsyncTask | None) -> dict:
    summary = ""
    top_severity = None
    cluster_count = 0
    confidence = None
    if task is not None and task.result_json:
        try:
            parsed = InsightReportResponse.model_validate_json(task.result_json)
            summary = parsed.summary
            confidence = parsed.confidence
            cluster_count = len(parsed.clusters)
            severities = [s.severity for s in parsed.risk_signals]
            if severities:
                top_severity = max(severities, key=lambda s: _SEVERITY_ORDER[s]).value
        except Exception as exc:
            logger.warning("voice_entry_summary_unreadable", entry_id=entry.id, error=str(exc))
    return {
        "entry_id": entry.id,
        "employee_name": entry.employee_name,
        "channel": entry.channel,
        "status": task.status if task is not None else entry.status,
        "top_severity": top_severity,
        "cluster_count": cluster_count,
        "summary": summary,
        "confidence": confidence,
        "raw_text_length": entry.raw_text_length,
        "has_result": bool(task is not None and task.result_json),
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "completed_at": task.completed_at.isoformat() if task is not None and task.completed_at else None,
    }


@router.get("/entries")
@require_auth
@require_capability("voice_insight")
async def list_entries(
    request: Request,
    q: str = "",
    channel: str = "",
    status: str = "",
    limit: int = 20,
    cursor: str = "",
    session: AsyncSession = Depends(get_db),
):
    """Material list — summary rows with keyset pagination, search and filters.

    Severity lives inside the result JSON (no materialized summary table yet),
    so it is not a SQL filter — filtering on it would break keyset pagination.
    """
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)
    limit = max(1, min(limit, 100))

    stmt = (
        select(VoiceEntry, AsyncTask)
        .outerjoin(
            AsyncTask,
            and_(
                AsyncTask.tenant_id == VoiceEntry.tenant_id,
                AsyncTask.id == VoiceEntry.digest_task_id,
            ),
        )
        .where(
            VoiceEntry.tenant_id == tenant_id,
            VoiceEntry.created_by.in_(visible_user_ids),
        )
        .order_by(VoiceEntry.created_at.desc(), VoiceEntry.id.desc())
    )
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                VoiceEntry.employee_name.ilike(like),
                VoiceEntry.channel.ilike(like),
                VoiceEntry.raw_text.ilike(like),
            )
        )
    if channel.strip():
        stmt = stmt.where(VoiceEntry.channel == channel.strip())
    if status.strip():
        stage = status.strip()
        if stage == "completed":
            stmt = stmt.where(AsyncTask.status == "completed")
        elif stage == "failed":
            stmt = stmt.where(AsyncTask.status == "failed")
        else:  # analyzing — pending or running worker stage
            stmt = stmt.where(AsyncTask.status.in_(("pending", "running")))
    if cursor:
        last_dt, last_id = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                VoiceEntry.created_at < last_dt,
                and_(VoiceEntry.created_at == last_dt, VoiceEntry.id < last_id),
            )
        )

    rows = ((await session.execute(stmt.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [_summarize_entry(entry, task) for entry, task in rows]
    next_cursor = None
    if has_more and items:
        next_cursor = _encode_cursor(rows[-1][0].created_at, rows[-1][0].id)
    return {"entries": items, "total": len(items), "next_cursor": next_cursor}


@router.get("/entries/{entry_id}")
@require_auth
@require_capability("voice_insight")
async def get_entry_detail(
    entry_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Full material detail — complete insight report, loaded on demand."""
    tenant_id = require_tenant_id(request)
    visible_user_ids = await resolve_visible_user_ids(tenant_id, request.state.user_id, request.state.user_role)

    row = (
        await session.execute(
            select(VoiceEntry, AsyncTask)
            .outerjoin(
                AsyncTask,
                and_(
                    AsyncTask.tenant_id == VoiceEntry.tenant_id,
                    AsyncTask.id == VoiceEntry.digest_task_id,
                ),
            )
            .where(
                VoiceEntry.tenant_id == tenant_id,
                VoiceEntry.id == entry_id,
                VoiceEntry.created_by.in_(visible_user_ids),
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("VoiceEntry", entry_id)
    entry, task = row

    result = None
    if task is not None and task.result_json:
        try:
            result = InsightReportResponse.model_validate_json(task.result_json).model_dump()
        except Exception as exc:
            logger.warning("voice_entry_result_unreadable", entry_id=entry.id, error=str(exc))
            result = None

    return {
        "entry": _summarize_entry(entry, task),
        "raw_text": entry.raw_text,
        "result": result,
    }
