"""``get_my_access_profile``：把"这份凭据能做什么"告诉调用者。

为什么值得一个工具
------------------
外部 Agent 拿到的是一份被剪裁过的工具清单。它需要能回答"为什么 `search_policy`
在我这里不见了"这类问题 —— 否则用户的排查路径是"换个 Agent 试试"，而真正的原因
（角色没有该能力 / 凭据没申请该 scope / 客户端上限不含它）永远浮不出来。

方案 §WP4 把它列为只读闭环的一部分，scope 是 ``hrb:profile:read``。

返回什么、不返回什么
--------------------
**返回**：当前身份的**安全摘要**、有效 scope、可用的工具名。

**不返回**：内部权限结构。具体地说，"你这个角色理论上能做哪些事"不返回 ——
那会把角色到能力的完整映射交给外部模型，而它对用户没有可操作性（用户改变不了
自己的角色）。只返回**当前这份凭据实际能用的**，即三个维度的交集结果。

这一点不是洁癖：``ROLE_CAPABILITIES`` 是一张内部配置表，泄漏它等于把"这个系统
里有哪些能力位"变成公开知识，而那正是越权尝试的输入。
"""

from __future__ import annotations

from typing import Any

from app.access.policies.contracts import ToolCatalog
from app.access.policies.tool_access import capabilities_for_role
from app.mcp.auth.authorization import visible_tools
from app.mcp.auth.principal import McpPrincipal


def access_profile(principal: McpPrincipal | None, catalog: ToolCatalog) -> dict[str, Any]:
    """当前凭据的身份摘要与可用范围。``principal`` 为 ``None`` 时返回"未认证"。"""
    if principal is None:
        # 匿名不是错误，是"没有作用范围"。返回结构一致的摘要而不是抛异常 ——
        # 调用方拿到的字段集合不随认证状态变化，少一类分支少一类漏判。
        return {
            "authenticated": False,
            "user_message": "当前调用没有携带身份，因此没有可用的权限范围。请先完成授权。",
        }

    visible, hidden = visible_tools(principal, catalog)
    return {
        "authenticated": True,
        # 刻意**不**返回 tenant_id：信封本体已经带着它，重复返回会让
        # ``envelope()`` 收到重复关键字而整条调用崩掉（真实发生过）。工具 payload
        # 与信封固定字段的重名是一条静默的故障路径 —— 见 read_dispatch._RESERVED_KEYS。
        "user_id": principal.user_id,
        "role": principal.role,
        "auth_method": principal.auth_method.value,
        "client_id": principal.client_id,
        "installation_id": str(principal.installation_id) if principal.installation_id else None,
        # 凭据声明与**有效** scope 分开列出。两者不同时的差值就是客户端上限在起作用，
        # 而这正是"为什么申请了却用不了"的答案 —— 混成一个集合就看不出来了。
        "scopes": sorted(scope.value for scope in principal.scopes),
        "effective_scopes": sorted(scope.value for scope in principal.effective_scopes),
        # 只回工具名，不回 tool→capability 映射：那是内部结构。
        "available_tools": sorted(tool.name for tool in visible),
        "unavailable_tool_count": len(hidden),
        "user_message": _summary_message(principal, available=len(visible)),
    }


def _summary_message(principal: McpPrincipal, *, available: int) -> str:
    """面向用户的一句话。不出现能力位、scope 名、策略版本等内部术语。"""
    return f"当前以「{principal.role}」身份接入，来自客户端「{principal.client_id}」，可用工具 {available} 个。"


def role_capability_names(principal: McpPrincipal) -> list[str]:
    """当前角色的能力位名称。**仅供内部审计**，不进工具响应。

    单独抽出来是为了有一个明确的地方说明"这个函数不对外"：审计需要它来回答
    "当时他有哪些能力位"，而外部响应需要它缺席。
    """
    return sorted(capabilities_for_role(principal.role))
