"""Data source admin routes (Phase 5 — 数据接入, spec §7.10).

Admin-only platform surface (capability ``data_source_admin`` in RBAC).
Business language throughout: no connector/MCP vocabulary in responses.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.access.middleware.decorators import require_auth, require_capability
from app.access.middleware.tenant import require_tenant_id
from app.scenarios.data_source.service import (
    CreateDataSourceBody,
    WeComCallbackConfigBody,
    bind_platform_identity,
    complete_oauth,
    configure_wecom_callback,
    create_data_source,
    list_data_sources,
    pause_data_source,
    resume_data_source,
    revoke_data_source,
    start_oauth,
    trigger_sync,
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


class IdentityBindingBody(BaseModel):
    external_user_id: str = Field(..., min_length=1, max_length=255)
    user_id: str = Field(..., min_length=1, max_length=36)


@router.post("/{source_id}/identity-bindings")
@require_auth
@require_capability("data_source_admin")
async def bind_identity(source_id: str, body: IdentityBindingBody, request: Request):
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    return await bind_platform_identity(
        tenant_id, actor_id, source_id, body.external_user_id, body.user_id
    )


@router.put("/{source_id}/wecom-callback-config")
@require_auth
@require_capability("data_source_admin")
async def configure_wecom_callback_route(
    source_id: str, body: WeComCallbackConfigBody, request: Request
):
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    return await configure_wecom_callback(tenant_id, actor_id, source_id, body)


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


class OAuthStartBody(BaseModel):
    redirect_uri: str = Field(..., min_length=1, max_length=500)


@router.post("/{source_id}/oauth-start")
@require_auth
async def oauth_start(source_id: str, body: OAuthStartBody, request: Request):
    """Generate the platform consent URL; the admin follows it off-site."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    result = await start_oauth(tenant_id, actor_id, source_id, body.redirect_uri)
    return result


class OAuthCallbackBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=2000)
    state: str = Field(..., min_length=1, max_length=500)


@router.post("/{source_id}/oauth-callback")
@require_auth
async def oauth_callback(source_id: str, body: OAuthCallbackBody, request: Request):
    """Complete the consent flow: validate the CSRF state, exchange the code."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    view = await complete_oauth(tenant_id, actor_id, source_id, body.code, body.state)
    return view.model_dump()


class SyncBody(BaseModel):
    pass


@router.post("/{source_id}/sync")
@require_auth
async def sync_now(source_id: str, request: Request):
    """Trigger one guarded sync; the result lands on the source row."""
    tenant_id = require_tenant_id(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    view = await trigger_sync(tenant_id, actor_id, source_id)
    return view.model_dump()
