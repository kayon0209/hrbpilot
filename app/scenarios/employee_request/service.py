"""Employee request service (Phase 4) — the employee-visible service contract.

Two projections, one table (spec §5.4):
  - employee view: desensitized business status + next step. hr_note and
    hr_case_id never leave this module toward an employee.
  - HR triage view (hrbp/hr_manager with request capability): full row.

Employees can only see and act on their OWN requests (object-level ACL);
one user can never enumerate another's requests even inside the same tenant.
"""

from __future__ import annotations

from datetime import UTC

from pydantic import BaseModel, Field

from app.shared.errors import NotFoundError, ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

EMPLOYEE_STATUS_LABELS = {
    "submitted": "已提交",
    "needs_materials": "待补充",
    "in_progress": "处理中",
    "resolved": "已解决",
}

REQUEST_TYPE_LABELS = {
    "policy_check": "制度核对",
    "certificate": "证明开具",
    "process_help": "流程协助",
    "other": "其他事项",
}


class CreateRequestBody(BaseModel):
    request_type: str = Field(..., pattern="^(policy_check|certificate|process_help|other)$")
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=4000)


class EmployeeRequestView(BaseModel):
    """The desensitized projection returned to the requesting employee."""

    request_id: str
    request_type: str
    request_type_label: str
    title: str
    status: str
    status_label: str
    next_step: str
    needs_materials: str | None = None
    updated_at: str | None = None
    created_at: str | None = None


class HrTriageBody(BaseModel):
    status: str = Field(..., pattern="^(needs_materials|in_progress|resolved)$")
    next_step_for_employee: str | None = Field(None, max_length=500)
    needs_materials: str | None = Field(None, max_length=1000)
    hr_note: str | None = Field(None, max_length=2000)
    hr_owner_id: str | None = Field(None, max_length=36)


def _employee_view(row) -> EmployeeRequestView:
    status = row.status or "submitted"
    return EmployeeRequestView(
        request_id=row.id,
        request_type=row.request_type,
        request_type_label=REQUEST_TYPE_LABELS.get(row.request_type, row.request_type),
        title=row.title,
        status=status,
        status_label=EMPLOYEE_STATUS_LABELS.get(status, status),
        next_step=row.next_step_for_employee or "HR 会尽快处理；如需补充材料会在这里说明。",
        needs_materials=row.needs_materials,
        updated_at=row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
        created_at=row.created_at.isoformat() if getattr(row, "created_at", None) else None,
    )


async def create_request(tenant_id: str, user_id: str, body: CreateRequestBody) -> EmployeeRequestView:
    """Employee files a request. No auto-triage, no auto-resolution."""
    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = EmployeeRequest(
            tenant_id=tenant_id,
            created_by=user_id,
            request_type=body.request_type,
            title=body.title,
            description=body.description,
            status="submitted",
            next_step_for_employee="已提交，HR 会尽快查看；需要补充材料时会在这里说明。",
        )
        db.add(row)
        await db.commit()
        view = _employee_view(row)
    logger.info("employee_request_created", tenant_id=tenant_id, request_id=row.id, request_type=body.request_type)
    return view


async def list_my_requests(tenant_id: str, user_id: str) -> list[EmployeeRequestView]:
    """Only the caller's OWN requests — object-level ACL, newest first."""
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(EmployeeRequest)
                    .where(EmployeeRequest.tenant_id == tenant_id, EmployeeRequest.created_by == user_id)
                    .order_by(EmployeeRequest.updated_at.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
    return [_employee_view(row) for row in rows]


async def get_my_request(tenant_id: str, user_id: str, request_id: str) -> EmployeeRequestView:
    view = await _load_owned(tenant_id, user_id, request_id)
    return view


async def hr_triage(
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    request_id: str,
    body: HrTriageBody,
) -> dict:
    """HR updates the business status and the employee-facing next step.

    The internal note is stored but NEVER returned to the employee; the
    employee only sees the mapped status and next step (spec §7.9).
    """
    from datetime import datetime as dt

    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest
    from app.data.models.user import User
    from app.shared.audit import append_security_audit_event

    if body.status == "needs_materials" and not body.needs_materials:
        raise ValidationError("请求补充材料时需要说明缺什么")
    if body.status in ("in_progress", "resolved") and not body.next_step_for_employee:
        raise ValidationError("需要给员工一个明确的下一步说明")

    factory = get_session_factory()
    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        filters = [EmployeeRequest.tenant_id == tenant_id, EmployeeRequest.id == request_id]
        if actor_role == "hrbp":
            filters.append(EmployeeRequest.hr_owner_id == actor_id)
        elif actor_role == "hr_manager":
            filters.append(EmployeeRequest.created_by.in_(visible_user_ids))
        else:
            raise NotFoundError("Request", request_id)
        row = (
            (
                await db.execute(
                    select(EmployeeRequest).where(*filters)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError("Request", request_id)
        row.status = body.status
        row.next_step_for_employee = (body.next_step_for_employee or "")[:500] or None
        row.needs_materials = (body.needs_materials or "")[:1000] or None
        row.hr_note = (body.hr_note or "")[:2000] or None
        if body.hr_owner_id:
            if actor_role != "hr_manager":
                raise ValidationError("只有授权范围内的 HR 经理可以分配负责人")
            owner = (
                (
                    await db.execute(
                        select(User).where(
                            User.tenant_id == tenant_id,
                            User.id == body.hr_owner_id,
                            User.role == "hrbp",
                        )
                    )
                )
                .scalars()
                .first()
            )
            if owner is None or owner.id not in visible_user_ids:
                raise ValidationError("负责人不在你的授权组织范围内")
            row.hr_owner_id = owner.id
        if body.status == "resolved":
            row.resolved_at = dt.now(UTC)
        row.updated_at = dt.now(UTC)
        await append_security_audit_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="employee_request.triaged",
            object_type="employee_request",
            object_id=request_id,
            details={"status": body.status, "owner_id": row.hr_owner_id},
        )
        await db.commit()
    logger.info("employee_request_triaged", tenant_id=tenant_id, request_id=request_id, status=body.status)
    # HR sees the desensitized employee view plus the internal note (their own).
    view = _employee_view(row)
    return {"request": view.model_dump(), "hr_note": body.hr_note}


async def hr_list_open(tenant_id: str, actor_id: str, actor_role: str) -> list[dict]:
    """Return only explicitly owned or manager-scoped open requests."""
    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest

    visible_user_ids = await resolve_visible_user_ids(tenant_id, actor_id, actor_role)
    if actor_role == "hrbp":
        scope_filter = EmployeeRequest.hr_owner_id == actor_id
    elif actor_role == "hr_manager":
        if not visible_user_ids:
            return []
        scope_filter = EmployeeRequest.created_by.in_(visible_user_ids)
    else:
        return []

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(EmployeeRequest)
                    .where(
                        EmployeeRequest.tenant_id == tenant_id,
                        EmployeeRequest.status != "resolved",
                        scope_filter,
                    )
                    .order_by(EmployeeRequest.updated_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            **_employee_view(row).model_dump(),
            "description": row.description,
            "hr_note": row.hr_note,
            "hr_case_id": row.hr_case_id,
            "hr_owner_id": row.hr_owner_id,
        }
        for row in rows
    ]


async def hr_list_assignees(tenant_id: str, manager_id: str, manager_role: str) -> list[dict]:
    """HRBPs inside the manager's authorised org scope — the assign pool (audit P1-7).

    A manager needs an in-product way to hand an employee request to an HRBP;
    until now ``hr_owner_id`` was only writable via SQL, so the hrbp queue was
    structurally always empty.
    """
    from sqlalchemy import select

    from app.access.object_scope import resolve_visible_user_ids
    from app.data.database import get_session_factory
    from app.data.models.user import User

    if manager_role != "hr_manager":
        return []
    visible_user_ids = await resolve_visible_user_ids(tenant_id, manager_id, manager_role)
    if not visible_user_ids:
        return []
    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        rows = (
            (
                await db.execute(
                    select(User.id, User.name, User.email).where(
                        User.tenant_id == tenant_id,
                        User.id.in_(visible_user_ids),
                        User.role == "hrbp",
                    )
                )
            )
            .all()
        )
    return [{"user_id": r[0], "name": r[1], "email": r[2]} for r in rows]


async def _load_owned(tenant_id: str, user_id: str, request_id: str) -> EmployeeRequestView:
    from sqlalchemy import select

    from app.data.database import get_session_factory
    from app.data.models.scenarios import EmployeeRequest

    factory = get_session_factory()
    async with factory() as db:
        db.info["tenant_id"] = tenant_id
        row = (
            (
                await db.execute(
                    select(EmployeeRequest).where(
                        EmployeeRequest.tenant_id == tenant_id,
                        EmployeeRequest.id == request_id,
                        EmployeeRequest.created_by == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            # Uniform denial — no disclosure of whether the request exists (spec §3.3)
            raise NotFoundError("Request", request_id)
    return _employee_view(row)
