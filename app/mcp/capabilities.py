"""单一 capability manifest —— 唯一真相源（文档 §6.1、§6.10）。

为什么值得单独一个模块
----------------------
发现清单、授权三重交集、连接诊断、WorkBuddy Skill 是同一个事实的四种视图，
而"事实"在代码里是散的：``ROLE_CAPABILITIES``（rbac）、``CAPABILITY_SCOPES``
（scopes）、``TOOL_CATALOG``（原子工具）、``AGENT_TASK_CATALOG``（任务工具）、
``TASK_TYPES``（意图编译器）、``TOOL_DESCRIPTIONS``/``TASK_TOOL_DESCRIPTIONS``
（路由描述）。任何一处加了工具却漏了描述（或反过来），路由与权限就静默漂移。

manifest 把它们拼成**一份**机器可读文档：

- task bundle（普通 HR 默认）：7+2 高频工具；
- atomic bundle（高级/兼容端点）：全部原子工具；
- permissions：角色 × bundle 的能力矩阵（含 scope 与 ask_order）；
- scope_packages：连接时两档套餐（仅查询 / 查询+办理）；
- evidence：生成时的目录版本与描述版本，用于漂移校验与评测对账。

``assert_manifest_is_current()`` 是漂移校验：目录与描述的任何不一致都让
测试失败，而不是让客户端在运行时才发现"多了一个调不动的工具"。
"""

from __future__ import annotations

import json
from typing import Any

from app.access.middleware.rbac import ROLE_CAPABILITIES
from app.access.policies.tool_access import capabilities_for_role
from app.access.scopes import Scope
from app.mcp import task_descriptions, tool_descriptions
from app.scenarios.agent_tasks.intent import FIELD_ASK_ORDER, TASK_TYPES
from app.scenarios.agent_tasks.tools import AGENT_TASK_CATALOG
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG

#: 对外契约版本。manifest 结构变化时加一；测试按此断言。
MANIFEST_VERSION = "2026-09-15.1"

#: 两档授权套餐（scope 包）：连接时用户只选档，不逐个勾 scope。
#: 「查询+办理」显式包含写权限 —— 写权限永远不默认授予。
SCOPE_PACKAGES: dict[str, dict[str, Any]] = {
    "query_only": {
        "label": "仅查询",
        # `description` 是给界面用的**结果描述**，不是 scope 列表的翻译：用户要判断的是
        # "这个助手能对我的数据做什么"，所以写结果（会不会改动数据），不写权限名。
        # 前端直接渲染它 —— 曾用 key 名猜文案（`key.includes("propose")`），
        # 那样改一次键名就会静默配错说明。
        "description": "只查制度、案件和审批状态，不会改动任何数据。",
        "scopes": sorted(
            {
                Scope.PROFILE_READ.value,
                Scope.POLICY_READ.value,
                Scope.CASE_READ.value,
                Scope.APPROVAL_READ.value,
            }
        ),
    },
    "query_and_propose": {
        "label": "查询 + 发起办理申请",
        # 刻意不写"提交办理建议"：那会让人以为只是产出一段文字，而实际上会**创建一条
        # 正式的审批申请**进入 HR 的待办。措辞必须与实际后果一致。
        "description": "可以生成办理草稿并提交审批，但不会直接修改案件、任务或通知。",
        "scopes": sorted(
            {
                Scope.PROFILE_READ.value,
                Scope.POLICY_READ.value,
                Scope.CASE_READ.value,
                Scope.APPROVAL_READ.value,
                Scope.CASE_PROPOSE.value,
            }
        ),
    },
}

BUNDLE_TASK = "task"
BUNDLE_ATOMIC = "atomic"


def build_manifest() -> dict[str, Any]:
    atomic = sorted(tool.name for tool in TOOL_CATALOG.tools)
    task = sorted(tool.name for tool in AGENT_TASK_CATALOG.tools)
    roles = sorted({*ROLE_CAPABILITIES, "hrbp", "hr_manager", "employee", "admin"})
    return {
        "version": MANIFEST_VERSION,
        "bundles": {
            BUNDLE_TASK: {
                "tools": task,
                "entrypoint": "/mcp/tasks",
                "task_types": sorted(TASK_TYPES),
                "ask_order": list(FIELD_ASK_ORDER),
            },
            BUNDLE_ATOMIC: {
                "tools": atomic,
                "entrypoint": "/mcp",
                "note": "高级/兼容端点：保留 11 个原子工具，直接走审批 gate。",
            },
        },
        "capabilities": {
            role: {bundle: sorted(_visible_names(role, bundle)) for bundle in (BUNDLE_TASK, BUNDLE_ATOMIC)}
            for role in roles
            if role in ROLE_CAPABILITIES
        },
        "scope_packages": SCOPE_PACKAGES,
        "evidence": {
            "atomic_catalog_version": TOOL_CATALOG.version,
            "task_catalog_version": AGENT_TASK_CATALOG.version,
            "descriptions_version": tool_descriptions.DESCRIPTIONS_VERSION,
            "task_descriptions_version": task_descriptions.TASK_DESCRIPTIONS_VERSION,
        },
    }


def _visible_names(role: str, bundle: str) -> list[str]:
    caps = capabilities_for_role(role)
    catalog = AGENT_TASK_CATALOG if bundle == BUNDLE_TASK else TOOL_CATALOG
    return sorted(tool.name for tool in catalog.tools if tool.required_capability in caps)


def assert_manifest_is_current() -> None:
    """漂移校验：目录 ↔ 描述 ↔ 意图类型 ↔ manifest 完全对齐。

    任一"描述没有对应工具"或"工具有却没描述"都直接抛错 —— 这种漂移在运行时
    表现为"Agent 选了个不存在的工具"或"有个工具永远选不到"，只能在生成侧拦。
    """
    from app.scenarios.hr_case_agent.tools import ToolError  # noqa: F401

    atomic_names = {tool.name for tool in TOOL_CATALOG.tools}
    task_names = {tool.name for tool in AGENT_TASK_CATALOG.tools}
    if set(tool_descriptions.TOOL_DESCRIPTIONS) != atomic_names:
        missing = atomic_names ^ set(tool_descriptions.TOOL_DESCRIPTIONS)
        raise AssertionError(f"atomic catalog/descriptions drift: {sorted(missing)}")
    if set(task_descriptions.TASK_TOOL_DESCRIPTIONS) != task_names:
        missing = task_names ^ set(task_descriptions.TASK_TOOL_DESCRIPTIONS)
        raise AssertionError(f"task catalog/descriptions drift: {sorted(missing)}")
    if set(TASK_TYPES) - {
        "case_followup",
        "probation_followup",
        "case_owner_change",
        "case_resolve",
        "case_notification",
    }:
        raise AssertionError("unknown task_type leaked into manifest")
    if len(task_names) != len(AGENT_TASK_CATALOG.tools):
        raise AssertionError("task catalog has duplicate tool names")
    manifest = build_manifest()
    if manifest["bundles"][BUNDLE_TASK]["tools"] != sorted(task_names):
        raise AssertionError("manifest task bundle is stale")
    if manifest["bundles"][BUNDLE_ATOMIC]["tools"] != sorted(atomic_names):
        raise AssertionError("manifest atomic bundle is stale")


def manifest_json(*, indent: int = 2) -> str:
    return json.dumps(build_manifest(), ensure_ascii=False, indent=indent, sort_keys=True)
