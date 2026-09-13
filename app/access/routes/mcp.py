"""MCP REST bridge — lets the workbench UI discover and try the MCP tools.

The Streamable-HTTP MCP endpoint lives at ``/mcp`` (SDK-managed, mounted in
``app.main``).  This bridge exposes the same capabilities as plain JSON over
``/api/mcp`` so the SPA can render them without speaking the MCP wire
protocol.  Every call is tenant-scoped and reuses the existing JWT + Tool
whitelist + approval gate — nothing bypasses the HR case safety net.

Two rules this module keeps in sync with the protocol surface
-------------------------------------------------------------
1. **同一条实现**：读工具走 ``app.mcp.read_dispatch.run_read_tool``，与 MCP 出口
   是同一条路径（也因此与 agent loop 共用执行器）。这里不再自己写一遍
   "校验参数 + 返回 note"。
2. **按角色收窄**：``required_capability`` 过去只在执行侧校验，发现侧把整份目录
   原样返回给任何登录用户。现在发现与调用都按调用者角色过滤/拒绝，两处共用
   ``app.access.policies.tool_access``，不会各自漂移。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.access.policies.tool_access import MISSING_CAPABILITY_REASON, partition_tools, tool_allowed
from app.mcp.contract import APPROVAL_SUBMITTED_TEMPLATE, ToolOutcome, envelope, failure_envelope
from app.mcp.read_dispatch import run_read_tool
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, ToolError, validate_tool_call
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ToolCallBody(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _caller_role(request: Request) -> str | None:
    """与 RBACMiddleware 同样的解析顺序：先 scope["auth"]，再 state。

    BaseHTTPMiddleware 的 state 是逐层副本，所以 scope 更可靠。
    """
    auth = request.scope.get("auth")
    if isinstance(auth, dict):
        role = auth.get("role")
        if isinstance(role, str) and role:
            return role
    role = getattr(request.state, "user_role", None)
    return role if isinstance(role, str) and role else None


def _tool_view(name: str) -> dict[str, Any]:
    tool = next((t for t in TOOL_CATALOG.tools if t.name == name), None)
    if tool is None:
        return {"name": name}
    return {
        "name": tool.name,
        "kind": tool.kind.value,
        "description": tool.input_schema.get("description") if isinstance(tool.input_schema, dict) else None,
        # input_schema 仍返回：它是工作台「业务表单」的唯一来源，
        # 前端据它生成中文字段，而不是另维护一份会漂移的字段表。
        # 界面上不再把它原样铺出来（见 web/src/features/mcp/McpPage.tsx）。
        "input_schema": tool.input_schema,
        "requires_approval": tool.approval_required,
        "capability": tool.required_capability,
    }


@router.get("/capabilities")
@require_auth
async def capabilities(request: Request) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    role = _caller_role(request)
    visible, hidden = partition_tools(TOOL_CATALOG, role)
    read_tools = [t.name for t in visible if t.kind.value == "read"]
    write_tools = [t.name for t in visible if t.kind.value == "write"]
    return {
        "server": "hrbpilot-mcp",
        "scope": "L",
        "role": role or "",
        "transports": [
            {"kind": "stdio", "command": "python -m app.mcp.server", "note": "Inspector / Claude Desktop — 本地直连"},
            {
                "kind": "streamable-http",
                "url": "/mcp",
                "note": "远程：需 Authorization: Bearer <JWT>，匿名读放行、匿名写拒",
            },
        ],
        "tenant_id": tenant_id,
        "read_tools": [_tool_view(n) for n in read_tools],
        "write_tools": [_tool_view(n) for n in write_tools],
        # 被过滤掉的工具不返回名称，只返回数量与原因：让界面能解释"为什么这里
        # 比别人少几个"，同时不把无权限的工具名当作可发现信息暴露出去。
        "hidden_tool_count": len(hidden),
        "hidden_reason": MISSING_CAPABILITY_REASON if hidden else None,
        "write_mode": "create-approval-request",
        "auth": "读可匿名试，写需登录（携带 JWT）；写工具仅创建 ApprovalRequest，不直执",
    }


@router.get("/tools")
@require_auth
async def list_tools(request: Request) -> dict[str, Any]:
    _ = require_tenant_id(request)
    visible, _hidden = partition_tools(TOOL_CATALOG, _caller_role(request))
    return {"tools": [_tool_view(t.name) for t in visible], "count": len(visible)}


@router.post("/tools/{tool_name}/call")
@require_auth
async def call_tool(tool_name: str, body: ToolCallBody, request: Request) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    user_id: str = getattr(request.state, "user_id", "unknown")
    role = _caller_role(request)

    tool = next((t for t in TOOL_CATALOG.tools if t.name == tool_name), None)
    if tool is None:
        return failure_envelope(tool_name, "UNKNOWN_TOOL")

    if not tool_allowed(tool, role):
        # 执行侧（policy → grant → dispatcher）本来就会拒，这里提前拒是为了
        # 不让用户走完"提交 → 待审批"才发现这份审批永远不会通过。
        logger.warning("mcp_bridge_forbidden", tool=tool_name, role=role, tenant_id=tenant_id)
        return envelope(tool_name, ToolOutcome.FORBIDDEN, tenant_id=tenant_id)

    if tool.kind.value != "write":
        return await run_read_tool(tool_name, body.arguments, tenant_id)

    case_id = body.arguments.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        return failure_envelope(tool_name, "CASE_ID_REQUIRED", tenant_id=tenant_id)
    params = {k: v for k, v in body.arguments.items() if k != "case_id"}
    try:
        validated = validate_tool_call(tool_name, params)
    except ToolError as e:
        return failure_envelope(tool_name, e.code, tenant_id=tenant_id)

    from app.data.database import tenant_session
    from app.scenarios.hr_case_agent.service import HRCaseService

    actor = f"user:{user_id}|role:{role}"
    try:
        async with tenant_session(tenant_id) as session:
            service = HRCaseService(session, tenant_id, actor=actor)
            approval = await service.request_approval(case_id.strip(), tool_name=tool_name, params=validated)
            await session.commit()
            return envelope(
                tool_name,
                ToolOutcome.AWAITING_APPROVAL,
                case_id=case_id.strip(),
                approval_id=approval.id,
                status=approval.status,
                tenant_id=tenant_id,
                user_message=APPROVAL_SUBMITTED_TEMPLATE.format(approval_id=approval.id),
                next={
                    "approve": f"POST /api/v1/hr-cases/{case_id.strip()}/approve",
                    "execute": f"POST /api/v1/hr-cases/{case_id.strip()}/execute",
                },
            )
    except AppError as e:
        logger.warning("mcp_bridge_approval_rejected", tool=tool_name, code=e.code, tenant_id=tenant_id)
        return failure_envelope(tool_name, e.code, tenant_id=tenant_id)
    except Exception:
        logger.exception("mcp_bridge_approval_crashed", tool=tool_name, tenant_id=tenant_id)
        return failure_envelope(tool_name, "INTERNAL_ERROR", tenant_id=tenant_id)
