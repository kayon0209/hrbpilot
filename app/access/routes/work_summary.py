"""Work summary routes — 今日工作 aggregation API (spec §5.1, Phase 2)."""

from fastapi import APIRouter, Request, status

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.work_summary.service import collect_work_summaries
from app.scenarios.work_tasks.service import (
    CreateWorkTaskBody,
    UpdateWorkTaskBody,
    advance_work_task,
    create_work_task,
    update_work_task,
)

router = APIRouter(prefix="/api/work-summaries", tags=["work-summaries"])


@router.get("")
@require_auth
async def get_work_summaries(request: Request):
    """Aggregated recent work for the signed-in user: continue / attention / completed_today."""
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    user_role = getattr(request.state, "user_role", "employee")
    summaries = await collect_work_summaries(tenant_id, user_id, user_role)
    return summaries.model_dump()


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
@require_auth
async def create_task(request: Request, body: CreateWorkTaskBody):
    tenant_id = require_tenant_id(request)
    result = await create_work_task(
        tenant_id,
        getattr(request.state, "user_id", "unknown"),
        getattr(request.state, "user_role", "employee"),
        body,
    )
    return result.model_dump()


@router.post("/tasks/{task_id}/subtasks", status_code=status.HTTP_201_CREATED)
@require_auth
async def create_subtask(task_id: str, request: Request, body: CreateWorkTaskBody):
    tenant_id = require_tenant_id(request)
    result = await create_work_task(
        tenant_id,
        getattr(request.state, "user_id", "unknown"),
        getattr(request.state, "user_role", "employee"),
        body,
        parent_task_id=task_id,
    )
    return result.model_dump()


@router.post("/tasks/{task_id}/advance")
@require_auth
async def advance_task(task_id: str, request: Request):
    """Atomically complete one unit of a units-mode task (TASK-02).

    The increment happens in a single guarded UPDATE, so concurrent '完成一个
    单位' clicks cannot double-count or exceed the total.
    """
    tenant_id = require_tenant_id(request)
    result = await advance_work_task(
        tenant_id,
        getattr(request.state, "user_id", "unknown"),
        getattr(request.state, "user_role", "employee"),
        task_id,
    )
    return result.model_dump()


@router.get("/assignable-owners")
@require_auth
async def list_assignable_owners(request: Request):
    """Assignee candidates from the caller's visible scope (never the admin user list)."""
    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.user import User

    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    user_role = getattr(request.state, "user_role", "employee")
    visible_user_ids = await resolve_visible_user_ids(tenant_id, user_id, user_role)
    if not visible_user_ids:
        return {"owners": []}
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            await db.execute(
                select(User.id, User.name)
                .where(User.tenant_id == tenant_id, User.id.in_(visible_user_ids))
                .order_by(User.name.asc())
                .limit(200)
            )
        ).all()
    return {"owners": [{"user_id": row.id, "name": row.name} for row in rows]}


@router.patch("/tasks/{task_id}")
@require_auth
async def update_task(task_id: str, request: Request, body: UpdateWorkTaskBody):
    tenant_id = require_tenant_id(request)
    result = await update_work_task(
        tenant_id,
        getattr(request.state, "user_id", "unknown"),
        getattr(request.state, "user_role", "employee"),
        task_id,
        body,
    )
    return result.model_dump()
