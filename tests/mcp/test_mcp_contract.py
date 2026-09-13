"""MCP 出口契约回归 —— outcome 语义 / 读工具单一实现 / 按角色收窄。

锁定三件在 2026-09-13 改造前不成立的事：

1. **读工具只有一条实现**：调用 `get_policy_source` 必须真的走到注册的执行器
   （水合正文），而不是返回一句"已校验参数"。改造前 MCP 出口与 REST 桥各有一份
   "只校验"的弱实现，agent loop 才是真的 —— 于是页面承诺超过了系统实现。
2. **"没查到"与"查不动"必须可区分**：命中为零是 NO_EVIDENCE（正常终态），
   检索不可用是 FAILED（带受控 error_code）。过去两者都被压成 `ok=true` + 一句
   `warning`，调用方无从分辨。
3. **发现侧按角色收窄**：`required_capability` 过去只在执行侧校验，发现侧把整份
   目录返回给任何登录用户。
"""

from __future__ import annotations

from app.access.middleware.rbac import ROLE_CAPABILITIES
from app.access.policies.tool_access import (
    MISSING_CAPABILITY_REASON,
    capabilities_for_role,
    partition_tools,
    tool_allowed,
)
from app.mcp import read_dispatch
from app.mcp.contract import (
    APPROVAL_SUBMITTED_TEMPLATE,
    USER_MESSAGES,
    ToolOutcome,
    envelope,
    failure_envelope,
    read_outcome,
)
from app.mcp.read_dispatch import anonymous_read_envelope, run_read_tool
from app.scenarios.hr_case_agent import read_executors
from app.scenarios.hr_case_agent.tools import TOOL_CATALOG, validate_tool_call

READ_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "read")
WRITE_TOOL_NAMES = tuple(t.name for t in TOOL_CATALOG.tools if t.kind.value == "write")


async def _fake_resolve_kb_id(tenant_id: str, kb_id: str | None) -> str:
    return kb_id or "kb-1"


def _chunk() -> dict:
    return {
        "chunk_id": "c1",
        "document_id": "d1",
        "kb_id": "kb-1",
        "source": "薪酬福利管理制度.docx",
        "section": "第三章 加班费",
        "content": "加班费按 1.5/2/3 倍计算",
    }


def _install_retriever(monkeypatch, *, chunks: list[dict] | None = None, boom: Exception | None = None) -> None:
    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_resolve_kb_id)

    class FakeRetriever:
        async def retrieve(self, **kwargs):
            if boom is not None:
                raise boom
            return chunks if chunks is not None else [_chunk()]

    monkeypatch.setattr("app.rag.retrieval.retriever.Retriever", lambda *a, **k: FakeRetriever())


# --- 1. 契约本身 -----------------------------------------------------------


def test_every_outcome_has_a_plain_language_message() -> None:
    """新增 outcome 时必须同时给出中文文案，否则用户会看到裸的枚举值。"""
    missing = [o for o in ToolOutcome if o not in USER_MESSAGES]
    assert missing == []


def test_outcome_decides_ok_instead_of_the_other_way_round() -> None:
    """`ok` 是 outcome 的函数，不是并列的第二套判断。"""
    assert envelope("t", ToolOutcome.FOUND)["ok"] is True
    # 没查到是正常的终态，不是失败 —— 这条最容易在重构时被写反。
    assert envelope("t", ToolOutcome.NO_EVIDENCE)["ok"] is True
    assert envelope("t", ToolOutcome.AWAITING_APPROVAL)["ok"] is True
    assert envelope("t", ToolOutcome.FAILED)["ok"] is False
    assert envelope("t", ToolOutcome.FORBIDDEN)["ok"] is False
    assert envelope("t", ToolOutcome.AUTH_REQUIRED)["ok"] is False


def test_read_outcome_is_derived_from_the_registered_evidence_key() -> None:
    assert read_outcome("search_policy", {"chunks": [_chunk()]}) is ToolOutcome.FOUND
    assert read_outcome("search_policy", {"chunks": []}) is ToolOutcome.NO_EVIDENCE
    assert read_outcome("get_policy_source", {"document": {"filename": "a.pdf"}}) is ToolOutcome.FOUND
    assert read_outcome("get_policy_source", {"document": None}) is ToolOutcome.NO_EVIDENCE
    # 未登记证据字段的工具：有内容即已找到，否则按无依据处理（保守，不谎报命中）。
    assert read_outcome("some_future_tool", {"anything": 1}) is ToolOutcome.FOUND
    assert read_outcome("some_future_tool", {}) is ToolOutcome.NO_EVIDENCE


def test_failure_envelope_never_leaks_the_raw_exception() -> None:
    result = failure_envelope("search_policy", "SOMETHING_UNMAPPED")
    assert result["outcome"] == ToolOutcome.FAILED.value
    assert result["error_code"] == "SOMETHING_UNMAPPED"
    # 未登记的 code 回落通用文案，绝不把原始异常内容当作用户文案。
    assert result["user_message"] == USER_MESSAGES[ToolOutcome.FAILED]
    assert "Traceback" not in str(result)


# --- 2. 读工具统一派发 -----------------------------------------------------


async def test_search_policy_returns_found_for_real_hits(monkeypatch) -> None:
    _install_retriever(monkeypatch, chunks=[_chunk()])

    result = await run_read_tool("search_policy", {"query": "加班费"}, "tenant-1")

    assert result["outcome"] == ToolOutcome.FOUND.value
    assert result["ok"] is True
    assert result["chunks"][0]["source"] == "薪酬福利管理制度.docx"
    assert result["tenant_id"] == "tenant-1"
    assert result["validated_params"]["query"] == "加班费"


async def test_search_policy_distinguishes_empty_from_unavailable(monkeypatch) -> None:
    """同一句「没结果」，两种成因必须给出不同的 outcome。"""
    _install_retriever(monkeypatch, chunks=[])
    empty = await run_read_tool("search_policy", {"query": "不存在的制度"}, "tenant-1")
    assert empty["outcome"] == ToolOutcome.NO_EVIDENCE.value
    assert empty["ok"] is True

    _install_retriever(monkeypatch, boom=ConnectionError("milvus unreachable"))
    down = await run_read_tool("search_policy", {"query": "不存在的制度"}, "tenant-1")
    assert down["outcome"] == ToolOutcome.FAILED.value
    assert down["ok"] is False
    assert down["error_code"] == "RETRIEVAL_UNAVAILABLE"
    # 原始异常内容只进日志。
    assert "milvus unreachable" not in str(down)


async def test_get_policy_source_actually_reaches_the_registered_executor(monkeypatch) -> None:
    """A1 回归：出口必须调用注册的执行器，不能只回一句「已校验参数」。

    改造前 MCP 出口返回 `note: "L: validated; document hydration is P1."`，
    于是页面上"查看制度原文"永远拿不到正文。
    """
    calls: list[dict] = []

    async def fake_executor(params: dict) -> dict:
        calls.append(params)
        return {"summary": "已定位《请假管理制度.pdf》", "document": {"filename": "请假管理制度.pdf", "chunks": []}}

    monkeypatch.setitem(read_dispatch.READ_TOOL_EXECUTORS, "get_policy_source", fake_executor)

    result = await run_read_tool("get_policy_source", {"document_name": "请假管理制度"}, "tenant-1")

    assert calls == [{"document_name": "请假管理制度"}], "出口没有把请求交给执行器"
    assert result["outcome"] == ToolOutcome.FOUND.value
    assert result["document"]["filename"] == "请假管理制度.pdf"
    assert "note" not in result


async def test_get_policy_source_reports_missing_document_without_inventing_one(monkeypatch) -> None:
    async def missing_executor(params: dict) -> dict:
        return {"summary": "未找到文档", "document": None}

    monkeypatch.setitem(read_dispatch.READ_TOOL_EXECUTORS, "get_policy_source", missing_executor)

    result = await run_read_tool("get_policy_source", {"document_name": "不存在"}, "tenant-1")

    assert result["outcome"] == ToolOutcome.NO_EVIDENCE.value
    assert result["document"] is None


async def test_anonymous_read_never_returns_policy_content() -> None:
    """没有身份就没有作用范围 —— 匿名只能拿到"参数已校验"这个事实。"""
    result = anonymous_read_envelope("search_policy", {"query": "加班费"})

    assert result["outcome"] == ToolOutcome.AUTH_REQUIRED.value
    assert result["validated_params"]["query"] == "加班费"
    assert "chunks" not in result
    assert "document" not in result


async def test_unknown_tool_and_invalid_params_are_controlled_failures() -> None:
    unknown = await run_read_tool("not_a_tool", {}, "tenant-1")
    assert unknown["error_code"] == "UNKNOWN_TOOL"

    invalid = await run_read_tool("search_policy", {}, "tenant-1")
    assert invalid["outcome"] == ToolOutcome.FAILED.value
    assert invalid["error_code"] == "INVALID_PARAMS"


async def test_a_read_tool_cannot_hijack_the_envelope(monkeypatch) -> None:
    """执行器返回的同名字段不得覆盖契约字段（否则一个工具就能改写 ok/outcome）。"""

    async def sneaky(params: dict) -> dict:
        # 明明命中了，却谎报 ok=False / outcome=FAILED，并试图改写工具名。
        return {"ok": False, "outcome": "FAILED", "tool": "other", "summary": "s", "chunks": [_chunk()]}

    monkeypatch.setitem(read_dispatch.READ_TOOL_EXECUTORS, "search_policy", sneaky)

    result = await run_read_tool("search_policy", {"query": "x"}, "tenant-1")
    assert result["ok"] is True
    assert result["outcome"] == ToolOutcome.FOUND.value
    assert result["tool"] == "search_policy"


# --- 3. 按角色收窄 ---------------------------------------------------------


def test_every_tool_capability_exists_in_the_rbac_matrix() -> None:
    """不变式：工具声明的能力名必须是 RBAC 里真实存在的取值。

    这条能直接抓到改造前的缺陷 —— 读工具声明的 "policy.read" 在
    ROLE_CAPABILITIES 里从来不存在，任何按能力判定的地方对读工具都会恒判 False。
    """
    known = {cap for caps in ROLE_CAPABILITIES.values() for cap in caps}
    declared = {t.required_capability for t in TOOL_CATALOG.tools}
    assert declared <= known, f"工具声明了 RBAC 不认识的能力: {sorted(declared - known)}"


def test_roles_see_exactly_what_they_can_invoke() -> None:
    """发现侧收窄后，HR 角色仍看到全部 7 个工具；平台管理员看到 0 个。

    管理员看到 0 个不是 bug，是 RBAC 的既定设计：平台管理员不继承 HR 业务
    内容权限（app/access/middleware/rbac.py 的 ROLE_CAPABILITIES 里 admin
    没有 hr_case / work_summary / policy_qa）。
    """
    for role in ("hrbp", "hr_manager"):
        visible, hidden = partition_tools(TOOL_CATALOG, role)
        assert {t.name for t in visible} == set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
        assert hidden == []

    visible, hidden = partition_tools(TOOL_CATALOG, "admin")
    assert visible == []
    assert len(hidden) == len(TOOL_CATALOG.tools)


def test_employee_only_sees_read_tools() -> None:
    visible, hidden = partition_tools(TOOL_CATALOG, "employee")
    assert {t.name for t in visible} == set(READ_TOOL_NAMES)
    assert {t.name for t in hidden} == set(WRITE_TOOL_NAMES)


def test_unknown_role_is_fail_closed() -> None:
    """角色解析不出来时按"什么都不给"处理，不能默认放行。"""
    assert capabilities_for_role(None) == frozenset()
    assert capabilities_for_role("nobody") == frozenset()
    visible, hidden = partition_tools(TOOL_CATALOG, None)
    assert visible == []
    assert len(hidden) == len(TOOL_CATALOG.tools)


def test_tool_allowed_matches_partition() -> None:
    for tool in TOOL_CATALOG.tools:
        assert tool_allowed(tool, "hrbp") is True
        assert tool_allowed(tool, "admin") is False
    assert MISSING_CAPABILITY_REASON  # 前端空状态要用它解释原因


def test_validated_params_never_carry_unknown_fields() -> None:
    """前端表单按 schema 生成字段；多传的键必须被规范化掉，不能进审批参数。"""
    validated = validate_tool_call("search_policy", {"query": "加班费", "not_a_field": "x"})
    assert "not_a_field" not in validated


def test_approval_message_describes_dedupe_honestly() -> None:
    """写工具的成功文案必须同时覆盖"新建"与"沿用同一条"两种情形。"""
    text = APPROVAL_SUBMITTED_TEMPLATE.format(approval_id="AP-77")
    assert "AP-77" in text
    assert "不会重复建单" in text
