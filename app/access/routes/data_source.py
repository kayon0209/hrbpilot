"""Data source admin routes (Phase 5 — 数据接入, spec §7.10).

Admin-only platform surface (capability ``data_source_admin`` in RBAC).
Business language throughout: no connector/MCP vocabulary in responses.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.data_source.service import (
    CreateDataSourceBody,
    create_data_source,
    list_data_sources,
    pause_data_source,
    resume_data_source,
    revoke_data_source,
)

router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])


@router.get("")
@require_auth
async def list_sources(request: Request):
    tenant_id = require_tenant_id(request)
    views = await list_data_sources(tenant_id)
    return {"sources": [v.model_dump() for v in views]}


@router.post("")
@require_auth
async def create_source(body: CreateDataSourceBody, request: Request):
    tenant_id = require_tenant_id(request)
    user_id = getattr(request.state, "user_id", "unknown")
    view = await create_data_source(tenant_id, user_id, body)
    return view.model_dump()


class PauseBody(BaseModel):
    pass


@router.post("/{source_id}/pause")
@require_auth
async def pause(source_id: str, request: Request):
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    view = await pause_data_source(tenant_id, actor_id, source_id)
    return view.model_dump()


@router.post("/{source_id}/resume")
@require_auth
async def resume(source_id: str, request: Request):
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    view = await resume_data_source(tenant_id, actor_id, source_id)
    return view.model_dump()


class RevokeBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


@router.post("/{source_id}/revoke")
@require_auth
async def revoke(source_id: str, body: RevokeBody, request: Request):
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    view = await revoke_data_source(tenant_id, actor_id, source_id, body.reason)
    return view.model_dump()
