"""MCP 工具调用的**唯一**授权决策。

为什么需要它
------------
2026-09-13 的核验发现两条 MCP 出口的判定不一致：

- ``/mcp``（MCP 协议出口）的**读**工具完全不做角色校验 —— 只要 JWT 有效，
  ``admin`` 角色也能拿到真实制度正文；
- ``/api/mcp``（REST 桥）对同一批工具做 ``tool_allowed`` 校验，``admin`` 被拒。

后果是可复现的：``ROLE_CAPABILITIES["admin"]`` 不持有 ``policy_qa``，
而两个读工具要求的正是 ``policy_qa``。同一个人、同一份权限，换个出口结论相反。

根因不是某处漏写，而是"判定"被执行了两遍。所以这里只留**一个**函数
``authorize_tool_call``，发现侧与执行侧、协议出口与 REST 桥全部调用它；拒绝时
也由同一个 ``denial_envelope`` 构造响应，保证两条出口的拒绝包逐字节同形。

判定顺序与 fail-closed
----------------------
顺序不是随意排的 —— 它决定**拒绝原因**的准确度（原因只进日志与审计，不进外部
响应，见 ``denial_envelope``）：

1. 工具不在目录里 → 未知工具；
2. 没有身份 → 需要认证；
3. 角色能力不含所需能力；
4. 凭据声明的 scope 不含所需 scope；
5. **有效** scope（= 声明 scope ∩ 客户端上限）不含所需 scope → 客户端授权上限拒绝。

第 3、4、5 步互斥地定位到三个不同维度，这样审计里能直接看出"是人不够、还是
这个客户端不够"，而不是笼统一条 FORBIDDEN。

刻意**不**在这里做的事
----------------------
对象级 ACL（"这个案件是不是他有权看的"）不在此评估。它需要一次数据库读取
（所有人 / 上级组织范围），无法对主体做纯函数判定，项目既有约定是放在
``app/access/object_scope.py`` + ``HRCaseService`` 执行期再校验。方案 §3.1 的权限
交集公式里确实包含 object_acl，本决策因此**显式标注**该维度尚未评估
（``ToolAuthorization.object_acl``），而不是默认它已经过了。WP7 引入案件工具时，
判定要接在这个字段上，而不是新写一条路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.access.policies.contracts import ToolCatalog, ToolDefinition
from app.access.policies.tool_access import capabilities_for_role
from app.access.scopes import Scope
from app.mcp.auth.principal import McpPrincipal
from app.mcp.contract import ToolOutcome, envelope, failure_envelope
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 决策规则自身的版本。授权判定变更时可追溯"当时用的是哪一版规则"。
POLICY_VERSION = "mcp-authz-v1"

#: 对象级 ACL 在本决策中不评估。取值即说明，避免被读成"已通过"。
OBJECT_ACL_DEFERRED = "must_be_rechecked_at_execution"


class DenyReason(StrEnum):
    """拒绝原因的稳定内部代码。**只进日志与审计，不进外部响应。**"""

    UNKNOWN_TOOL = "unknown_tool"
    ANONYMOUS = "anonymous"
    MISSING_CAPABILITY = "missing_capability"
    MISSING_SCOPE = "missing_scope"
    CLIENT_CEILING = "client_ceiling"


@dataclass(frozen=True, slots=True)
class ToolAuthorization:
    """一次授权决策的结果。允许时只需要看 ``allowed``。"""

    allowed: bool
    tool_name: str
    required_capability: str | None = None
    required_scope: Scope | None = None
    deny_reason: DenyReason | None = None
    deny_outcome: ToolOutcome | None = None
    deny_error_code: str | None = None
    policy_version: str = POLICY_VERSION
    object_acl: str = OBJECT_ACL_DEFERRED


def _allow(tool: ToolDefinition) -> ToolAuthorization:
    return ToolAuthorization(
        allowed=True,
        tool_name=tool.name,
        required_capability=tool.required_capability,
        required_scope=tool.required_scope,
    )


def _deny(
    tool_name: str,
    *,
    reason: DenyReason,
    outcome: ToolOutcome,
    tool: ToolDefinition | None = None,
    error_code: str | None = None,
) -> ToolAuthorization:
    return ToolAuthorization(
        allowed=False,
        tool_name=tool_name,
        required_capability=tool.required_capability if tool else None,
        required_scope=tool.required_scope if tool else None,
        deny_reason=reason,
        deny_outcome=outcome,
        deny_error_code=error_code,
    )


def summarize(authorization: ToolAuthorization) -> str:
    """日志用的一行摘要。拒绝原因需要它，但不需要一个完整的结构化 payload。"""
    return authorization.deny_reason.value if authorization.deny_reason else "allowed"


def log_denial(authorization: ToolAuthorization, principal: McpPrincipal | None, *, surface: str) -> None:
    """把拒绝写进日志。

    ``surface`` 区分是协议出口还是 REST 桥 —— WP1 修掉的那处越权正是"两条出口
    结论不同"，把它记进日志才能在下一次回归时一眼看出。
    """
    logger.warning(
        "mcp_tool_denied",
        surface=surface,
        tool=authorization.tool_name,
        reason=summarize(authorization),
        policy_version=authorization.policy_version,
        role=principal.role if principal else None,
        tenant_id=principal.tenant_id if principal else None,
        auth_method=principal.auth_method.value if principal else None,
        client_id=principal.client_id if principal else None,
    )


def authorize_tool_call(
    principal: McpPrincipal | None,
    tool_name: str,
    *,
    catalog: ToolCatalog,
) -> ToolAuthorization:
    """判定一次 MCP 工具调用是否放行。纯函数，无 IO —— 因此可以被穷举测试。"""
    tool = next((candidate for candidate in catalog.tools if candidate.name == tool_name), None)
    if tool is None:
        return _deny(
            tool_name,
            reason=DenyReason.UNKNOWN_TOOL,
            outcome=ToolOutcome.FAILED,
            error_code="UNKNOWN_TOOL",
        )

    if principal is None:
        return _deny(tool_name, reason=DenyReason.ANONYMOUS, outcome=ToolOutcome.AUTH_REQUIRED, tool=tool)

    if tool.required_capability not in capabilities_for_role(principal.role):
        return _deny(tool_name, reason=DenyReason.MISSING_CAPABILITY, outcome=ToolOutcome.FORBIDDEN, tool=tool)

    required_scope = tool.required_scope
    if required_scope not in principal.scopes:
        return _deny(tool_name, reason=DenyReason.MISSING_SCOPE, outcome=ToolOutcome.FORBIDDEN, tool=tool)
    if required_scope not in principal.effective_scopes:
        return _deny(tool_name, reason=DenyReason.CLIENT_CEILING, outcome=ToolOutcome.FORBIDDEN, tool=tool)

    return _allow(tool)


def denial_envelope(authorization: ToolAuthorization, **extra: Any) -> dict[str, Any]:
    """把**被拒**的决策构造成对外信封。

    外部响应只暴露 outcome 与既有中文文案：不放 ``deny_reason``，不放缺失的 scope，
    也不区分"对象不存在"与"对象无权限"。原因已经进日志（``log_denial``），
    要机器可读的 scope 挑战属于传输层（WP2 的 403 + 标准错误体），不在业务信封里
    造一个将来要被取代的半成品。
    """
    if authorization.allowed:
        raise ValueError("denial_envelope called for an allowed decision")
    if authorization.deny_error_code is not None:
        return failure_envelope(authorization.tool_name, authorization.deny_error_code, **extra)
    return envelope(authorization.tool_name, authorization.deny_outcome or ToolOutcome.FAILED, **extra)


def visible_tools(
    principal: McpPrincipal | None,
    catalog: ToolCatalog,
) -> tuple[list[ToolDefinition], list[ToolDefinition]]:
    """按授权决策切分工具目录（可见, 不可见）。

    与 ``app.access.policies.tool_access.partition_tools`` 的分工：那个回答"这个
    **角色**能看见什么"（只有角色一个维度，用于能力矩阵与文档）；这个回答"这个
    **凭据/安装实例**能看见什么"（角色 ∩ scope ∩ 客户端上限）。MCP 的发现接口用后者，
    因为客户端拿到的清单必须与它执行时会被允许的范围一致 —— 否则会出现
    "看得见却调不动"，而调用失败的原因在清单里看不出来。

    两者共用 ``authorize_tool_call``，所以不会各自漂移。
    """
    visible: list[ToolDefinition] = []
    hidden: list[ToolDefinition] = []
    for tool in catalog.tools:
        decision = authorize_tool_call(principal, tool.name, catalog=catalog)
        (visible if decision.allowed else hidden).append(tool)
    return visible, hidden
