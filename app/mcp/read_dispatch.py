"""读工具的统一派发 —— MCP 协议出口与 REST 桥共用**同一条**实现。

为什么需要它
------------
同一个 `get_policy_source` 曾经有两套实现：agent loop 走
``read_executors.execute_get_policy_source``（真查 Document/DocumentChunk 并
返回正文），而 MCP 出口与 REST 桥只做参数校验就返回一句 ``note``。于是用户在
页面上照着示例发问，拿到的是"已按白名单校验参数"——**承诺超过了实现**。

这里把两个出口都收敛到 `run_read_tool`，杜绝再长出第二条路径。
租户上下文用 `read_context.bind_read_tenant` 绑定：executor 的签名是
``(params) -> dict``，没有地方传租户，contextvar 是其既有的、并发安全的载体。
"""

from __future__ import annotations

from typing import Any

from app.mcp.contract import ToolOutcome, envelope, failure_envelope, read_outcome
from app.scenarios.hr_case_agent.read_context import bind_read_tenant, reset_read_tenant
from app.scenarios.hr_case_agent.read_executors import READ_TOOL_EXECUTORS
from app.scenarios.hr_case_agent.tools import ToolError, validate_tool_call
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 信封固定字段，工具自身 payload 不得覆盖。
_RESERVED_KEYS = frozenset({"ok", "tool", "outcome", "user_message", "error_code"})


async def run_read_tool(tool_name: str, params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    """校验 → 执行 → 统一信封。读工具只有这一条路径。"""
    try:
        validated = validate_tool_call(tool_name, params)
    except ToolError as e:
        return failure_envelope(tool_name, e.code)

    executor = READ_TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        return failure_envelope(tool_name, "UNKNOWN_TOOL")

    token = bind_read_tenant(tenant_id)
    try:
        payload = await executor(validated)
    except ToolError as e:
        # 受控失败码：文案来自 contract.FAILURE_MESSAGES，原始 message 只进日志。
        logger.warning("mcp_read_tool_failed", tool=tool_name, code=e.code, tenant_id=tenant_id)
        return failure_envelope(tool_name, e.code, validated_params=validated)
    except Exception:
        logger.exception("mcp_read_tool_crashed", tool=tool_name, tenant_id=tenant_id)
        return failure_envelope(tool_name, "INTERNAL_ERROR", validated_params=validated)
    finally:
        reset_read_tenant(token)

    extras = {k: v for k, v in payload.items() if k not in _RESERVED_KEYS}
    return envelope(
        tool_name,
        read_outcome(tool_name, payload),
        validated_params=validated,
        tenant_id=tenant_id,
        **extras,
    )


def anonymous_read_envelope(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """未携带身份时的读调用：只校验参数，明确说明为什么没有真实数据。

    刻意**不**做匿名检索 —— 没有身份就没有作用范围，任何返回都可能是别的
    单位的内容。
    """
    try:
        validated = validate_tool_call(tool_name, params)
    except ToolError as e:
        return failure_envelope(tool_name, e.code)
    return envelope(
        tool_name,
        ToolOutcome.AUTH_REQUIRED,
        validated_params=validated,
        note="已校验参数，但未读取任何真实数据：需要登录并携带身份凭证。",
    )
