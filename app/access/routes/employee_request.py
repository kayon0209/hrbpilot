"""Employee request routes (Phase 4 — Request 闭环).

Employee endpoints (/api/my-requests): own requests only, desensitized.
HR triage endpoints (/api/hr-requests): capability-gated by RBACMiddleware.
The employee surface never includes hr_note, hr_case_id or internal plans.
"""

from fastapi import APIRouter, Request

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.employee_request.service import (
    CreateRequestBody,
    EmployeeRequestView,
    HrTriageBody,
    create_request,
    get_my_request,
    hr_list_assignees,
    hr_list_open,
    hr_triage,
    list_my_requests,
)

router = APIRouter(tags=["employee-requests"])


# ---- employee surface: own requests only ----

@router.post("/api/my-requests")
@require_auth
async def file_request(body: CreateRequestBody, request: Request):
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    view: EmployeeRequestView = await create_request(tenant_id, user_id, body)
    return view.model_dump()


@router.get("/api/my-requests")
@require_auth
async def my_requests(request: Request):
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    views = await list_my_requests(tenant_id, user_id)
    return {"requests": [v.model_dump() for v in views]}


@router.get("/api/my-requests/{request_id}")
@require_auth
async def my_request_detail(request_id: str, request: Request):
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    view = await get_my_request(tenant_id, user_id, request_id)
    return view.model_dump()


# ---- HR triage surface: capability-gated (hrbp / hr_manager) ----

@router.get("/api/hr-requests")
@require_auth
async def open_hr_requests(request: Request):
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "user_role", "employee")
    rows = await hr_list_open(tenant_id, actor_id, actor_role)
    return {"requests": rows}


@router.get("/api/hr-requests/assignees")
@require_auth
async def assignable_hrbps(request: Request):
    """HRBPs a manager may assign requests to (audit P1-7)."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "user_role", "employee")
    return {"assignees": await hr_list_assignees(tenant_id, actor_id, actor_role)}


@router.post("/api/hr-requests/{request_id}/triage")
@require_auth
async def triage_request(request_id: str, body: HrTriageBody, request: Request):
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "user_role", "employee")
    return await hr_triage(tenant_id, actor_id, actor_role, request_id, body)
