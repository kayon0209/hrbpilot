"""工具可见性 —— "谁能看到 / 谁能调用哪些工具"的唯一判据。

为什么需要它
------------
``required_capability`` 早就写在工具目录里，而且执行侧真的会校验
（``app/access/policies/hr_case.py`` 判能力、``app/outbox/dispatcher.py`` 二次复核）。
但**发现侧**从来没有用过它：``/api/mcp/capabilities`` 与 ``/api/mcp/tools``
把整份目录原样返回给任何已登录用户。结果是这个桥接层比它背后的执行链更宽松
—— 平台管理员看不出自己其实不持有 HR 业务能力，而一个只读角色能看见一堆
自己调不动的写工具。

这里把"能力 → 工具"的判定收敛成一处，发现侧与调用侧共用，避免两边各写一份
分支而漂移。
"""

from __future__ import annotations

from app.access.middleware.rbac import ROLE_CAPABILITIES
from app.access.policies.contracts import ToolCatalog, ToolDefinition

#: 当前角色不持有工具所需能力时的统一说明（面向用户，不含技术名词）。
MISSING_CAPABILITY_REASON = "当前角色不持有这项能力所需的业务权限"


def capabilities_for_role(role: str | None) -> frozenset[str]:
    """角色 → 能力集合。未知/空角色返回空集合（fail-closed）。"""
    if not role:
        return frozenset()
    return frozenset(ROLE_CAPABILITIES.get(role, ()))


def tool_allowed(tool: ToolDefinition, role: str | None) -> bool:
    return tool.required_capability in capabilities_for_role(role)


def partition_tools(catalog: ToolCatalog, role: str | None) -> tuple[list[ToolDefinition], list[ToolDefinition]]:
    """按角色把目录切成 (可见, 不可见)。顺序与目录一致，便于前端稳定渲染。"""
    visible: list[ToolDefinition] = []
    hidden: list[ToolDefinition] = []
    for tool in catalog.tools:
        (visible if tool_allowed(tool, role) else hidden).append(tool)
    return visible, hidden
