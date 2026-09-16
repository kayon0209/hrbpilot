"""T4 结果侧响应预算回归 —— 分页 / 截断留痕 / 摘要分档 / 人类可读字段。

锁定四件事（任务书 2026-09-15 T4 验收）：

1. **列表永不返回全量**：``search_cases`` 默认 ≤20 条，schema 层钳死上限；
2. **截断必须留痕**：``has_more=True`` 时带 ``next_cursor`` 与引导文案，
   不静默截断；
3. **concise / detailed 有可观测差距**：concise 只给出处+片段（截断正文、
   不带标识 id），detailed 才带 chunk_id/document_id 等后续调用需要的字段；
4. **人类可读字段优先**：条目里有 title/source/section 这类可读字段，
   uuid 仅作辅助。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.mcp import read_dispatch
from app.mcp.auth.principal import AuthMethod, McpPrincipal
from app.rag.retrieval.retriever import RetrievalDiagnostics
from app.scenarios.hr_case_agent import read_executors
from app.scenarios.hr_case_agent.read_context import (
    bind_read_principal,
    bind_read_tenant,
    reset_read_principal,
    reset_read_tenant,
)
from app.scenarios.hr_case_agent.tools import TOOL_SCHEMAS


def _principal() -> McpPrincipal:
    """与既有 tests/mcp/test_access_profile.py 同形的 HR 经理替身。"""
    return McpPrincipal(
        tenant_id="tenant-1",
        user_id="hr-manager-1",
        role="hr_manager",
        auth_method=AuthMethod.OAUTH,
        client_id="workbuddy",
        installation_id="2f0282cd-5639-41b1-a36f-fb4b1dc0e567",  # type: ignore[arg-type]
        scopes=frozenset(),
        client_ceiling=frozenset(),
        token_id="jti-1",
    )


def _case(n: int) -> SimpleNamespace:
    """最小 HRCase 替身：execute_search_cases 只读这几个字段。"""
    return SimpleNamespace(
        id=f"case-{n:03d}",
        title=f"案件 {n}",
        subject_ref=f"EMP-{n:03d}",
        category="onboarding",
        risk_level="LOW",
        status="OPEN",
        created_at=None,
    )


def _patch_cases(monkeypatch, rows: list, *, visible=None) -> None:
    """把 ACL/会话两层都换成可控桩：本测试关心的是信封层，不是 SQL。"""
    monkeypatch.setattr(read_executors, "_visible_user_ids", _visible_stub)
    monkeypatch.setattr(read_executors, "make_tenant_session", _fake_session_factory)
    monkeypatch.setattr("app.scenarios.hr_case_agent.service.HRCaseService.search_cases", _fake_search(rows))


def _visible_stub(principal):
    async def _run():
        return set()

    return _run()


async def _fake_session_factory(tenant_id: str):
    class _S:
        async def close(self):
            return None

    return _S()


def _fake_search(rows: list):
    async def _search(self, *, limit: int = 20, offset: int = 0, status=None, category=None):
        return rows[offset : offset + limit]

    return _search


async def _run_cases(monkeypatch, rows: list, **params) -> dict:
    _patch_cases(monkeypatch, rows)
    tenant_token = bind_read_tenant("tenant-1")
    principal_token = bind_read_principal(_principal())
    try:
        return await read_dispatch.run_read_tool("search_cases", params, "tenant-1")
    finally:
        reset_read_principal(principal_token)
        reset_read_tenant(tenant_token)


# --- 1. 列表永不返回全量 ---------------------------------------------------


def test_search_cases_schema_caps_limit_at_20() -> None:
    schema = TOOL_SCHEMAS["search_cases"].model_json_schema()
    limit = schema["properties"]["limit"]
    assert limit.get("maximum") == 20 or limit.get("le") == 20, "列表上限必须是 20（T4-1）"
    assert limit.get("default", limit.get("ge")) in (20, 1), "默认 limit 应为 20"


async def test_search_cases_returns_at_most_limit_rows(monkeypatch) -> None:
    rows = [_case(n) for n in range(50)]
    result = await _run_cases(monkeypatch, rows, limit=20)
    assert len(result["cases"]) == 20


# --- 2. 截断留痕 -----------------------------------------------------------


async def test_truncation_is_loud_not_silent(monkeypatch) -> None:
    """命中超过一页：has_more + next_cursor + 引导文案，三者缺一不可。"""
    rows = [_case(n) for n in range(35)]
    result = await _run_cases(monkeypatch, rows, limit=20)
    assert result["count"] == 20
    assert result["has_more"] is True
    assert result["next_cursor"] == 20
    assert "缩小范围" in result["pagination_note"], "截断引导必须建议缩小范围重查"


async def test_last_page_has_no_cursor(monkeypatch) -> None:
    rows = [_case(n) for n in range(35)]
    result = await _run_cases(monkeypatch, rows, limit=20, offset=20)
    assert len(result["cases"]) == 15
    assert result["has_more"] is False
    assert "next_cursor" not in result


async def test_cursor_round_trips_to_next_page(monkeypatch) -> None:
    """next_cursor 直接作为 offset 回传 → 取到下一页（协议上闭环）。"""
    rows = [_case(n) for n in range(45)]
    page1 = await _run_cases(monkeypatch, rows, limit=20)
    page2 = await _run_cases(monkeypatch, rows, limit=20, offset=page1["next_cursor"])
    titles2 = {c["title"] for c in page2["cases"]}
    assert "案件 20" in titles2 and "案件 39" in titles2, "第二页应覆盖 20–39"
    assert len(page2["cases"]) == 20
    assert not (titles2 & {c["title"] for c in page1["cases"]}), "两页不得重叠"
    # 45 条按 20/页 → 第三页（offset=40）只有 5 条且没有下一页。
    page3 = await _run_cases(monkeypatch, rows, limit=20, offset=page2["next_cursor"])
    assert len(page3["cases"]) == 5 and page3["has_more"] is False


# --- 3. 摘要分档 -----------------------------------------------------------


async def test_policy_search_concise_is_smaller_than_detailed(monkeypatch) -> None:
    """同一问句，concise 与 detailed 的返回体积有可观测差距（T4 验收）。"""
    long_content = "第" + "一" * 900 + "条 内容"
    chunk = {
        "chunk_id": "c-001",
        "document_id": "d-001",
        "kb_id": "kb-1",
        "source": "员工手册.pdf",
        "section": "第二章 年假",
        "content": long_content,
    }

    async def fake_retriever(self, **kwargs):
        return [dict(chunk)], RetrievalDiagnostics(strategy="hybrid")

    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_kb)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever.retrieve_with_diagnostics", fake_retriever
    )

    tenant_token = bind_read_tenant("tenant-1")
    try:
        concise = await read_dispatch.run_read_tool("search_policy", {"query": "年假", "detail": "concise"}, "tenant-1")
        detailed = await read_dispatch.run_read_tool(
            "search_policy", {"query": "年假", "detail": "detailed"}, "tenant-1"
        )
    finally:
        reset_read_tenant(tenant_token)

    c0, d0 = concise["chunks"][0], detailed["chunks"][0]
    assert "chunk_id" not in c0 and "document_id" not in c0, "concise 档不应带标识 id"
    assert "chunk_id" in d0 and "document_id" in d0, "detailed 档必须带后续调用需要的 id"
    # concise 被档内预算截到 ~300；detailed 保留到信封总闸（600）—— 明显更长即可。
    assert len(d0["content"]) > len(c0["content"]) * 1.5, "detailed 的正文不应被 concise 的截断预算削短"


async def test_concise_is_the_default(monkeypatch) -> None:
    """不传 detail 时默认 concise（响应预算的默认值必须是最小档）。"""
    chunk = {
        "chunk_id": "c-1",
        "document_id": "d-1",
        "kb_id": "kb-1",
        "source": "s.pdf",
        "section": "s1",
        "content": "x",
    }

    async def fake_retriever(self, **kwargs):
        return [dict(chunk)], RetrievalDiagnostics(strategy="hybrid")

    monkeypatch.setattr(read_executors, "_resolve_kb_id", _fake_kb)
    monkeypatch.setattr(
        "app.rag.retrieval.retriever.Retriever.retrieve_with_diagnostics", fake_retriever
    )
    tenant_token = bind_read_tenant("tenant-1")
    try:
        result = await read_dispatch.run_read_tool("search_policy", {"query": "q"}, "tenant-1")
    finally:
        reset_read_tenant(tenant_token)
    assert result["detail"] == "concise"
    assert "chunk_id" not in result["chunks"][0]


# --- 4. 人类可读字段优先 ----------------------------------------------------


async def test_case_rows_lead_with_readable_fields(monkeypatch) -> None:
    rows = [_case(1)]
    result = await _run_cases(monkeypatch, rows, limit=5)
    case = result["cases"][0]
    for field in ("title", "subject_ref", "category", "risk_level", "status"):
        assert case.get(field), f"案件条目应带可读字段 {field}"
    # case_id 是后续调用的必要键，保留；但它不是给人看的展示位 —— 标题才是。
    assert case["title"] == "案件 1"


async def _fake_kb(tenant_id: str, kb_id: str | None) -> str:
    return kb_id or "kb-1"
