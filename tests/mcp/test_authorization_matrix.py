"""MCP 授权矩阵 —— 跨角色 × 跨工具 × 跨出口的一致性回归。

本文件存在的直接原因
--------------------
2026-09-13 核验发现：``/api/mcp``（REST 桥）对工具做角色校验，而 ``/mcp``（协议出口）
的**读**工具完全不校验。于是同一个 ``admin`` 角色 —— 它的 ``ROLE_CAPABILITIES``
里没有 ``policy_qa`` —— 在 REST 桥被拒、在协议出口却能拿到真实制度正文。

所以这里逐条锁定三件事：

1. **两条出口结论相同**：同一主体、同一工具，两条出口的拒绝包逐字节同形。
2. **发现被隐藏 ≠ 执行会被拦**：隐藏工具名只是体验，执行期必须重新判定。
3. **写工具永远只创建审批**：被拒时连租户会话都不该开启。

覆盖角色：``employee`` / ``hrbp`` / ``hr_manager`` / ``admin`` / 未知角色。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from app.access.routes import mcp as rest_bridge
from app.access.scopes import CAPABILITY_SCOPES, Scope
from app.access.tokens import SCOPE_AUTH_METHOD_INTERNAL
from app.mcp import server as protocol
from app.mcp.auth import (
    INTERNAL_CLIENT_ID,
    AuthMethod,
    DenyReason,
    McpPrincipal,
    authorize_tool_call,
    denial_envelope,
    verified_principal,
    visible_tools,
)
from app.mcp.auth.authorization import OBJECT_ACL_DEFERRED
from app.mcp.contract import USER_MESSAGES, ToolOutcome
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG

ROLES = ("employee", "hrbp", "hr_manager", "admin", "nobody")
WRITE_TOOLS = {t.name for t in TOOL_CATALOG.tools if t.kind.value == "write"}


def _internal(role: str, *, tenant: str = "tenant-a") -> McpPrincipal:
    return verified_principal(user_id=f"u-{role}", role=role, tenant_id=tenant)


def _oauth(
    *,
    scopes: set[Scope],
    ceiling: set[Scope],
    role: str = "hrbp",
) -> McpPrincipal:
    return McpPrincipal(
        tenant_id="tenant-a",
        user_id="u-external",
        role=role,
        auth_method=AuthMethod.OAUTH,
        client_id="workbuddy",
        installation_id=uuid.uuid4(),
        scopes=frozenset(scopes),
        client_ceiling=frozenset(ceiling),
    )


def _fake_request(principal: McpPrincipal) -> Any:
    """够用的假 Request：``require_auth`` 只认 isinstance(Request)，会自行跳过。"""
    return SimpleNamespace(
        scope={
            "auth": {
                "user_id": principal.user_id,
                "role": principal.role,
                "tenant_id": principal.tenant_id,
                "auth_method": SCOPE_AUTH_METHOD_INTERNAL,
            }
        },
        state=SimpleNamespace(tenant_id=principal.tenant_id, user_id=principal.user_id),
    )


async def _call_protocol(principal: McpPrincipal | None, tool_name: str) -> dict[str, Any]:
    """从协议出口调用一个**注定被拒**的工具，取回拒绝包。

    只用于拒绝路径：允许路径会真去检索/建审批，另有专门用例。
    """
    kind = next(t.kind.value for t in TOOL_CATALOG.tools if t.name == tool_name)
    if kind == "read":
        return await protocol._run_read(tool_name, {}, None)
    return await protocol._create_approval_via_mcp(tool_name, {}, "case-1", None)


# --- 1. 两条出口结论相同 ---------------------------------------------------


async def test_platform_admin_is_denied_on_both_surfaces(monkeypatch) -> None:
    """WP1 修的越权：admin 有角色但无 policy_qa，两条出口必须给出同一结论。

    改造前 REST 桥拒、协议出口放行并返回真实制度正文。
    """
    admin = _internal("admin")
    monkeypatch.setattr(protocol, "_principal_from_ctx", lambda _ctx: admin)

    from_protocol = await _call_protocol(admin, "search_policy")
    from_rest = await rest_bridge.call_tool("search_policy", rest_bridge.ToolCallBody(), _fake_request(admin))

    assert from_protocol["outcome"] == ToolOutcome.FORBIDDEN.value
    assert from_rest["outcome"] == ToolOutcome.FORBIDDEN.value
    # 逐字节同形 —— 拒绝响应由同一个 denial_envelope 构造，不是两份实现
    assert from_protocol == from_rest
    assert "chunks" not in from_protocol and "document" not in from_protocol


async def test_every_denied_role_tool_pair_matches_across_surfaces(monkeypatch) -> None:
    """穷举角色 × 工具，逐对比较两条出口的拒绝包。"""
    denied: set[tuple[str, str]] = set()

    for role in ROLES:
        principal = _internal(role)
        monkeypatch.setattr(protocol, "_principal_from_ctx", lambda _ctx, _p=principal: _p)
        for tool in TOOL_CATALOG.tools:
            if authorize_tool_call(principal, tool.name, catalog=TOOL_CATALOG).allowed:
                continue
            denied.add((role, tool.name))
            from_protocol = await _call_protocol(principal, tool.name)
            from_rest = await rest_bridge.call_tool(tool.name, rest_bridge.ToolCallBody(), _fake_request(principal))
            assert from_protocol == from_rest, (role, tool.name, from_protocol, from_rest)

    # 精确断言而不是"至少若干条"：策略变了就该显式改这条，而不是被阈值掩盖。
    expected = {("employee", name) for name in WRITE_TOOLS} | {
        (role, tool.name) for role in ("admin", "nobody") for tool in TOOL_CATALOG.tools
    }
    assert denied == expected


async def test_authorized_read_reaches_the_same_dispatch_from_both_surfaces(monkeypatch) -> None:
    """允许路径也必须同一条实现：两条出口都要走进同一个读派发。"""
    calls: list[tuple[str, dict[str, Any], str]] = []

    async def _record(tool_name: str, params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        calls.append((tool_name, params, tenant_id))
        return {"ok": True, "tool": tool_name, "outcome": ToolOutcome.FOUND.value}

    monkeypatch.setattr(protocol, "run_read_tool", _record)
    monkeypatch.setattr(rest_bridge, "run_read_tool", _record)
    principal = _internal("hrbp")
    monkeypatch.setattr(protocol, "_principal_from_ctx", lambda _ctx: principal)

    await protocol._run_read("search_policy", {"query": "加班费"}, None)
    await rest_bridge.call_tool(
        "search_policy", rest_bridge.ToolCallBody(arguments={"query": "加班费"}), _fake_request(principal)
    )

    assert calls == [("search_policy", {"query": "加班费"}, "tenant-a")] * 2


# --- 2. 三个维度各自能独立否决 ---------------------------------------------


def test_capability_satisfied_but_scope_missing_is_denied() -> None:
    """角色能力够，但凭据没有声明所需 scope → 拒，且原因定位到 scope 维度。"""
    principal = McpPrincipal(
        tenant_id="tenant-a",
        user_id="u-1",
        role="hrbp",
        auth_method=AuthMethod.INTERNAL,
        client_id=INTERNAL_CLIENT_ID,
        scopes=frozenset(),
    )
    decision = authorize_tool_call(principal, "search_policy", catalog=TOOL_CATALOG)
    assert decision.allowed is False
    assert decision.deny_reason is DenyReason.MISSING_SCOPE


def test_client_ceiling_narrows_a_grant_the_role_permits() -> None:
    """第三维独立生效：用户被授予的 scope 仍受客户端上限收紧。"""
    principal = _oauth(
        scopes={Scope.POLICY_READ, Scope.CASE_PROPOSE},
        ceiling={Scope.CASE_PROPOSE},
    )
    narrowed = authorize_tool_call(principal, "search_policy", catalog=TOOL_CATALOG)
    assert narrowed.allowed is False
    assert narrowed.deny_reason is DenyReason.CLIENT_CEILING
    # 同一凭据上、落在上限内的工具仍然可用 —— 上限是收紧，不是一律拒绝
    assert authorize_tool_call(principal, "create_hr_case", catalog=TOOL_CATALOG).allowed is True


def test_unknown_role_fails_closed() -> None:
    decision = authorize_tool_call(_internal("nobody"), "search_policy", catalog=TOOL_CATALOG)
    assert decision.allowed is False
    assert decision.deny_reason is DenyReason.MISSING_CAPABILITY


def test_unknown_tool_is_a_controlled_failure() -> None:
    decision = authorize_tool_call(_internal("hrbp"), "not_a_tool", catalog=TOOL_CATALOG)
    assert decision.deny_reason is DenyReason.UNKNOWN_TOOL
    assert denial_envelope(decision)["error_code"] == "UNKNOWN_TOOL"


# --- 3. 发现不是安全边界 ---------------------------------------------------


def test_tool_hidden_from_discovery_is_still_denied_at_execution() -> None:
    """把工具从清单里拿掉只是体验，执行期必须重新判定。"""
    admin = _internal("admin")
    visible, hidden = visible_tools(admin, TOOL_CATALOG)
    assert visible == []
    assert len(hidden) == len(TOOL_CATALOG.tools)

    for tool in hidden:
        decision = authorize_tool_call(admin, tool.name, catalog=TOOL_CATALOG)
        assert decision.allowed is False, tool.name

    # employee 看得见读工具、看不见写工具；看不见的那些同样在执行期被拒
    employee = _internal("employee")
    visible, hidden = visible_tools(employee, TOOL_CATALOG)
    assert {t.name for t in visible} == {t.name for t in TOOL_CATALOG.tools if t.kind.value == "read"}
    assert {t.name for t in hidden} == WRITE_TOOLS
    for tool in hidden:
        assert authorize_tool_call(employee, tool.name, catalog=TOOL_CATALOG).allowed is False


# --- 4. 写工具永远只创建审批 -------------------------------------------------


async def test_denied_write_never_opens_a_tenant_session(monkeypatch) -> None:
    """被拒的写工具连租户会话都不该开启 —— 拒绝要在任何副作用之前。"""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("被拒的写工具不应该开启租户会话")

    monkeypatch.setattr("app.data.database.tenant_session", _boom)
    monkeypatch.setattr(protocol, "_principal_from_ctx", lambda _ctx: _internal("employee"))

    result = await protocol._create_approval_via_mcp("create_hr_case", {"title": "工单"}, "case-1", None)

    assert result["outcome"] == ToolOutcome.FORBIDDEN.value


async def test_allowed_write_only_creates_an_approval_request(monkeypatch) -> None:
    """写工具唯一的副作用是建一条待审批记录，且文案不得暗示已经执行。"""
    recorded: list[dict[str, Any]] = []

    class _FakeService:
        def __init__(self, _session: Any, tenant_id: str, actor: str) -> None:
            self.tenant_id = tenant_id
            self.actor = actor

        async def request_approval(self, case_id: str, *, tool_name: str, params: dict[str, Any]) -> Any:
            recorded.append({"case_id": case_id, "tool_name": tool_name, "params": params, "actor": self.actor})
            return SimpleNamespace(id="AP-77", status="PENDING")

    class _FakeSession:
        committed = False

        async def commit(self) -> None:
            self.committed = True

    fake_session = _FakeSession()

    @asynccontextmanager
    async def _session_factory(_tenant_id: str) -> Any:
        yield fake_session

    monkeypatch.setattr("app.data.database.tenant_session", _session_factory)
    monkeypatch.setattr("app.scenarios.hr_case_agent.service.HRCaseService", _FakeService)
    monkeypatch.setattr(protocol, "_principal_from_ctx", lambda _ctx: _internal("hrbp"))

    result = await protocol._create_approval_via_mcp(
        "create_hr_case",
        {"title": "加班费争议", "subject_ref": "EMP-1", "category": "overtime"},
        "case-1",
        None,
    )

    assert len(recorded) == 1
    assert recorded[0]["tool_name"] == "create_hr_case"
    assert recorded[0]["case_id"] == "case-1"
    assert recorded[0]["actor"] == "user:u-hrbp|role:hrbp"
    assert result["outcome"] == ToolOutcome.AWAITING_APPROVAL.value
    assert result["approval_id"] == "AP-77"
    assert fake_session.committed is True
    # 不得把"已提交审批"说成"已完成业务动作"
    assert "已提交" in result["user_message"]
    assert "批准后才会真正执行" in result["user_message"]


# --- 5. 拒绝响应不泄漏 -----------------------------------------------------


def test_denial_envelope_leaks_neither_reason_nor_scope() -> None:
    """外部只拿到 outcome 与既有中文文案；原因进日志，不进响应。"""
    decision = authorize_tool_call(_internal("admin"), "search_policy", catalog=TOOL_CATALOG)
    body = denial_envelope(decision, tenant_id="tenant-a")

    assert body["ok"] is False
    assert body["outcome"] == ToolOutcome.FORBIDDEN.value
    assert body["user_message"] == USER_MESSAGES[ToolOutcome.FORBIDDEN]
    for leaked in ("deny_reason", "reason", "required_scope", "required_capability", "policy_version"):
        assert leaked not in body


def test_denial_envelope_refuses_to_wrap_an_allowed_decision() -> None:
    """误用要么立刻炸，要么就会变成"允许被当成拒绝"的静默 bug。"""
    allowed = authorize_tool_call(_internal("hrbp"), "search_policy", catalog=TOOL_CATALOG)
    assert allowed.allowed is True
    try:
        denial_envelope(allowed)
    except ValueError:
        pass
    else:  # pragma: no cover - 只有断言失败才会走到
        raise AssertionError("denial_envelope 必须拒绝处理已允许的决策")


# --- 6. 明确"尚未评估"的维度 -----------------------------------------------


def test_object_acl_is_explicitly_marked_as_deferred() -> None:
    """决策不声称对象级权限已校验 —— 它在业务执行层（app/access/object_scope.py）。"""
    allowed = authorize_tool_call(_internal("hrbp"), "search_policy", catalog=TOOL_CATALOG)
    assert allowed.object_acl == OBJECT_ACL_DEFERRED


# --- 7. 结构性不变式 -------------------------------------------------------


def test_every_tool_scope_is_reachable_from_its_capability() -> None:
    """工具声明的 scope 必须是"持有该能力的角色"能拿到的取值。

    否则这个工具对**所有人**恒不可达，而且只有读代码才发现 —— 属于死锁级缺陷。
    """
    for tool in TOOL_CATALOG.tools:
        reachable = CAPABILITY_SCOPES.get(tool.required_capability, frozenset())
        assert tool.required_scope in reachable, (tool.name, tool.required_capability, tool.required_scope)


def test_scope_auth_method_marker_matches_the_principal_enum() -> None:
    """标记常量与枚举不许漂移：一旦不等，REST 桥会静默开始拒绝所有登录用户。"""
    assert AuthMethod.INTERNAL.value == SCOPE_AUTH_METHOD_INTERNAL


def test_rest_bridge_refuses_credentials_it_cannot_represent_fully() -> None:
    """REST 桥只拿得到 claim 的三个字段，无法表达客户端与授权上限。

    所以它对任何**非自签**来源一律返回 None（下游按 AUTH_REQUIRED 处理），
    而不是把外部凭据当自签处理 —— 那会静默丢掉客户端授权上限，
    让这条桥重新变成最弱的一环。
    """
    scope_auth = {"user_id": "u-1", "role": "hrbp", "tenant_id": "tenant-a"}
    request = SimpleNamespace(scope={"auth": dict(scope_auth)}, state=SimpleNamespace(tenant_id="tenant-a"))

    # 完全没有来源标记：不放行（不靠"没有标记就默认自签"）
    assert rest_bridge._principal(request) is None

    # 明确是外部凭据：不放行
    request.scope["auth"]["auth_method"] = AuthMethod.OAUTH.value
    assert rest_bridge._principal(request) is None

    # 明确是自签凭据：正常构造
    request.scope["auth"]["auth_method"] = SCOPE_AUTH_METHOD_INTERNAL
    principal = rest_bridge._principal(request)
    assert principal is not None
    assert principal.auth_method is AuthMethod.INTERNAL


async def test_anonymous_read_returns_no_business_data(monkeypatch) -> None:
    monkeypatch.setattr(protocol, "_principal_from_ctx", lambda _ctx: None)
    result = await protocol._run_read("search_policy", {"query": "加班费", "top_k": 3}, None)

    assert result["outcome"] == ToolOutcome.AUTH_REQUIRED.value
    assert "chunks" not in result
    assert "document" not in result
