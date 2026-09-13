"""HRBPilot MCP server — L scope.

Read tools: real tenant-scoped retrieval. Both read tools are dispatched through
``app.mcp.read_dispatch.run_read_tool``, i.e. the *same* executors the agent loop
uses — there is no second, weaker implementation behind this surface.
Write tools: create ``ApprovalRequest`` (never auto-execute) — preserves the
human approval gate (``APPROVED → CONSUMED``) and Outbox/Worker path.

Auth: stdio has no headers (anonymous); HTTP carries ``Authorization: Bearer <JWT>``
which is decoded with ``app.config.settings`` to recover ``tenant_id/user_id/role``.

Every tool call returns the shared envelope from ``app.mcp.contract`` so a caller
can tell "有依据 / 没查到 / 无权 / 待审批 / 失败" apart instead of guessing from
``ok`` and ``warning``。

发现（discovery）不是安全边界 —— 未认证的 stdio 客户端本来就能列到工具名，
真正的边界是**执行时按角色校验能力**（本文件与 REST 桥都做）。

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

from app.access.policies.tool_access import tool_allowed
from app.config.settings import settings
from app.mcp.contract import APPROVAL_SUBMITTED_TEMPLATE, ToolOutcome, envelope, failure_envelope
from app.mcp.read_dispatch import anonymous_read_envelope, run_read_tool
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, ToolError, validate_tool_call
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

mcp_server = MCPServer("hrbpilot-mcp")

_READ_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "read")
_WRITE_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "write")


def _tool_definition(name: str):
    return next((t for t in TOOL_CATALOG.tools if t.name == name), None)


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
    description="Read-only: hybrid RAG search (dense + PostgreSQL FTS + RRF) over the caller's tenant policy KB. Requires Authorization; anonymous calls return the validated parameters only, never policy content.",
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
    auth = _auth_from_ctx(ctx)
    if auth is None:
        return anonymous_read_envelope("search_policy", params)
    return await run_read_tool("search_policy", params, auth["tenant_id"])


@mcp_server.tool(
    name="get_policy_source",
    description="Read-only: hydrate one policy document (optionally a single section) so the caller can cite the real text. Requires Authorization.",
)
async def get_policy_source(
    document_name: str,
    section: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"document_name": document_name}
    if section is not None:
        params["section"] = section
    auth = _auth_from_ctx(ctx)
    if auth is None:
        return anonymous_read_envelope("get_policy_source", params)
    return await run_read_tool("get_policy_source", params, auth["tenant_id"])


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
        return envelope(tool_name, ToolOutcome.AUTH_REQUIRED)

    tool = _tool_definition(tool_name)
    if tool is not None and not tool_allowed(tool, auth["role"]):
        # 执行侧（policy → dispatcher）本来就会拒。这里提前拒，是为了让用户在
        # 提交之前就知道结果，而不是等一个注定失败的审批。
        logger.warning("mcp_tool_forbidden", tool=tool_name, role=auth["role"], tenant_id=auth["tenant_id"])
        return envelope(tool_name, ToolOutcome.FORBIDDEN, tenant_id=auth["tenant_id"])

    try:
        validated = validate_tool_call(tool_name, params)
    except ToolError as e:
        return failure_envelope(tool_name, e.code)

    try:
        from app.data.database import tenant_session
        from app.scenarios.hr_case_agent.service import HRCaseService

        async with tenant_session(auth["tenant_id"]) as session:
            service = HRCaseService(session, auth["tenant_id"], actor=_actor_label(auth))
            approval = await service.request_approval(case_id, tool_name=tool_name, params=validated)
            await session.commit()
            return envelope(
                tool_name,
                ToolOutcome.AWAITING_APPROVAL,
                case_id=case_id,
                approval_id=approval.id,
                status=approval.status,
                tenant_id=auth["tenant_id"],
                user_message=APPROVAL_SUBMITTED_TEMPLATE.format(approval_id=approval.id),
            )
    except AppError as e:
        # 受控失败码（HIGH_RISK_WRITE_BLOCKED / INVALID_CASE_TRANSITION / …）：
        # 映射成用户文案，原始 message 只进日志。
        logger.warning("mcp_approval_rejected", tool=tool_name, code=e.code, tenant_id=auth["tenant_id"])
        return failure_envelope(tool_name, e.code, tenant_id=auth["tenant_id"])
    except Exception:
        logger.exception("mcp_approval_crashed", tool=tool_name, tenant_id=auth["tenant_id"])
        return failure_envelope(tool_name, "INTERNAL_ERROR", tenant_id=auth["tenant_id"])


@mcp_server.tool(
    name="create_hr_case",
    description="Write (M): create an ApprovalRequest for create_hr_case on an existing case container. Requires Authorization + case_id. Never auto-executes.",
)
async def mcp_create_hr_case(
    case_id: str,
    title: str,
    subject_ref: str,
    category: str,
    description: str | None = None,
    risk_level: str = "LOW",
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "title": title,
        "subject_ref": subject_ref,
        "category": category,
        "risk_level": risk_level,
    }
    if description is not None:
        params["description"] = description
    return await _create_approval_via_mcp("create_hr_case", params, case_id, ctx)


@mcp_server.tool(
    name="assign_case_owner",
    description="Write (M): create an ApprovalRequest for assign_case_owner. Requires Authorization + case_id.",
)
async def mcp_assign_case_owner(case_id: str, owner_id: str, ctx: Context | None = None) -> dict[str, Any]:
    return await _create_approval_via_mcp("assign_case_owner", {"owner_id": owner_id}, case_id, ctx)


@mcp_server.tool(
    name="send_case_notification",
    description="Write (M): create an ApprovalRequest for send_case_notification (in_app only). Requires Authorization + case_id.",
)
async def mcp_send_case_notification(
    case_id: str, recipient_ref: str, template: str, ctx: Context | None = None
) -> dict[str, Any]:
    return await _create_approval_via_mcp(
        "send_case_notification",
        {"channel": "in_app", "recipient_ref": recipient_ref, "template": template},
        case_id,
        ctx,
    )


@mcp_server.tool(
    name="update_case_status",
    description="Write (M): create an ApprovalRequest for update_case_status (only RESOLVED). Requires Authorization + case_id.",
)
async def mcp_update_case_status(case_id: str, status: str = "RESOLVED", ctx: Context | None = None) -> dict[str, Any]:
    return await _create_approval_via_mcp("update_case_status", {"status": status}, case_id, ctx)


@mcp_server.tool(
    name="create_work_task",
    description="Write (M): create an ApprovalRequest for create_work_task. Requires Authorization + case_id.",
)
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
    """Discovery document.

    刻意**不**在这份文档里做角色过滤：MCP 的 resource 与 tool 列表对未认证的
    stdio 客户端本来就可见，隐藏名字不会带来安全收益，反而会让客户端以为
    "没有这个能力"。真正的边界在执行：每个工具在调用时都会按调用者的角色
    校验 ``required_capability``（MCP 出口与 REST 桥都做），不通过就返回
    FORBIDDEN，且执行链（policy → grant → dispatcher）会再复核一次。
    """
    payload = {
        "server": "hrbpilot-mcp",
        "scope": "L",
        "read_tools": sorted(_READ_TOOL_NAMES),
        "write_tools": sorted(_WRITE_TOOL_NAMES),
        "write_mode": "create-approval-request",
        "transports": ["stdio", "streamable-http:/mcp"],
        "auth": "Authorization: Bearer <JWT> (tenant_id/role from token); anonymous reads allowed, anonymous writes rejected",
        "authorization": "每个工具在调用时按调用者角色校验 required_capability；不通过返回 FORBIDDEN，不会进入审批或执行链。",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp_server.run()
