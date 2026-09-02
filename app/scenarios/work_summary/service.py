"""Work summary aggregation — the read-only backbone of 今日工作 (spec §5.1).

Aggregates recent work objects across scenarios into three buckets:
  - continue:      the most recent resumable item (drafts, unfinished tasks)
  - attention:     needs confirmation / materials / retry, or is due soon
  - completed_today: finished today, with real outputs to link to

Phase 2 scope: a read-only aggregate over existing tables. No giant unified
WorkItem table yet — each source keeps its own identity (work_id + work_type)
and a safe resume_target back into its original context.

Permission filtering happens before aggregation: every source query is
tenant-scoped and only contributes items the requesting user can act on.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from app.shared.logger import get_logger

logger = get_logger(__name__)


class WorkSummary(BaseModel):
    """One aggregated work object (spec §5.1 field table)."""

    work_id: str
    work_type: str  # policy_qa | interview_digest | voice_insight | weekly_report
    title: str
    business_status: str  # 草稿 | 等待材料 | 待确认 | 处理中 | 已完成 | 失败
    next_action: str
    resume_target: str
    updated_at: str | None = None
    due_at: str | None = None
    owner: str | None = None
    waiting_for: str | None = None
    progress_mode: str = "none"  # none | stage | units (spec §9.1)
    completed_units: int | None = None
    total_units: int | None = None


class WorkSummaries(BaseModel):
    continue_work: WorkSummary | None = None
    attention: list[WorkSummary] = []
    completed_today: list[WorkSummary] = []


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _collect_async_tasks(tenant_id: str, visible_user_ids: set[str], summaries: list[WorkSummary]) -> None:
    """Interview / voice async tasks (spec §5.4): stage words, no fake percentages."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.infra import AsyncTask

    if not visible_user_ids:
        return
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(AsyncTask)
                    .where(
                        AsyncTask.tenant_id == tenant_id,
                        AsyncTask.type.in_(("interview_digest", "voice_insight")),
                        AsyncTask.created_by.in_(visible_user_ids),
                    )
                    .order_by(AsyncTask.updated_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )

    for row in rows:
        work_type = "面谈纪要" if row.type == "interview_digest" else "员工声音"
        resume = "/interview" if row.type == "interview_digest" else "/voice"
        if row.status == "completed":
            summaries.append(
                WorkSummary(
                    work_id=row.id,
                    work_type=row.type,
                    title=f"{work_type}分析已完成",
                    business_status="待确认",
                    next_action="打开结果确认草稿，建立跟进",
                    resume_target=resume,
                    updated_at=_iso(row.updated_at),
                    waiting_for="你确认草稿",
                )
            )
        elif row.status == "failed":
            summaries.append(
                WorkSummary(
                    work_id=row.id,
                    work_type=row.type,
                    title=f"{work_type}分析失败",
                    business_status="失败",
                    next_action=row.error_message or "重新发起分析"[:80],
                    resume_target=resume,
                    updated_at=_iso(row.updated_at),
                )
            )
        else:
            summaries.append(
                WorkSummary(
                    work_id=row.id,
                    work_type=row.type,
                    title=f"{work_type}正在处理",
                    business_status="处理中",
                    next_action="等待分析完成后确认草稿",
                    resume_target=resume,
                    updated_at=_iso(row.updated_at),
                    waiting_for="系统分析",
                )
            )


async def _collect_weekly_reports(tenant_id: str, visible_user_ids: set[str], summaries: list[WorkSummary]) -> None:
    """Weekly reports: unpublished = 草稿 awaiting confirmation; published = done (spec §7.6)."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.scenarios import WeeklyReport

    if not visible_user_ids:
        return
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(WeeklyReport)
                    .where(
                        WeeklyReport.tenant_id == tenant_id,
                        WeeklyReport.created_by.in_(visible_user_ids),
                    )
                    .order_by(WeeklyReport.updated_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )

    for row in rows:
        if row.published_at is None:
            summaries.append(
                WorkSummary(
                    work_id=row.id,
                    work_type="weekly_report",
                    title=f"周报 {row.period} 草稿待确认",
                    business_status="待确认",
                    next_action="检查进展、风险与计划，确认后发布",
                    resume_target="/weekly",
                    updated_at=_iso(row.updated_at),
                    waiting_for="你确认发布",
                )
            )
        else:
            summaries.append(
                WorkSummary(
                    work_id=row.id,
                    work_type="weekly_report",
                    title=f"周报 {row.period} 已发布",
                    business_status="已完成",
                    next_action="回看本期内容，或开始准备下一期",
                    resume_target="/weekly",
                    updated_at=_iso(row.published_at or row.updated_at),
                )
            )


async def _collect_policy_sessions(tenant_id: str, user_id: str, summaries: list[WorkSummary]) -> None:
    """Recent policy QA sessions: the resumable thread for the asking user (spec §7.3)."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.chat import ChatMessage, ChatSession

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        sessions = (
            (
                await db.execute(
                    select(ChatSession)
                    .where(
                        ChatSession.tenant_id == tenant_id,
                        ChatSession.user_id == user_id,
                        ChatSession.scenario_id == "policy_qa",
                    )
                    .order_by(ChatSession.updated_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        for s in sessions:
            first_user = (
                await db.execute(
                    select(ChatMessage.content)
                    .where(ChatMessage.session_id == s.id, ChatMessage.role == "user")
                    .order_by(ChatMessage.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            summaries.append(
                WorkSummary(
                    work_id=s.id,
                    work_type="policy_qa",
                    title=f"制度问答：{(first_user or '新会话')[:40]}",
                    business_status="可继续",
                    next_action="带着原上下文继续追问",
                    resume_target=f"/policy?session={s.id}",
                    updated_at=_iso(s.updated_at),
                )
            )


async def _collect_employee_requests(
    tenant_id: str, actor_id: str, actor_role: str, summaries: list[WorkSummary]
) -> None:
    """Open employee requests this actor must handle (spec §5.4, audit P1-5).

    hrbp sees requests explicitly assigned to them; hr_manager sees requests
    from their authorised org scope; other roles see nothing (fail closed).
    Employee-created requests are a service commitment — when they exist they
    belong in 需要你处理, not buried in a separate page.
    """
    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User

    if actor_role not in ("hrbp", "hr_manager"):
        return
    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        filters = [EmployeeRequest.tenant_id == tenant_id, EmployeeRequest.status != "resolved"]
        if actor_role == "hrbp":
            filters.append(EmployeeRequest.hr_owner_id == actor_id)
        else:
            if not visible_user_ids:
                return
            filters.append(EmployeeRequest.created_by.in_(visible_user_ids))
        rows = (
            await db.execute(
                select(EmployeeRequest, User.name)
                .outerjoin(User, User.id == EmployeeRequest.hr_owner_id)
                .where(*filters)
                .order_by(EmployeeRequest.updated_at.desc())
                .limit(20)
            )
        ).all()

    status_labels = {"submitted": "待处理", "needs_materials": "待补充", "in_progress": "处理中"}
    for row, owner_name in rows:
        summaries.append(
            WorkSummary(
                work_id=row.id,
                work_type="employee_request",
                title=f"员工请求：{row.title[:40]}",
                business_status=status_labels.get(row.status, "处理中"),
                next_action="查看员工诉求并更新下一步说明",
                resume_target="/hr-requests",
                updated_at=_iso(row.updated_at),
                owner=owner_name or None,
                waiting_for="员工等待处理" if row.status == "submitted" else None,
            )
        )


async def _collect_knowledge_feedback(
    tenant_id: str, actor_id: str, actor_role: str, summaries: list[WorkSummary]
) -> None:
    """Open knowledge-feedback candidates awaiting a manager decision (spec §7.7)."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.scenarios import KnowledgeFeedbackCandidate
    from app.scenarios.knowledge_feedback.service import _candidate_scope_filter, _visible_scope

    if actor_role != "hr_manager":
        return
    visible_user_ids, visible_org_unit_ids = await _visible_scope(tenant_id, actor_id, actor_role)
    if not visible_user_ids:
        return
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(KnowledgeFeedbackCandidate)
                    .where(
                        KnowledgeFeedbackCandidate.tenant_id == tenant_id,
                        KnowledgeFeedbackCandidate.status == "open",
                        _candidate_scope_filter(
                            KnowledgeFeedbackCandidate,
                            visible_user_ids,
                            visible_org_unit_ids,
                        ),
                    )
                    .order_by(KnowledgeFeedbackCandidate.updated_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )

    for row in rows:
        summaries.append(
            WorkSummary(
                work_id=row.id,
                work_type="knowledge_feedback",
                title=f"知识反馈待判断：{row.question[:40]}",
                business_status="待确认",
                next_action="确认缺口、指派处理或驳回该候选",
                resume_target="/knowledge",
                updated_at=_iso(row.updated_at),
                waiting_for="你的判断",
            )
        )


async def _collect_work_tasks(tenant_id: str, visible_user_ids: set[str], summaries: list[WorkSummary]) -> None:
    """User-managed multi-day tasks, including independently completable subtasks."""
    from sqlalchemy import or_, select

    from app.data.database import get_session_factory
    from app.data.models.user import User
    from app.data.models.work_task import WorkTask

    if not visible_user_ids:
        return
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            await db.execute(
                select(WorkTask, User.name)
                .join(User, User.id == WorkTask.owner_user_id)
                .where(
                    WorkTask.tenant_id == tenant_id,
                    WorkTask.status != "cancelled",
                    or_(
                        WorkTask.created_by.in_(visible_user_ids),
                        WorkTask.owner_user_id.in_(visible_user_ids),
                    ),
                )
                .order_by(WorkTask.updated_at.desc())
                .limit(100)
            )
        ).all()

    status_labels = {
        "open": "处理中",
        "in_progress": "处理中",
        "waiting": "待确认",
        "completed": "已完成",
    }
    for row, owner_name in rows:
        summaries.append(
            WorkSummary(
                work_id=row.id,
                work_type="work_task",
                title=row.title,
                business_status=status_labels.get(row.status, "处理中"),
                next_action=row.next_action or ("回看完成结果" if row.status == "completed" else "继续处理"),
                resume_target="/tasks",
                updated_at=_iso(row.completed_at or row.updated_at),
                due_at=_iso(row.due_at),
                owner=owner_name,
                waiting_for=row.waiting_for,
                progress_mode=row.progress_mode,
                completed_units=row.completed_units if row.progress_mode == "units" else None,
                total_units=row.total_units if row.progress_mode == "units" else None,
            )
        )


async def collect_work_summaries(tenant_id: str, user_id: str, user_role: str) -> WorkSummaries:
    """Aggregate the user's work across scenario tables, newest first."""
    from app.access.object_scope import resolve_visible_user_ids

    summaries: list[WorkSummary] = []
    visible_user_ids = await resolve_visible_user_ids(tenant_id, user_id, user_role)

    await _collect_async_tasks(tenant_id, visible_user_ids, summaries)
    await _collect_weekly_reports(tenant_id, visible_user_ids, summaries)
    await _collect_policy_sessions(tenant_id, user_id, summaries)
    await _collect_employee_requests(tenant_id, user_id, user_role, summaries)
    await _collect_knowledge_feedback(tenant_id, user_id, user_role, summaries)
    await _collect_work_tasks(tenant_id, visible_user_ids, summaries)

    summaries.sort(key=lambda s: s.updated_at or "", reverse=True)

    # Deduplicate per work_type: the newest item carries the current state —
    # listing five "面谈纪要分析已完成" rows adds noise, not information.
    seen_types: set[str] = set()
    deduped: list[WorkSummary] = []
    for s in summaries:
        if s.work_type != "work_task" and s.work_type in seen_types:
            continue
        if s.work_type != "work_task":
            seen_types.add(s.work_type)
        deduped.append(s)
    summaries = deduped

    today = datetime.now(UTC).date().isoformat()
    completed_today = [
        s for s in summaries if s.updated_at and s.updated_at[:10] == today and s.business_status == "已完成"
    ][:20]
    # 继续上次工作: 最近一个可继续且属于当前用户的事项 (方案 §7.2 唯一主按钮).
    # Service queues (employee requests, knowledge feedback) are NOT resumable
    # drafts — they are new judgements waiting in their own action centers, so
    # they only ever appear under 需要你处理.
    service_types = {"employee_request", "knowledge_feedback"}
    resumable = [
        s
        for s in summaries
        if s.business_status in ("可继续", "待确认", "处理中", "失败") and s.work_type not in service_types
    ]
    continue_work = resumable[0] if resumable else None
    # Mutually exclusive buckets (audit P1-3): the continue card IS the newest
    # actionable item — repeating it under 需要你处理 duplicated rows and
    # produced duplicate React keys on /tasks. Exclude it from attention.
    attention = [
        s
        for s in summaries
        if s.business_status in ("失败", "待确认", "处理中")
        and (continue_work is None or s.work_id != continue_work.work_id)
    ][:50]

    return WorkSummaries(
        continue_work=continue_work,
        attention=attention,
        completed_today=completed_today,
    )
