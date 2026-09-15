"""REST bridge for the Intent-to-Task gateway.

The workbench uses this JSON surface; external Agents use the task MCP facade.
Both call ``AgentTaskService`` and therefore share the same frozen-draft hash,
state machine, ACL checks, approval binding, and status projection.  This route
never accepts tenant/user/client as body fields: all identity comes from the
verified platform token in ``request.scope['auth']``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.access.middleware.decorators import require_auth
from app.access.tokens import SCOPE_AUTH_METHOD_INTERNAL
from app.mcp.auth import McpPrincipal, verified_principal
from app.scenarios.agent_tasks.service import AgentTaskService

router = APIRouter(prefix="/api/agent-tasks", tags=["agent-tasks"])


class PrepareBody(BaseModel):
    goal: str = Field(..., min_length=1, max_length=1000)
    case_ref: str | None = Field(None, max_length=64)
    response_format: str = Field("concise", pattern="^(concise|detailed)$")


class ProvideBody(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict, max_length=8)
    response_format: str = Field("concise", pattern="^(concise|detailed)$")


class SubmitBody(BaseModel):
    draft_version: int = Field(..., ge=1)
    idempotency_key: str = Field(..., min_length=8, max_length=64)


class ListQuery(BaseModel):
    status: str | None = None
    limit: int = Field(20, ge=1, le=20)
    offset: int = Field(0, ge=0, le=980)
    response_format: str = Field("concise", pattern="^(concise|detailed)$")


def _principal(request: Request) -> McpPrincipal:
    auth = request.scope.get("auth")
    if not isinstance(auth, dict) or auth.get("auth_method") != SCOPE_AUTH_METHOD_INTERNAL:
        # This UI bridge deliberately accepts only platform JWTs, just like /api/mcp.
        from app.shared.errors import AuthError

        raise AuthError("Authentication required")
    values = {key: auth.get(key) for key in ("user_id", "role", "tenant_id")}
    if not all(isinstance(value, str) and value for value in values.values()):
        from app.shared.errors import AuthError

        raise AuthError("Authentication required")
    return verified_principal(**values)  # type: ignore[arg-type]


async def _service(request: Request) -> AgentTaskService:
    from app.data.database import get_session_factory

    principal = _principal(request)
    session = get_session_factory()()
    session.info["tenant_id"] = principal.tenant_id
    # Keep the session on request state. Each handler commits/rolls back in one place.
    request.state.agent_task_session = session
    return AgentTaskService(session, principal, trace_id=getattr(request.state, "request_id", None))


async def _finish(request: Request, *, commit: bool = True) -> None:
    session = getattr(request.state, "agent_task_session", None)
    if session is None:
        return
    try:
        if commit:
            await session.commit()
        else:
            await session.rollback()
    finally:
        await session.close()


@router.post("/prepare")
@require_auth
async def prepare(request: Request, body: PrepareBody) -> dict[str, Any]:
    try:
        service = await _service(request)
        return await service.prepare(body.goal, case_ref=body.case_ref, response_format=body.response_format)
    finally:
        await _finish(request)


@router.post("/{task_id}/input")
@require_auth
async def provide_input(task_id: str, request: Request, body: ProvideBody) -> dict[str, Any]:
    try:
        return await (await _service(request)).provide_input(task_id, body.fields, response_format=body.response_format)
    finally:
        await _finish(request)


@router.post("/{task_id}/submit")
@require_auth
async def submit(task_id: str, request: Request, body: SubmitBody) -> dict[str, Any]:
    try:
        return await (await _service(request)).submit(task_id, body.draft_version, body.idempotency_key)
    finally:
        await _finish(request)


@router.get("/approvals")
@require_auth
async def approval_queue(request: Request, limit: int = 20) -> dict[str, Any]:
    """审批人的待办队列（hr_manager only）。

    必须注册在 ``/{task_id}`` **之前**：FastAPI 按声明顺序匹配，放后面会被
    ``{task_id}`` 吞掉。决定动作本身走既有业务接口 ``POST /api/v1/hr-cases/…/approve``，
    这里只负责把队列送到决定人眼前。
    """
    limit = max(1, min(limit, 50))
    try:
        return await (await _service(request)).approval_queue(limit=limit)
    finally:
        await _finish(request)


@router.get("/{task_id}")
@require_auth
async def get_status(task_id: str, request: Request, response_format: str = "concise") -> dict[str, Any]:
    try:
        return await (await _service(request)).get_status(task_id, response_format=response_format)
    finally:
        await _finish(request)


@router.get("")
@require_auth
async def list_tasks(
    request: Request, status: str | None = None, limit: int = 20, offset: int = 0, response_format: str = "concise"
) -> dict[str, Any]:
    query = ListQuery(status=status, limit=limit, offset=offset, response_format=response_format)
    try:
        return await (await _service(request)).list_tasks(**query.model_dump())
    finally:
        await _finish(request)


@router.post("/{task_id}/cancel")
@require_auth
async def cancel(task_id: str, request: Request) -> dict[str, Any]:
    try:
        return await (await _service(request)).cancel(task_id)
    finally:
        await _finish(request)


@router.get("/{task_id}/events")
@require_auth
async def events(task_id: str, request: Request) -> dict[str, Any]:
    try:
        service = await _service(request)
        return {"task_id": task_id, "events": await service.task_events(task_id)}
    finally:
        await _finish(request)
