"""方案 §WP4：``get_my_access_profile``。

这个工具的作用是把"为什么某个工具在我这里不见了"变成一个**可回答的问题**。
因此测试的重点不是字段是否齐全，而是两条边界：

1. 它**不**泄漏内部权限结构（角色 → 能力的完整映射）；
2. 它**不**因为拿不到主体就降级成"没有限制"。
"""

from __future__ import annotations

from app.access.scopes import Scope
from app.mcp.auth import AuthMethod, McpPrincipal
from app.mcp.profile import access_profile, role_capability_names
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG


def _principal(
    role: str = "hrbp",
    *,
    scopes: frozenset[Scope] | None = None,
    ceiling: frozenset[Scope] | None = None,
) -> McpPrincipal:
    return McpPrincipal(
        tenant_id="tenant-a",
        user_id="user-1",
        role=role,
        auth_method=AuthMethod.OAUTH,
        client_id="workbuddy",
        installation_id="2f0282cd-5639-41b1-a36f-fb4b1dc0e567",  # type: ignore[arg-type]
        scopes=scopes if scopes is not None else frozenset({Scope.POLICY_READ, Scope.CASE_PROPOSE}),
        client_ceiling=ceiling if ceiling is not None else frozenset({Scope.POLICY_READ}),
        token_id="jti-1",
    )


def test_an_anonymous_caller_gets_a_statement_not_a_default_profile() -> None:
    """匿名必须被明确告知"没有作用范围"，而不是拿到一份看起来正常的摘要。"""
    profile = access_profile(None, TOOL_CATALOG)
    assert profile["authenticated"] is False
    assert "scopes" not in profile
    assert "available_tools" not in profile


def test_declared_and_effective_scopes_are_reported_separately() -> None:
    """两者的差值就是客户端上限在起作用 —— 混成一个集合就看不出来了。"""
    profile = access_profile(_principal(), TOOL_CATALOG)
    assert profile["scopes"] == ["hrb:case:propose", "hrb:policy:read"]
    assert profile["effective_scopes"] == ["hrb:policy:read"]


def test_it_never_exposes_the_role_to_capability_map() -> None:
    """``ROLE_CAPABILITIES`` 是一张内部配置表。泄漏它等于把"系统里有哪些能力位"
    变成公开知识，而那正是越权尝试的输入。"""
    profile = access_profile(_principal(), TOOL_CATALOG)
    serialized = str(profile)
    for leaked in ("policy_qa", "hr_case", "work_summary", "self_profile", "kb_management"):
        assert leaked not in serialized, f"权限摘要泄漏了能力位 {leaked}"


def test_it_reports_tools_the_credential_can_actually_use() -> None:
    """上限收窄到 policy:read 时，写工具不该出现在"可用"里。"""
    profile = access_profile(_principal(), TOOL_CATALOG)
    assert "search_policy" in profile["available_tools"]
    assert "create_hr_case" not in profile["available_tools"]


def test_it_counts_what_it_hides() -> None:
    """给数量不给名字：界面能解释"为什么比别人少"，但不把无权限的工具名当可发现信息。"""
    profile = access_profile(_principal(), TOOL_CATALOG)
    assert profile["unavailable_tool_count"] > 0
    assert "create_hr_case" not in str(profile)


def test_the_user_facing_message_has_no_internal_terms() -> None:
    """面向用户的一句话里不该出现 scope 名、能力位、策略版本。"""
    profile = access_profile(_principal(), TOOL_CATALOG)
    message = profile["user_message"]
    assert "hrb:" not in message
    assert "scope" not in message.lower()
    assert "capability" not in message.lower()


def test_the_profile_does_not_return_envelope_owned_keys() -> None:
    """工具 payload 不得返回信封自有的字段。

    端到端抓到的真事故：``access_profile`` 原本返回 ``tenant_id``，而
    ``run_read_tool`` 也把 ``tenant_id`` 作为关键字传给 ``envelope`` ——
    Python 直接抛 "got multiple values for keyword argument"，整条读调用变成 500，
    而错误信息指向 ``envelope`` 而不是那个多返回了一个字段的工具。单测测不到，因为
    单测直接调 ``access_profile``，不经过 ``envelope``。

    这里把不变式写成断言：只要新增字段撞上信封的固定字段集合，这条就会红。
    """
    from app.mcp.read_dispatch import _RESERVED_KEYS

    profile = access_profile(_principal(), TOOL_CATALOG)
    collisions = set(profile) & _RESERVED_KEYS
    assert not collisions, f"profile 返回了信封自有字段：{sorted(collisions)}"


def test_role_capability_names_is_the_internal_view() -> None:
    """它存在，但**只**给审计用 —— 这份测试同时说明它不该被塞进工具响应。"""
    names = role_capability_names(_principal())
    assert "hr_case" in names
    assert "hr_case" not in str(access_profile(_principal(), TOOL_CATALOG))
