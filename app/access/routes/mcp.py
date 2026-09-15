"""MCP REST bridge — lets the workbench UI discover and try the MCP tools.

The Streamable-HTTP MCP endpoint lives at ``/mcp`` (SDK-managed, mounted in
``app.main``).  This bridge exposes the same capabilities as plain JSON over
``/api/mcp`` so the SPA can render them without speaking the MCP wire
protocol.  Every call is tenant-scoped and reuses the existing JWT + Tool
whitelist + approval gate — nothing bypasses the HR case safety net.

Rules this module keeps in sync with the protocol surface
---------------------------------------------------------
1. **同一条实现**：读工具走 ``app.mcp.read_dispatch.run_read_tool``，与 MCP 出口
   是同一条路径（也因此与 agent loop 共用执行器）。这里不再自己写一遍
   "校验参数 + 返回 note"。
2. **同一个判定**：授权走 ``app.mcp.auth.authorize_tool_call``，与 ``/mcp`` 共用。
   改造前这里按角色判定、而 ``/mcp`` 的读工具完全不判定，于是同一个 ``admin``
   角色在两条出口上结论相反（这里拒、那里放行并返回真实制度正文）。现在两条出口的
   允许/拒绝由同一个函数决定，拒绝响应也由同一个 ``denial_envelope`` 构造。
3. **身份只来自已验证凭据**：主体由 ``verified_principal`` 构造，字段取自
   ``request.scope["auth"]``（AuthMiddleware 写入的已验证 claim），**不**接受
   工具参数或 ``X-Tenant-ID`` 头覆盖。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.access.middleware.decorators import require_auth
from app.access.middleware.tenant import require_tenant_id
from app.access.policies.contracts import ToolDefinition
from app.access.policies.tool_access import MISSING_CAPABILITY_REASON
from app.access.tokens import SCOPE_AUTH_METHOD_INTERNAL
from app.data.database import get_session_factory
from app.mcp import capabilities as capability_manifest
from app.mcp.auth import (
    McpPrincipal,
    ToolAuthorization,
    authorize_tool_call,
    denial_envelope,
    log_denial,
    verified_principal,
    visible_tools,
)
from app.mcp.connection_status import build_my_connections
from app.mcp.contract import APPROVAL_SUBMITTED_TEMPLATE, ToolOutcome, envelope, failure_envelope
from app.mcp.installations import list_installations, revoke_own_installation
from app.mcp.read_dispatch import run_read_tool
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, ToolError, validate_tool_call
from app.shared.errors import AppError, AuthError, NotFoundError
from app.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

#: 与 MCP 协议出口区分开，便于在日志里分辨拒绝来自哪条出口。
_SURFACE = "rest_bridge"


class ToolCallBody(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _principal(request: Request) -> McpPrincipal | None:
    """从 AuthMiddleware 已验证的身份构造主体。

    ``request.scope``（而不是 ``request.state``）是跨中间件层唯一共享的位置 ——
    BaseHTTPMiddleware 每层拿到的 ``state`` 是副本。这与 RBACMiddleware 的读取
    顺序一致。

    **只接受平台自签凭据。** 本桥目前无法表达客户端安装实例与授权上限（它只拿到
    claim 里的三个字段），所以对任何非自签来源一律 fail-closed 返回 None，
    而不是把它当自签凭据处理 —— 那会静默丢掉客户端上限，让这条桥重新变成最弱
    的一环。将来接入外部 Agent 授权时，这里必须显式扩展（把完整主体从请求上带
    过来），而不是靠"没有标记就默认自签"。
    """
    auth = request.scope.get("auth")
    if not isinstance(auth, dict):
        return None
    if auth.get("auth_method") != SCOPE_AUTH_METHOD_INTERNAL:
        return None
    user_id = auth.get("user_id")
    role = auth.get("role")
    tenant_id = auth.get("tenant_id")
    # 逐个 isinstance 收窄（而不是 all(...)）：mypy 只在能收窄到 str 时才认，
    # 否则 Any 会一路传到 verified_principal 的参数上。
    if not isinstance(user_id, str) or not isinstance(role, str) or not isinstance(tenant_id, str):
        return None
    if not user_id or not role or not tenant_id:
        return None
    return verified_principal(user_id=user_id, role=role, tenant_id=tenant_id)


def _denied(decision: ToolAuthorization, principal: McpPrincipal | None) -> dict[str, Any]:
    """共用的拒绝响应 —— 所有工具、所有拒绝原因都走这里。"""
    log_denial(decision, principal, surface=_SURFACE)
    if principal is None:
        return envelope(decision.tool_name, ToolOutcome.AUTH_REQUIRED)
    return denial_envelope(decision, tenant_id=principal.tenant_id)


def _tool_view(tool: ToolDefinition) -> dict[str, Any]:
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
        "scope": tool.required_scope.value,
    }


@router.get("/capabilities")
@require_auth
async def capabilities(request: Request) -> dict[str, Any]:
    tenant_id = require_tenant_id(request)
    principal = _principal(request)
    visible, hidden = visible_tools(principal, TOOL_CATALOG)
    # 任务网关的清单从 manifest 派生，不在这里另抄一份 —— 抄一份就会重演本项目
    # 自己警告过的漂移（加了工具但副本没跟上，而 assert_manifest_is_current()
    # 只校验 目录↔描述↔manifest，拦不住副本）。
    manifest = capability_manifest.build_manifest()
    task_bundle = manifest["bundles"]["task"]
    # 用户自助视图：只回**自己**的安装实例。不加 user_id 过滤就会把同租户其他人
    # 接了什么助手一并返回 —— 那既是隐私问题，也让「我接过的助手」这个名字变成谎话。
    my_installations: list[dict[str, Any]] = []
    if principal is not None:
        async with get_session_factory()() as session:
            session.info["tenant_id"] = tenant_id
            records = await list_installations(session, tenant_id, user_id=principal.user_id, active_only=True)
            my_installations = [record.model_dump(mode="json") for record in records]
    return {
        "server": "hrbpilot-mcp",
        "scope": "L",
        "role": principal.role if principal else "",
        "transports": [
            {"kind": "stdio", "command": "python -m app.mcp.server", "note": "Inspector / Claude Desktop — 本地直连"},
            {
                "kind": "streamable-http",
                "url": "/mcp",
                "note": (
                    "远程：需 Authorization: Bearer <token>；未携带时传输层返回 401 与 "
                    "RFC 9728 挑战（resource_metadata 指向 /.well-known/oauth-protected-resource）"
                ),
            },
            {
                "kind": "streamable-http",
                "url": "/mcp/tasks",
                "note": "任务型网关（默认连接入口）：同一判定，7+2 高频工具 + 冻结草稿/确认/审批/状态",
            },
        ],
        "tenant_id": tenant_id,
        "read_tools": [_tool_view(t) for t in visible if t.kind.value == "read"],
        "write_tools": [_tool_view(t) for t in visible if t.kind.value == "write"],
        # 被过滤掉的工具不返回名称，只返回数量与原因：让界面能解释"为什么这里
        # 比别人少几个"，同时不把无权限的工具名当作可发现信息暴露出去。
        "hidden_tool_count": len(hidden),
        "hidden_reason": MISSING_CAPABILITY_REASON if hidden else None,
        "write_mode": "create-approval-request",
        "task_gateway": {
            "default_bundle": "task",
            "entrypoint": task_bundle["entrypoint"],
            "tools": task_bundle["tools"],
            "task_types": task_bundle["task_types"],
            "manifest_version": manifest["version"],
            "confirmation": "服务端冻结草稿；提交只接受 task_id + draft_version + idempotency_key",
        },
        # 「我接过的助手」——用户自助视图，只含自己那条链上的活跃实例。
        "my_installations": my_installations,
        # 两档授权套餐（仅查询 / 查询 + 提交办理建议）。前端据此拼接入命令，
        # 而不是自己硬编码一份 scope 串 —— 那是本仓库明令禁止的第二份事实源，
        # 而且改了后端没改前端会让用户照着界面配出一个权限不对的助手。
        "scope_packages": manifest["scope_packages"],
        "auth": "读可匿名试（但拿不到真实数据），写需登录；写工具仅创建 ApprovalRequest，不直执",
        "authorization": "按 用户角色能力 ∩ 凭据 scope ∩ 客户端授权上限 判定，与 /mcp 共用同一判定",
    }


@router.get("/my-connections")
@require_auth
async def my_connections(request: Request) -> dict[str, Any]:
    """「我的连接」：我接过的助手 + 每个的最近调用情况 + 一份结构化结论。

    与 ``/capabilities`` 分开的理由见 ``app.mcp.connection_status`` 的模块说明：
    那边是静态能力清单（工具表、套餐），这里是动态运行状态（最近调用、失败、用量）。
    """
    tenant_id = require_tenant_id(request)
    principal = _principal(request)
    if principal is None:
        # 与 ``/capabilities`` 的 ``my_installations`` 不同：这里**不用空列表兜底**。
        # 空列表的含义是"你一个都没接"，而实际发生的是"没识别出你的身份" ——
        # 这两件事对用户是相反的结论，把后者说成前者会让他去重做一个已经做过的接入。
        raise AuthError("Authentication required")

    visible, hidden = visible_tools(principal, TOOL_CATALOG)
    async with get_session_factory()() as session:
        session.info["tenant_id"] = tenant_id
        return await build_my_connections(
            session,
            tenant_id,
            principal.user_id,
            tool_count=len(visible),
            hidden_tool_count=len(hidden),
        )


class RevokeOwnInstallationOut(BaseModel):
    family_id: str
    revoked: bool
    user_message: str


@router.post("/my-installations/{family_id}/revoke")
@require_auth
async def revoke_my_installation(request: Request, family_id: str) -> RevokeOwnInstallationOut:
    """解绑一个**自己接过**的助手。

    与管理员那条 ``/api/admin/mcp/installations/{id}/revoke`` 的区别只有两处：不需要
    ``mcp_admin`` 能力，且把 ``user_id`` 下推到查询里 —— 用户只能撤掉自己的实例。
    归属不符与不存在返回**同一个** 404，不区分：区分它等于提供一个"这个 id 是否
    存在"的探测器，而 ``family_id`` 是可能从日志或截图里泄漏的。
    """
    tenant_id = require_tenant_id(request)
    principal = _principal(request)
    if principal is None:
        raise AuthError("Authentication required")

    async with get_session_factory()() as session:
        session.info["tenant_id"] = tenant_id
        revoked = await revoke_own_installation(
            session,
            tenant_id,
            principal.user_id,
            family_id,
            actor_id=principal.user_id,
        )
        if not revoked:
            raise NotFoundError("oauth_family", family_id)
        await session.commit()

    return RevokeOwnInstallationOut(
        family_id=family_id,
        revoked=True,
        user_message="已解绑。这个助手下一次调用会立即失败，不用等登录过期。",
    )


@router.get("/tools")
@require_auth
async def list_tools(request: Request) -> dict[str, Any]:
    _ = require_tenant_id(request)
    visible, _hidden = visible_tools(_principal(request), TOOL_CATALOG)
    return {"tools": [_tool_view(t) for t in visible], "count": len(visible)}


@router.post("/tools/{tool_name}/call")
@require_auth
async def call_tool(tool_name: str, body: ToolCallBody, request: Request) -> dict[str, Any]:
    # 租户上下文缺失时 fail-closed（抛 AuthError → 401），不静默用一个兜底租户。
    _ = require_tenant_id(request)
    principal = _principal(request)

    decision = authorize_tool_call(principal, tool_name, catalog=TOOL_CATALOG)
    if not decision.allowed:
        return _denied(decision, principal)

    assert principal is not None  # 授权通过 ⇒ 一定有身份（匿名在上面已返回）

    tool = next(candidate for candidate in TOOL_CATALOG.tools if candidate.name == tool_name)
    if tool.kind.value != "write":
        return await run_read_tool(tool_name, body.arguments, principal.tenant_id, principal=principal)

    case_id = body.arguments.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        return failure_envelope(tool_name, "CASE_ID_REQUIRED", tenant_id=principal.tenant_id)
    params = {k: v for k, v in body.arguments.items() if k != "case_id"}
    try:
        validated = validate_tool_call(tool_name, params)
    except ToolError as e:
        return failure_envelope(tool_name, e.code, tenant_id=principal.tenant_id)

    from app.data.database import tenant_session
    from app.scenarios.hr_case_agent.service import HRCaseService

    actor = f"user:{principal.user_id}|role:{principal.role}"
    try:
        async with tenant_session(principal.tenant_id) as session:
            service = HRCaseService(session, principal.tenant_id, actor=actor)
            approval = await service.request_approval(case_id.strip(), tool_name=tool_name, params=validated)
            await session.commit()
            return envelope(
                tool_name,
                ToolOutcome.AWAITING_APPROVAL,
                case_id=case_id.strip(),
                approval_id=approval.id,
                status=approval.status,
                tenant_id=principal.tenant_id,
                user_message=APPROVAL_SUBMITTED_TEMPLATE.format(approval_id=approval.id),
                next={
                    "approve": f"POST /api/v1/hr-cases/{case_id.strip()}/approve",
                    "execute": f"POST /api/v1/hr-cases/{case_id.strip()}/execute",
                },
            )
    except AppError as e:
        logger.warning("mcp_bridge_approval_rejected", tool=tool_name, code=e.code, tenant_id=principal.tenant_id)
        return failure_envelope(tool_name, e.code, tenant_id=principal.tenant_id)
    except Exception:
        logger.exception("mcp_bridge_approval_crashed", tool=tool_name, tenant_id=principal.tenant_id)
        return failure_envelope(tool_name, "INTERNAL_ERROR", tenant_id=principal.tenant_id)
