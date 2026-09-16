"""T3 工具契约回归 —— annotations / 集中描述 / 可自纠错的错误。

锁定三件事（任务书 2026-09-15 T3 验收）：

1. **每个 MCP 工具都必须有 annotations**，且写工具 ``read_only_hint=False``
   —— annotations 是客户端（Codex/WorkBuddy）展示与确认风险的依据，
   新增工具漏配会让客户端把"要审批的写操作"当无害查询对待。
2. **描述集中且三段式**（何时用/何时不用/例子）—— 描述是路由依据，散落在
   装饰器里手写等于把路由行为变成不可 diff 的暗改。
3. **失败信封可自纠错**：``fix``（怎么改）与 ``retryable``（重试是否有意义），
   参数校验失败必须带上"哪个字段、什么约束"的 detail，而不是裸 INVALID_PARAMS。
"""

from __future__ import annotations

from app.mcp.contract import FAILURE_HINTS, failure_envelope
from app.mcp.read_dispatch import run_read_tool
from app.mcp.server import mcp_server
from app.mcp.tool_descriptions import DESCRIPTIONS_VERSION, TOOL_DESCRIPTIONS
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, validate_tool_call

READ_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "read")
WRITE_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "write")


def test_every_registered_tool_has_annotations() -> None:
    """不变式：注册进 MCP server 的每个工具都带 annotations。"""
    tools = mcp_server._tool_manager.list_tools()
    assert tools, "MCP server 未注册任何工具"
    missing = [t.name for t in tools if t.annotations is None]
    assert missing == [], f"漏配 annotations 的工具: {missing}"


def test_write_tools_declare_not_read_only_and_conservative_risk() -> None:
    """写工具必须 read_only_hint=False + destructive_hint=True（保守取值）。"""
    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    for name in WRITE_TOOL_NAMES:
        a = tools[name].annotations
        assert a.read_only_hint is False, f"{name} 是写工具，read_only_hint 必须为 False"
        assert a.destructive_hint is True, f"{name} 副作用在人工审批后才发生，destructive 取保守 True"
    for name in READ_TOOL_NAMES:
        a = tools[name].annotations
        assert a.read_only_hint is True, f"{name} 是读工具，read_only_hint 必须为 True"
        assert a.destructive_hint is False


def test_every_tool_has_open_world_false_and_structured_output() -> None:
    """全部工具只与本公司系统交互；全部带结构化输出 schema。"""
    tools = mcp_server._tool_manager.list_tools()
    for t in tools:
        assert t.annotations.open_world_hint is False, f"{t.name} open_world_hint 必须为 False"
        assert t.output_schema, f"{t.name} 未启用结构化输出（output_schema 为空）"


def test_descriptions_are_centralized_and_three_part() -> None:
    """描述来自集中文件且含三段式结构；catalog 工具与描述一一对应。"""
    catalog_names = {t.name for t in TOOL_CATALOG.tools}
    assert set(TOOL_DESCRIPTIONS) == catalog_names, "描述文件与 TOOL_CATALOG 的工具集合不一致"
    for name, desc in TOOL_DESCRIPTIONS.items():
        assert "何时用" in desc, f"{name} 描述缺「何时用」"
        assert "何时不用" in desc, f"{name} 描述缺「何时不用」"
        assert "【例子】" in desc, f"{name} 描述缺中文例子"
    assert DESCRIPTIONS_VERSION, "描述集必须带版本号（评测基线据此对账）"


def test_search_policy_description_tells_the_model_what_to_do_when_degraded() -> None:
    """降级字段必须"有人告诉模型怎么用"，否则透出字段只是摆设。

    2026-09-16 事故的残留：`retrieval_degraded` 已经出现在响应里，但没有一条指令
    要求模型据此调整作答 —— 模型照旧会把排第一位的片段（当时是酒店技能考核表）
    当成适用条款。这条测试锁住"描述里必须写清降级时该怎么做"。
    """
    description = TOOL_DESCRIPTIONS["search_policy"]

    assert "retrieval_degraded" in description, "模型需要认得这个字段名才能据它行动"
    assert "核对原文" in description, "必须给出可执行动作，而不是泛泛提示"
    assert "定论" in description, "必须明确禁止把首位片段当作确定结论"


def test_failure_envelope_carries_fix_and_retryable() -> None:
    """失败信封带自纠错字段；未登记的 code 也不得谎称可重试。"""
    payload = failure_envelope("search_policy", "RETRIEVAL_UNAVAILABLE")
    assert payload["retryable"] is True
    assert payload["fix"] == FAILURE_HINTS["RETRIEVAL_UNAVAILABLE"][0]

    unknown = failure_envelope("search_policy", "SOME_NEW_CODE")
    assert unknown["fix"] is None
    assert unknown["retryable"] is False


async def test_invalid_params_names_the_field_and_constraint() -> None:
    """参数校验失败必须写清哪个字段、什么约束 —— 模型据此自纠，而非盲重试。"""
    result = await run_read_tool("search_policy", {"query": ""}, "tenant-1")
    assert result["error_code"] == "INVALID_PARAMS"
    assert result["retryable"] is False
    assert result["fix"], "INVALID_PARAMS 必须带 fix"
    detail = result["detail"]
    assert "query" in detail, f"detail 必须点名出错的字段，实际: {detail}"
    assert "at least 1 character" in detail or "min_length" in detail, "detail 必须写清约束"


# ── 工具参数必须能穿透参数校验（2026-09-16）────────────────────────────────
# `authoritative_only` 第一次上线时，MCP 工具签名和执行器都改了，唯独漏了
# `TOOL_SCHEMAS` 里的 pydantic 模型 —— 而 `validate_tool_call` 对未声明字段
# 是 pydantic 默认的 extra=ignore：**静默丢弃，不报错**。结果参数在本地单测
# 全绿，真实调用里原样穿透，直到端到端验证才暴露。
#
# 这条测试的意义：给读工具加参数时，"签名 + schema"必须一起改，否则这里红。


def test_search_policy_accepts_authoritative_only_in_its_param_schema():
    validated = validate_tool_call("search_policy", {"query": "年假", "authoritative_only": True})
    assert validated["authoritative_only"] is True


def test_search_policy_param_defaults_keep_the_old_behavior():
    validated = validate_tool_call("search_policy", {"query": "年假"})
    assert validated["authoritative_only"] is False
