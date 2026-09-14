"""HRBPilot MCP server — L scope.

Read tools: real tenant-scoped retrieval. Both read tools are dispatched through
``app.mcp.read_dispatch.run_read_tool``, i.e. the *same* executors the agent loop
uses — there is no second, weaker implementation behind this surface.
Write tools: create ``ApprovalRequest`` (never auto-execute) — preserves the
human approval gate (``APPROVED → CONSUMED``) and Outbox/Worker path.

Auth — two transports, two ways of saying "credentials required"
--------------------------------------------------------------
- **HTTP** (``/mcp``): the *transport layer* authenticates first
  (``app/access/middleware/auth.py``). A request without credentials never reaches
  this module — it gets **401 + an RFC 9728 challenge**
  (``app/access/resource_metadata.py``) so the MCP client can discover the
  authorization server. With credentials present, identity is resolved again from
  ``ctx.headers`` — through the same entry point the transport layer uses
  (``app.access.as_tokens.resolve_access_claims``), so there is still only one place
  that interprets a token.
- **stdio**: no headers exist, so every call is anonymous. Read tools return the
  ``AUTH_REQUIRED`` envelope — a process with no status codes has no other way to
  express "this call has no scope".

The difference is deliberate, not an inconsistency: the two transports have
different channels for expressing refusal. What must stay identical is the
**authorization decision** on the tool call, and that is shared
(``app.mcp.auth.authorize_tool_call``).

授权（2026-09 改造）
--------------------
本出口与 REST 桥（``app/access/routes/mcp.py``）**共用**同一个判定：
``app.mcp.auth.authorize_tool_call``。改造前本出口的读工具不做角色校验，而 REST 桥
做 —— 同一个 ``admin`` 角色在两条出口上结论相反（REST 桥拒、协议出口放行并返回
真实制度正文）。现在两条出口按 角色能力 ∩ scope ∩ 客户端授权上限 判定，
拒绝响应也由同一个 ``denial_envelope`` 构造。

发现（discovery）不是安全边界 —— 未认证的 stdio 客户端本来就能列到工具名，
真正的边界是**执行时按角色与 scope 判定**（本文件与 REST 桥都做）。

Transports: stdio (``python -m app.mcp.server``) + Streamable HTTP
(mounted at ``/mcp`` inside ``app.main``).  FastMCP/MCPServer naming is the
mcp 2.x ``MCPServer`` surface (``mcp.server.fastmcp`` was renamed).
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.server import MCPServer
from mcp.types import ToolAnnotations

from app.mcp.audit import record_mcp_call
from app.mcp.auth import (
    McpPrincipal,
    authorize_tool_call,
    denial_envelope,
    log_denial,
    principal_from_headers,
)
from app.mcp.contract import APPROVAL_SUBMITTED_TEMPLATE, ToolOutcome, envelope, failure_envelope
from app.mcp.read_dispatch import anonymous_read_envelope, run_read_tool
from app.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, ToolError, validate_tool_call
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

mcp_server = MCPServer("hrbpilot-mcp")

#: 日志里标识"这次拒绝来自哪条出口"。WP1 修掉的那处越权正是"两条出口结论不同"，
#: 标出来才能在回归时一眼分辨。
_SURFACE = "mcp_protocol"

_READ_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "read")
_WRITE_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "write")


def _annotations_for(tool_name: str) -> ToolAnnotations:
    """从 catalog 元数据派生协议层 annotations —— 映射，不是第二份事实。

    取值保守（任务书 T3-1）：

    - 读工具：``read_only_hint=True``，其余无副作用语义；
    - 写工具：``read_only_hint=False``、``destructive_hint=True``（真正的副作用
      在人工审批之后，宁可让客户端多确认一步，也不低估风险）、
      ``idempotent_hint=True``（写工具声明了 ``supports_idempotency``，同参数
      重复提交会复用同一条待审批记录）；
    - 全部 ``open_world_hint=False``：工具只与本公司系统交互。

    从 catalog 派生而非手写：新工具登记进 catalog 就自动带正确取值，
    "新增工具漏配 annotations"这一类漂移从根上消失。
    """
    is_read = tool_name in _READ_TOOL_NAMES
    return ToolAnnotations(
        title=tool_name,
        read_only_hint=is_read,
        destructive_hint=not is_read,
        idempotent_hint=True,
        open_world_hint=False,
    )


def _describe(tool_name: str) -> str:
    """集中描述的取用口；catalog 新增工具而描述未登记时立刻暴露（不静默回落）。"""
    try:
        return TOOL_DESCRIPTIONS[tool_name]
    except KeyError:  # pragma: no cover - 新工具忘写描述时的快速失败
        raise KeyError(
            f"tool {tool_name!r} has no entry in app/mcp/tool_descriptions.py — "
            "every catalog tool needs a versioned description (何时用/何时不用/例子)"
        ) from None


async def _principal_from_ctx(ctx: Context | None) -> McpPrincipal | None:
    """从 MCP 调用上下文取出已验证身份；无法确定身份时返回 ``None``（匿名）。

    ``ctx.headers`` 在 stdio 传输下会抛 ``ValueError``（没有 HTTP 头），
    这不是错误而是"这个传输方式没有凭据"。

    ``async``：AS 签发的令牌要取一次 JWKS 并查一次撤销表（见
    ``app.mcp.auth.adapters``）。平台自签令牌不需要这些，但两条路径共用同一入口 ——
    "哪种凭据要查库"不该由调用点来分辨。
    """
    if ctx is None:
        return None
    try:
        headers = ctx.headers
    except ValueError:
        return None
    return await principal_from_headers(headers)


def _actor_label(principal: McpPrincipal | None) -> str:
    if principal is None:
        return "mcp:anonymous"
    return f"user:{principal.user_id}|role:{principal.role}"


def _elapsed_ms(started: float) -> int:
    """``time.perf_counter`` 起点 → 整数毫秒。"""
    return int((time.perf_counter() - started) * 1000)


async def _run_read(tool_name: str, params: dict[str, Any], ctx: Context | None) -> dict[str, Any]:
    """读工具的统一入口：先授权，再交给 ``run_read_tool``。"""
    started = time.perf_counter()
    principal = await _principal_from_ctx(ctx)
    decision = authorize_tool_call(principal, tool_name, catalog=TOOL_CATALOG)

    if principal is not None and decision.allowed:
        result = await run_read_tool(tool_name, params, principal.tenant_id, principal=principal)
        await record_mcp_call(
            principal,
            tool=tool_name,
            outcome_code=str(result.get("outcome") or ""),
            latency_ms=_elapsed_ms(started),
            params=params,
        )
        return result

    log_denial(decision, principal, surface=_SURFACE)
    # 被拒的调用也要留痕：只记成功调用等于把"有人在试探边界"从审计里删掉。
    await record_mcp_call(
        principal,
        tool=tool_name,
        outcome_code=(
            ToolOutcome.AUTH_REQUIRED.value
            if principal is None
            else (decision.deny_outcome or ToolOutcome.FAILED).value
        ),
        deny_reason=decision.deny_reason.value if decision.deny_reason else None,
        latency_ms=_elapsed_ms(started),
        params=params,
    )
    if principal is None:
        # 匿名不是"错误"，是"没有作用范围"：返回 AUTH_REQUIRED 信封，只回显
        # 已校验参数，不含任何真实数据（anonymous_read_envelope 保证这一点）。
        #
        # 这条分支现在**只服务 stdio 传输**。HTTP 出口在传输层就返回了 401 +
        # RFC 9728 挑战（app/access/middleware/auth.py 的 _dispatch_mcp），请求
        # 根本到不了这里 —— stdio 没有状态码可用，信封是它唯一能表达"需要身份"
        # 的方式。这个差异来自凭据信道不同，不是不一致；必须一致的是**判定**
        # （上面那行 authorize_tool_call）。
        return anonymous_read_envelope(tool_name, params)
    return denial_envelope(decision, tenant_id=principal.tenant_id)


@mcp_server.tool(
    name="search_policy",
    description=_describe("search_policy"),
    annotations=_annotations_for("search_policy"),
    structured_output=True,
)
async def search_policy(
    query: str,
    kb_id: str | None = None,
    top_k: int = 3,
    detail: Literal["concise", "detailed"] = "concise",
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"query": query, "top_k": top_k, "detail": detail}
    if kb_id is not None:
        params["kb_id"] = kb_id
    return await _run_read("search_policy", params, ctx)


@mcp_server.tool(
    name="get_policy_source",
    description=_describe("get_policy_source"),
    annotations=_annotations_for("get_policy_source"),
    structured_output=True,
)
async def get_policy_source(
    document_name: str,
    section: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"document_name": document_name}
    if section is not None:
        params["section"] = section
    return await _run_read("get_policy_source", params, ctx)


@mcp_server.tool(
    name="search_cases",
    description=_describe("search_cases"),
    annotations=_annotations_for("search_cases"),
    structured_output=True,
)
async def search_cases(
    limit: int = 20,
    offset: int = 0,
    detail: Literal["concise", "detailed"] = "concise",
    status: str | None = None,
    category: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset, "detail": detail}
    if status is not None:
        params["status"] = status
    if category is not None:
        params["category"] = category
    return await _run_read("search_cases", params, ctx)


@mcp_server.tool(
    name="get_case_summary",
    description=_describe("get_case_summary"),
    annotations=_annotations_for("get_case_summary"),
    structured_output=True,
)
async def get_case_summary(case_id: str, ctx: Context | None = None) -> dict[str, Any]:
    return await _run_read("get_case_summary", {"case_id": case_id}, ctx)


@mcp_server.tool(
    name="get_approval_status",
    description=_describe("get_approval_status"),
    annotations=_annotations_for("get_approval_status"),
    structured_output=True,
)
async def get_approval_status(
    case_id: str,
    approval_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"case_id": case_id}
    if approval_id is not None:
        params["approval_id"] = approval_id
    return await _run_read("get_approval_status", params, ctx)


@mcp_server.tool(
    name="get_my_access_profile",
    description=_describe("get_my_access_profile"),
    annotations=_annotations_for("get_my_access_profile"),
    structured_output=True,
)
async def get_my_access_profile(ctx: Context | None = None) -> dict[str, Any]:
    """当前凭据的身份摘要与可用范围。

    走 ``_run_read`` 而不是自己拼返回：这条工具也需要参与「角色能力 ∩ scope ∩
    客户端上限」的判定（它的 scope 是 ``hrb:profile:read``），而判定只有一处。
    """
    return await _run_read("get_my_access_profile", {}, ctx)


async def _create_approval_via_mcp(
    tool_name: str,
    params: dict[str, Any],
    case_id: str,
    ctx: Context | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    principal = await _principal_from_ctx(ctx)
    decision = authorize_tool_call(principal, tool_name, catalog=TOOL_CATALOG)
    if not decision.allowed:
        log_denial(decision, principal, surface=_SURFACE)
        await record_mcp_call(
            principal,
            tool=tool_name,
            outcome_code=(
                ToolOutcome.AUTH_REQUIRED.value
                if principal is None
                else (decision.deny_outcome or ToolOutcome.FAILED).value
            ),
            deny_reason=decision.deny_reason.value if decision.deny_reason else None,
            latency_ms=_elapsed_ms(started),
            params=params,
            object_ref=case_id,
        )
        if principal is None:
            return envelope(tool_name, ToolOutcome.AUTH_REQUIRED)
        # 提前拒是为了让用户在提交之前就知道结果，而不是等一个注定失败的审批。
        return denial_envelope(decision, tenant_id=principal.tenant_id)

    assert principal is not None  # 授权通过 ⇒ 一定有身份（匿名在上面的分支已返回）
    try:
        validated = validate_tool_call(tool_name, params)
    except ToolError as e:
        # detail 是受控的校验事实（哪个字段、什么约束），让调用方能自纠错。
        return failure_envelope(tool_name, e.code, detail=str(e.__cause__) if e.code == "INVALID_PARAMS" else None)

    try:
        from app.data.database import tenant_session
        from app.scenarios.hr_case_agent.service import HRCaseService

        async with tenant_session(principal.tenant_id) as session:
            service = HRCaseService(session, principal.tenant_id, actor=_actor_label(principal))
            approval = await service.request_approval(
                case_id,
                tool_name=tool_name,
                params=validated,
                # 绑定发起它的客户端与安装实例：撤销授权时必须能覆盖这条待审批。
                requester_user_id=principal.user_id,
                client_id=principal.client_id,
                installation_id=str(principal.installation_id) if principal.installation_id else None,
            )
            # 审计要能把"这次调用"与"它产生的审批"串起来 —— 事后追责时问的正是
            # "这条审批是哪个客户端、哪一次调用发起的"。写操作的审计与审批共享
            # 一个事务：审计落不下就不能向外宣称已受理。
            await record_mcp_call(
                principal,
                tool=tool_name,
                outcome_code=ToolOutcome.AWAITING_APPROVAL.value,
                latency_ms=_elapsed_ms(started),
                params=params,
                object_ref=case_id,
                approval_id=approval.id,
                session=session,
                required=True,
            )
            await session.commit()
            return envelope(
                tool_name,
                ToolOutcome.AWAITING_APPROVAL,
                case_id=case_id,
                approval_id=approval.id,
                status=approval.status,
                tenant_id=principal.tenant_id,
                user_message=APPROVAL_SUBMITTED_TEMPLATE.format(approval_id=approval.id),
            )
    except AppError as e:
        # 受控失败码（HIGH_RISK_WRITE_BLOCKED / INVALID_CASE_TRANSITION / …）：
        # 映射成用户文案，原始 message 只进日志。
        logger.warning("mcp_approval_rejected", tool=tool_name, code=e.code, tenant_id=principal.tenant_id)
        return failure_envelope(tool_name, e.code, tenant_id=principal.tenant_id)
    except Exception:
        logger.exception("mcp_approval_crashed", tool=tool_name, tenant_id=principal.tenant_id)
        return failure_envelope(tool_name, "INTERNAL_ERROR", tenant_id=principal.tenant_id)


@mcp_server.tool(
    name="create_hr_case",
    description=_describe("create_hr_case"),
    annotations=_annotations_for("create_hr_case"),
    structured_output=True,
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
    description=_describe("assign_case_owner"),
    annotations=_annotations_for("assign_case_owner"),
    structured_output=True,
)
async def mcp_assign_case_owner(case_id: str, owner_id: str, ctx: Context | None = None) -> dict[str, Any]:
    return await _create_approval_via_mcp("assign_case_owner", {"owner_id": owner_id}, case_id, ctx)


@mcp_server.tool(
    name="send_case_notification",
    description=_describe("send_case_notification"),
    annotations=_annotations_for("send_case_notification"),
    structured_output=True,
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
    description=_describe("update_case_status"),
    annotations=_annotations_for("update_case_status"),
    structured_output=True,
)
async def mcp_update_case_status(case_id: str, status: str = "RESOLVED", ctx: Context | None = None) -> dict[str, Any]:
    return await _create_approval_via_mcp("update_case_status", {"status": status}, case_id, ctx)


@mcp_server.tool(
    name="create_work_task",
    description=_describe("create_work_task"),
    annotations=_annotations_for("create_work_task"),
    structured_output=True,
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
    """Discovery document — 这份文档是**能力清单**，不是某个调用者的授权结果。

    刻意**不**按角色过滤。原因有两条，与"隐藏名字当权限控制"无关：

    1. 本资源可匿名读取，对未认证的 stdio 客户端本来就把工具名摆在那里；
    2. 过滤后客户端会以为"没有这个能力"，而不是"我这个身份没有这个能力" ——
       把授权差异说成能力缺失，用户拿到的解释是错的。

    调用者要看**自己**的有效权限摘要，应使用按身份返回的
    ``get_my_access_profile``（后续工作包）。真正的边界在执行：
    ``app.mcp.auth.authorize_tool_call`` 对每次调用按 角色能力 ∩ scope ∩
    客户端授权上限 判定，不通过返回 FORBIDDEN，且不会进入审批或执行链。
    """
    payload = {
        "server": "hrbpilot-mcp",
        "scope": "L",
        "read_tools": sorted(_READ_TOOL_NAMES),
        "write_tools": sorted(_WRITE_TOOL_NAMES),
        "write_mode": "create-approval-request",
        "transports": ["stdio", "streamable-http:/mcp"],
        "auth": (
            "HTTP /mcp：需 Authorization: Bearer <token>；未携带时在**传输层**返回 401 与"
            " RFC 9728 挑战（客户端据此发现授权服务器），不进入工具层。"
            "stdio：无凭据概念，读工具返回 AUTH_REQUIRED 信封且不含任何真实业务数据。"
            "两种传输的表达方式不同，但工具调用的判定是同一个。"
        ),
        "authorization": (
            "每个工具在调用时按 用户角色能力 ∩ 凭据 scope ∩ 客户端授权上限 判定，"
            "两条出口（/mcp 与 /api/mcp）共用同一判定；不通过返回 FORBIDDEN，"
            "不会进入审批或执行链。对象级权限在业务执行层再次校验。"
        ),
        "required_scopes": {tool.name: tool.required_scope.value for tool in TOOL_CATALOG.tools},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _tighten_tool_schemas() -> None:
    """给所有工具的入参 schema 补 ``additionalProperties: false``（T3-5）。

    SDK 从函数签名生成的 schema 默认不写这个键 —— 客户端模型于是认为
    "多传未知参数也合法"。收紧后，拼错参数名（``caseId`` vs ``case_id``）
    会在协议层被拒，而不是被静默吞掉后报一个难定位的 INVALID_PARAMS。
    """
    for tool in mcp_server._tool_manager.list_tools():
        if isinstance(tool.parameters, dict):
            tool.parameters.setdefault("additionalProperties", False)


_tighten_tool_schemas()


if __name__ == "__main__":
    mcp_server.run()
