"""HRBPilot MCP server — L scope.

Read tools: live RAG when infrastructure is reachable, otherwise validated payload.
Write tools: create ``ApprovalRequest`` (never auto-execute) — preserves the
human approval gate (``APPROVED → CONSUMED``) and Outbox/Worker path.

Auth: stdio has no headers (anonymous); HTTP carries ``Authorization: Bearer <JWT>``
which is decoded with ``app.config.settings`` to recover ``tenant_id/user_id/role``.
Anonymous write calls are rejected with a structured error.

Transports: stdio (``python -m app.mcp.server``) + Streamable HTTP
(mounted at ``/mcp`` inside ``app.main``).  FastMCP/MCPServer naming is the
mcp 2.x ``MCPServer`` surface (``mcp.server.fastmcp`` was renamed).
"""

from __future__ import annotations

import json
from typing import Any

from jose import JWTError, jwt
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.server import MCPServer

from app.config.settings import settings
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, validate_tool_call

mcp_server = MCPServer("hrbpilot-mcp")

_READ_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "read")
_WRITE_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "write")


def _auth_from_ctx(ctx: Context | None) -> dict[str, str] | None:
    if ctx is None:
        return None
    try:
        headers = ctx.headers
    except ValueError:
        return None
    if not headers:
        return None
    auth = headers.get("authorization") or headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    role = payload.get("role")
    tenant_id = payload.get("tenant_id")
    if not all(isinstance(v, str) and v for v in (user_id, role, tenant_id)):
        return None
    return {"user_id": str(user_id), "role": str(role), "tenant_id": str(tenant_id)}


def _actor_label(auth: dict[str, str] | None) -> str:
    if auth is None:
        return "mcp:anonymous"
    return f"user:{auth['user_id']}|role:{auth['role']}"


@mcp_server.tool(
    name="search_policy",
    description="Read-only: hybrid RAG search over the caller's tenant KB. Validates params; with Authorization+kb_id does live retrieval when Milvus/PG reachable, otherwise returns normalized payload.",
)
async def search_policy(
    query: str,
    kb_id: str | None = None,
    top_k: int = 3,
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"query": query, "top_k": top_k}
    if kb_id is not None:
        params["kb_id"] = kb_id
    validated = validate_tool_call("search_policy", params)
    auth = _auth_from_ctx(ctx)
    if auth is not None and kb_id:
        try:
            from app.rag.retrieval.retriever import Retriever

            chunks = await Retriever().retrieve(
                query=validated["query"],
                kb_id=str(validated.get("kb_id") or kb_id),
                top_k=int(validated.get("top_k", 3)),
                tenant_id=auth["tenant_id"],
            )
            return {
                "tool": "search_policy",
                "validated_params": validated,
                "chunks": chunks[: int(validated.get("top_k", 3))],
                "auth": {"tenant_id": auth["tenant_id"]},
            }
        except Exception as e:
            return {
                "tool": "search_policy",
                "validated_params": validated,
                "warning": f"live retrieval unavailable: {e}",
                "auth": {"tenant_id": auth["tenant_id"]},
            }
    return {
        "tool": "search_policy",
        "validated_params": validated,
        "note": "Validated only (no kb_id or anonymous). Pass kb_id + Authorization to enable live retrieval.",
    }


@mcp_server.tool(
    name="get_policy_source",
    description="Read-only: validates get_policy_source params. With Authorization + kb context a future revision will hydrate the source document.",
)
async def get_policy_source(
    document_name: str,
    section: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    _ = ctx
    params: dict[str, Any] = {"document_name": document_name}
    if section is not None:
        params["section"] = section
    validated = validate_tool_call("get_policy_source", params)
    return {"tool": "get_policy_source", "validated_params": validated, "note": "L: validated; document hydration is P1."}


@mcp_server.tool(name="hrbpilot_ping", description="Health/debug tool for the HRBPilot MCP server.")
async def hrbpilot_ping(ctx: Context | None = None) -> dict[str, Any]:
    auth = _auth_from_ctx(ctx)
    return {
        "ok": True,
        "server": "hrbpilot-mcp",
        "scope": "L",
        "read_tools": sorted(_READ_TOOL_NAMES),
        "write_tools": sorted(_WRITE_TOOL_NAMES),
        "write_mode": "create-approval-request",
        "auth": auth is not None,
    }


async def _create_approval_via_mcp(
    tool_name: str,
    params: dict[str, Any],
    case_id: str,
    ctx: Context | None,
) -> dict[str, Any]:
    auth = _auth_from_ctx(ctx)
    if auth is None:
        return {
            "ok": False,
            "error": "AUTH_REQUIRED",
            "message": "Write MCP tools require Authorization: Bearer <JWT>; anonymous calls are rejected.",
        }
    validated = validate_tool_call(tool_name, params)
    try:
        from app.data.database import tenant_session
        from app.scenarios.hr_case_agent.service import HRCaseService

        async with tenant_session(auth["tenant_id"]) as session:
            service = HRCaseService(session, auth["tenant_id"], actor=_actor_label(auth))
            approval = await service.request_approval(case_id, tool_name=tool_name, params=validated)
            await session.commit()
            return {
                "ok": True,
                "tool": tool_name,
                "case_id": case_id,
                "approval_id": approval.id,
                "status": approval.status,
                "message": "Approval created (AWAITING_APPROVAL). A human with hr_manager/admin must approve before execution; execution then goes via ToolGateway → Outbox → Worker.",
            }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "message": str(e)}


@mcp_server.tool(name="create_hr_case", description="Write (M): create an ApprovalRequest for create_hr_case on an existing case container. Requires Authorization + case_id. Never auto-executes.")
async def mcp_create_hr_case(
    case_id: str,
    title: str,
    subject_ref: str,
    category: str,
    description: str | None = None,
    risk_level: str = "LOW",
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"title": title, "subject_ref": subject_ref, "category": category, "risk_level": risk_level}
    if description is not None:
        params["description"] = description
    return await _create_approval_via_mcp("create_hr_case", params, case_id, ctx)


@mcp_server.tool(name="assign_case_owner", description="Write (M): create an ApprovalRequest for assign_case_owner. Requires Authorization + case_id.")
async def mcp_assign_case_owner(case_id: str, owner_id: str, ctx: Context | None = None) -> dict[str, Any]:
    return await _create_approval_via_mcp("assign_case_owner", {"owner_id": owner_id}, case_id, ctx)


@mcp_server.tool(name="send_case_notification", description="Write (M): create an ApprovalRequest for send_case_notification (in_app only). Requires Authorization + case_id.")
async def mcp_send_case_notification(case_id: str, recipient_ref: str, template: str, ctx: Context | None = None) -> dict[str, Any]:
    return await _create_approval_via_mcp(
        "send_case_notification", {"channel": "in_app", "recipient_ref": recipient_ref, "template": template}, case_id, ctx
    )


@mcp_server.tool(name="update_case_status", description="Write (M): create an ApprovalRequest for update_case_status (only RESOLVED). Requires Authorization + case_id.")
async def mcp_update_case_status(case_id: str, status: str = "RESOLVED", ctx: Context | None = None) -> dict[str, Any]:
    return await _create_approval_via_mcp("update_case_status", {"status": status}, case_id, ctx)


@mcp_server.tool(name="create_work_task", description="Write (M): create an ApprovalRequest for create_work_task. Requires Authorization + case_id.")
async def mcp_create_work_task(
    case_id: str,
    title: str,
    next_action: str = "",
    owner_user_id: str | None = None,
    waiting_for: str | None = None,
    due_at: str | None = None,
    total_units: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"title": title, "next_action": next_action}
    if owner_user_id is not None:
        params["owner_user_id"] = owner_user_id
    if waiting_for is not None:
        params["waiting_for"] = waiting_for
    if due_at is not None:
        params["due_at"] = due_at
    if total_units is not None:
        params["total_units"] = total_units
    return await _create_approval_via_mcp("create_work_task", params, case_id, ctx)


@mcp_server.resource("hrbpilot://capabilities")
async def hrbpilot_capabilities() -> str:
    payload = {
        "server": "hrbpilot-mcp",
        "scope": "L",
        "read_tools": sorted(_READ_TOOL_NAMES),
        "write_tools": sorted(_WRITE_TOOL_NAMES),
        "write_mode": "create-approval-request",
        "transports": ["stdio", "streamable-http:/mcp"],
        "auth": "Authorization: Bearer <JWT> (tenant_id/role from token); anonymous reads allowed, anonymous writes rejected",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp_server.run()
