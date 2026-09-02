"""Persistent multi-day task commands with object-level authorization."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.access.object_scope import resolve_visible_user_ids
from app.data.database import get_session_factory
from app.data.models.user import User
from app.data.models.work_task import WorkTask
from app.shared.errors import AppError, NotFoundError


class CreateWorkTaskBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    next_action: str = Field("", max_length=1000)
    owner_user_id: str | None = None
    waiting_for: str | None = Field(None, max_length=200)
    due_at: datetime | None = None
    total_units: int | None = Field(None, ge=1, le=10000)
    # FE-04: client-generated idempotency key (e.g. crypto.randomUUID()); a
    # retried create with the same key returns the existing row instead of
    # duplicating it.
    idempotency_key: str | None = Field(None, min_length=8, max_length=64)


class UpdateWorkTaskBody(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    next_action: str | None = Field(None, max_length=1000)
    owner_user_id: str | None = None
    waiting_for: str | None = Field(None, max_length=200)
    due_at: datetime | None = None
    status: str | None = Field(
        None, pattern="^(open|in_progress|waiting|completed|cancelled)$"
    )
    completed_units: int | None = Field(None, ge=0)
    total_units: int | None = Field(None, ge=1, le=10000)


class WorkTaskResponse(BaseModel):
    task_id: str
    parent_task_id: str | None
    title: str
    next_action: str
    owner_user_id: str
    status: str
    waiting_for: str | None
    due_at: str | None
    progress_mode: str
    completed_units: int | None
    total_units: int | None
    completed_at: str | None
    created_at: str | None
    updated_at: str | None


def _response(row: WorkTask) -> WorkTaskResponse:
    return WorkTaskResponse(
        task_id=row.id,
        parent_task_id=row.parent_task_id,
        title=row.title,
        next_action=row.next_action,
        owner_user_id=row.owner_user_id,
        status=row.status,
        waiting_for=row.waiting_for,
        due_at=row.due_at.isoformat() if row.due_at else None,
        progress_mode=row.progress_mode,
        completed_units=row.completed_units if row.progress_mode == "units" else None,
        total_units=row.total_units if row.progress_mode == "units" else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


async def _visible_ids(tenant_id: str, actor_id: str, actor_role: str) -> set[str]:
    return await resolve_visible_user_ids(tenant_id, actor_id, actor_role)


def _object_filter(visible_user_ids: set[str]):
    return or_(
        WorkTask.created_by.in_(visible_user_ids),
        WorkTask.owner_user_id.in_(visible_user_ids),
    )


async def _get_visible_task(db, tenant_id: str, task_id: str, visible_user_ids: set[str]) -> WorkTask:
    row: WorkTask | None = (
        (
            await db.execute(
                select(WorkTask).where(
                    WorkTask.tenant_id == tenant_id,
                    WorkTask.id == task_id,
                    _object_filter(visible_user_ids),
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise NotFoundError("Work task", task_id)
    return row


async def _validate_owner(db, tenant_id: str, owner_id: str, visible_user_ids: set[str]) -> None:
    if owner_id not in visible_user_ids:
        raise NotFoundError("User", owner_id)
    exists = await db.scalar(
        select(User.id).where(User.tenant_id == tenant_id, User.id == owner_id)
    )
    if exists is None:
        raise NotFoundError("User", owner_id)


async def _sync_parent_progress(db, tenant_id: str, parent_task_id: str | None) -> None:
    if not parent_task_id:
        return
    # Serialize concurrent aggregations on the parent row: two subtasks
    # completing in parallel each aggregate the children then write the
    # parent, and without a row lock the later writer overwrites the earlier
    # one with a stale count (lost update — both done, parent stuck at 1/2).
    # FOR UPDATE makes the second transaction wait, then re-read the fresh
    # child state before recomputing.
    parent = (
        (
            await db.execute(
                select(WorkTask)
                .where(
                    WorkTask.tenant_id == tenant_id,
                    WorkTask.id == parent_task_id,
                )
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if parent is None:
        return
    total, completed = (
        await db.execute(
            select(
                func.count(WorkTask.id),
                func.count(WorkTask.id).filter(WorkTask.status == "completed"),
            ).where(
                WorkTask.tenant_id == tenant_id,
                WorkTask.parent_task_id == parent_task_id,
                WorkTask.status != "cancelled",
            )
        )
    ).one()
    if total:
        parent.progress_mode = "units"
        parent.total_units = total
        parent.completed_units = completed


async def create_work_task(
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    body: CreateWorkTaskBody,
    parent_task_id: str | None = None,
) -> WorkTaskResponse:
    visible_user_ids = await _visible_ids(tenant_id, actor_id, actor_role)
    if not visible_user_ids:
        raise AppError("当前角色不能创建工作任务", code="FORBIDDEN", status_code=403)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        # FE-04: an existing row for the same (tenant, idempotency_key) means
        # this create was already processed — return it instead of creating a
        # duplicate.
        if body.idempotency_key:
            existing = await db.scalar(
                select(WorkTask).where(
                    WorkTask.tenant_id == tenant_id,
                    WorkTask.idempotency_key == body.idempotency_key,
                )
            )
            if existing is not None:
                return _response(existing)
        parent = None
        if parent_task_id:
            parent = await _get_visible_task(db, tenant_id, parent_task_id, visible_user_ids)
        owner_id = body.owner_user_id or (parent.owner_user_id if parent else actor_id)
        await _validate_owner(db, tenant_id, owner_id, visible_user_ids)
        row = WorkTask(
            tenant_id=tenant_id,
            created_by=actor_id,
            owner_user_id=owner_id,
            parent_task_id=parent_task_id,
            idempotency_key=body.idempotency_key,
            title=body.title.strip(),
            next_action=body.next_action.strip(),
            waiting_for=(body.waiting_for or "").strip() or None,
            due_at=body.due_at,
            progress_mode="units" if body.total_units else "stage",
            completed_units=0,
            total_units=body.total_units,
        )
        db.add(row)
        try:
            await db.flush()
        except Exception:
            # A concurrent request with the same key won the unique-index race.
            await db.rollback()
            winner = await db.scalar(
                select(WorkTask).where(
                    WorkTask.tenant_id == tenant_id,
                    WorkTask.idempotency_key == body.idempotency_key,
                )
            )
            if winner is not None and body.idempotency_key:
                return _response(winner)
            raise
        await _sync_parent_progress(db, tenant_id, parent_task_id)
        await db.commit()
        await db.refresh(row)
        return _response(row)


async def update_work_task(
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    task_id: str,
    body: UpdateWorkTaskBody,
) -> WorkTaskResponse:
    visible_user_ids = await _visible_ids(tenant_id, actor_id, actor_role)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await _get_visible_task(db, tenant_id, task_id, visible_user_ids)
        fields = body.model_fields_set
        if "owner_user_id" in fields and body.owner_user_id:
            await _validate_owner(db, tenant_id, body.owner_user_id, visible_user_ids)
            row.owner_user_id = body.owner_user_id
        if "title" in fields and body.title is not None:
            row.title = body.title.strip()
        if "next_action" in fields and body.next_action is not None:
            row.next_action = body.next_action.strip()
        if "waiting_for" in fields:
            row.waiting_for = (body.waiting_for or "").strip() or None
        if "due_at" in fields:
            row.due_at = body.due_at
        if "total_units" in fields and body.total_units is not None:
            row.progress_mode = "units"
            row.total_units = body.total_units
            if row.completed_units > body.total_units:
                raise AppError("已完成数量不能超过总量", code="VALIDATION_ERROR", status_code=400)
        if "completed_units" in fields and body.completed_units is not None:
            if row.progress_mode != "units" or row.total_units is None:
                raise AppError("只有真实单位任务可以更新数量进度", code="VALIDATION_ERROR", status_code=400)
            if body.completed_units > row.total_units:
                raise AppError("已完成数量不能超过总量", code="VALIDATION_ERROR", status_code=400)
            row.completed_units = body.completed_units
        if body.status is not None:
            row.status = body.status
            row.completed_at = datetime.now(UTC) if body.status == "completed" else None
            if body.status == "completed" and row.progress_mode == "units" and row.total_units:
                row.completed_units = row.total_units
        await _sync_parent_progress(db, tenant_id, row.parent_task_id)
        await db.commit()
        await db.refresh(row)
        return _response(row)


async def advance_work_task(
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    task_id: str,
) -> WorkTaskResponse:
    """Atomically increment completed_units by exactly one (TASK-02).

    Two rapid 'complete one unit' clicks must not both read N and write N+1 —
    the increment is a single ``UPDATE ... SET completed_units =
    completed_units + 1`` with a guard that refuses to exceed total_units.
    """
    from sqlalchemy import update
    from sqlalchemy.engine import CursorResult

    from app.data.models.work_task import WorkTask

    visible_user_ids = await _visible_ids(tenant_id, actor_id, actor_role)
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = await _get_visible_task(db, tenant_id, task_id, visible_user_ids)
        if row.progress_mode != "units" or row.total_units is None:
            raise AppError("只有真实单位任务可以推进数量进度", code="VALIDATION_ERROR", status_code=400)
        if row.completed_units >= row.total_units:
            raise AppError("任务已完成全部单位", code="VALIDATION_ERROR", status_code=409)
        from typing import Any, cast

        updated = cast(
            CursorResult[Any],
            await db.execute(
                update(WorkTask)
                .where(
                    WorkTask.tenant_id == tenant_id,
                    WorkTask.id == task_id,
                    WorkTask.completed_units < WorkTask.total_units,
                )
                .values(
                    completed_units=WorkTask.completed_units + 1,
                    status="in_progress",
                )
            ),
        )
        if updated.rowcount != 1:
            await db.rollback()
            raise AppError("任务已完成全部单位或已被更新", code="VALIDATION_ERROR", status_code=409)
        await _sync_parent_progress(db, tenant_id, row.parent_task_id)
        await db.commit()
        await db.refresh(row)
        return _response(row)
