"""MCP REST bridge — lets the workbench UI discover and try the MCP tools.

The Streamable-HTTP MCP endpoint lives at ``/mcp`` (SDK-managed, mounted in
``app.main``).  This bridge exposes the same capabilities as plain JSON over
``/api/mcp`` so the SPA can render them without speaking the MCP wire
protocol.  Every call is tenant-scoped and reuses the existing JWT + Tool
whitelist + approval gate — nothing bypasses the HR case safety net.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.mcp.server import _WRITE_TOOL_NAMES
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, validate_tool_call

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ToolCallBody(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _tool_view(name: str) -> dict[str, Any]:
    tool = next((t for t in TOOL_CATALOG.tools if t.name == name), None)
    if tool is None:
        return {"name": name}
    return {
        "name": tool.name,
        "kind": tool.kind.value,
        "description": tool.input_schema.get("description") if isinstance(tool.input_schema, dict) else None,
        "input_schema": tool.input_schema,
        "requires_approval": tool.approval_required,
        "capability": tool.required_capability,
    }


@router.get("/capabilities")
@require_auth
async def capabilities(request: Request) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    read_tools = [t.name for t in TOOL_CATALOG.tools if t.kind.value == "read"]
    write_tools = [t.name for t in TOOL_CATALOG.tools if t.kind.value == "write"]
    return {
        "server": "hrbpilot-mcp",
        "scope": "L",
        "transports": [
            {"kind": "stdio", "command": "python -m app.mcp.server", "note": "Inspector / Claude Desktop — 本地直连"},
            {"kind": "streamable-http", "url": "/mcp", "note": "远程：需 Authorization: Bearer <JWT>，匿名读放行、匿名写拒"},
        ],
        "tenant_id": tenant_id,
        "read_tools": [_tool_view(n) for n in read_tools],
        "write_tools": [_tool_view(n) for n in write_tools],
        "write_mode": "create-approval-request",
        "auth": "读可匿名试，写需登录（携带 JWT）；写工具仅创建 ApprovalRequest，不直执",
    }


@router.get("/tools")
@require_auth
async def list_tools(request: Request) -> dict[str, Any]:
    _ = require_tenant_id(request)
    return {
        "tools": [_tool_view(t.name) for t in TOOL_CATALOG.tools],
        "count": len(TOOL_CATALOG.tools),
    }


@router.post("/tools/{tool_name}/call")
@require_auth
async def call_tool(tool_name: str, body: ToolCallBody, request: Request) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    user_id: str = getattr(request.state, "user_id", "unknown")
    role: str = getattr(request.state, "user_role", "unknown")

    if tool_name not in {t.name for t in TOOL_CATALOG.tools}:
        return {"ok": False, "error": "UNKNOWN_TOOL", "message": f"Tool {tool_name} is not whitelisted."}

    is_write = tool_name in set(_WRITE_TOOL_NAMES)

    if is_write:
        case_id = body.arguments.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            return {"ok": False, "error": "CASE_ID_REQUIRED", "message": "写工具需提供 case_id（HR 案件 ID），仅创建审批，不直执。"}
        params = {k: v for k, v in body.arguments.items() if k != "case_id"}
        try:
            validated = validate_tool_call(tool_name, params)
        except Exception as e:
            return {"ok": False, "error": type(e).__name__, "message": str(e)}

        from app.data.database import tenant_session
        from app.scenarios.hr_case_agent.service import HRCaseService

        actor = f"user:{user_id}|role:{role}"
        try:
            async with tenant_session(tenant_id) as session:
                service = HRCaseService(session, tenant_id, actor=actor)
                approval = await service.request_approval(case_id.strip(), tool_name=tool_name, params=validated)
                await session.commit()
                return {
                    "ok": True,
                    "tool": tool_name,
                    "case_id": case_id.strip(),
                    "approval_id": approval.id,
                    "status": approval.status,
                    "tenant_id": tenant_id,
                    "message": "已创建审批请求（AWAITING_APPROVAL）。需 HR 经理/管理员在“团队待处理”中批准后，经 ToolGateway → Outbox → Worker 执行。",
                    "next": {"approve": f"POST /api/v1/hr-cases/{case_id.strip()}/approve", "execute": f"POST /api/v1/hr-cases/{case_id.strip()}/execute"},
                }
        except Exception as e:
            return {"ok": False, "error": type(e).__name__, "message": str(e)}

    # read path — live retrieval when the caller supplies kb_id, otherwise validated payload
    try:
        validated = validate_tool_call(tool_name, body.arguments)
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "message": str(e)}

    if tool_name == "search_policy" and validated.get("kb_id"):
        try:
            from app.rag.retrieval.retriever import Retriever

            chunks = await Retriever().retrieve(
                query=str(validated["query"]),
                kb_id=str(validated["kb_id"]),
                top_k=int(validated.get("top_k", 3)),
                tenant_id=tenant_id,
            )
            return {"ok": True, "tool": tool_name, "validated_params": validated, "chunks": chunks[: int(validated.get("top_k", 3))], "tenant_id": tenant_id}
        except Exception as e:
            return {"ok": True, "tool": tool_name, "validated_params": validated, "warning": f"live retrieval unavailable: {e}", "tenant_id": tenant_id}

    note = "已按白名单校验参数；制度检索需同时提供 kb_id。" if tool_name == "search_policy" else "已按白名单校验参数。"
    return {"ok": True, "tool": tool_name, "validated_params": validated, "note": note, "tenant_id": tenant_id}
