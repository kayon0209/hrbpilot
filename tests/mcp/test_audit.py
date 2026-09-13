"""方案 §WP3：MCP 调用审计的留痕范围与参数处理。

本文件关心的三件事
------------------
1. **被拒的调用也要留痕**。只记成功调用，等于把"有人在试探边界"从审计里删掉 ——
   而那正是审计存在的理由之一。
2. **403 那一类拒绝不能消失**。它在传输层就被返回，工具层的审计点根本不会执行，
   所以守卫必须自己写。
3. **参数只落摘要**。审计表存参数原文就会变成第二份员工数据副本。

审计**写不进去**时的行为也测：那必须是"响亮地失败"（记 error 日志），而不是
让工具调用一起挂掉。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.mcp import transport_guard
from app.mcp.audit import params_digest
from tests.mcp.conftest import FakeAuthorizationServer


class _RecordingAudit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, principal: Any, **kwargs: Any) -> str | None:
        self.calls.append(kwargs)
        return "audit-row"


def _install(monkeypatch: pytest.MonkeyPatch, audit: _RecordingAudit) -> None:
    monkeypatch.setattr(transport_guard, "record_mcp_call", audit)


def _tools_call(name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}


# --------------------------------------------------------------------------- #
# 参数摘要
# --------------------------------------------------------------------------- #


def test_the_same_arguments_always_produce_the_same_digest() -> None:
    assert params_digest({"a": 1, "b": "x"}) == params_digest({"a": 1, "b": "x"})


def test_key_order_does_not_change_the_digest() -> None:
    """键顺序必须无关 —— 否则同一组参数会算出不同摘要，重放检测就失效了。"""
    assert params_digest({"a": 1, "b": 2}) == params_digest({"b": 2, "a": 1})


def test_the_digest_does_not_contain_the_argument_text() -> None:
    """审计表不能成为第二份员工数据副本。"""
    digest = params_digest({"employee_name": "张三", "salary_band": "P7"})
    assert digest is not None
    assert "张三" not in digest
    assert "P7" not in digest


def test_empty_arguments_produce_no_digest() -> None:
    assert params_digest({}) is None
    assert params_digest(None) is None


# --------------------------------------------------------------------------- #
# 留痕范围
# --------------------------------------------------------------------------- #


def test_a_403_denial_is_audited(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 在传输层返回，工具层不会执行 —— 守卫必须自己留痕。"""
    audit = _RecordingAudit()
    _install(monkeypatch, audit)
    mcp_client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("create_hr_case", {"case_id": "c-1"}),
    )
    assert audit.calls, "403 拒绝没有写审计 —— 这类拒绝在审计里会完全消失"
    entry = audit.calls[0]
    assert entry["tool"] == "create_hr_case"
    assert entry["outcome_code"] == "FORBIDDEN"
    assert entry["deny_reason"]


def test_an_allowed_call_is_not_audited_by_the_guard(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """放行的调用留给工具层去记，守卫不重复写一行。"""
    audit = _RecordingAudit()
    _install(monkeypatch, audit)
    mcp_client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("search_policy", {"query": "请假"}),
    )
    assert audit.calls == []


def test_audit_failure_does_not_break_the_challenge(
    as_server: FakeAuthorizationServer, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据库挂掉时，403 必须照常返回。

    两种失败都不可接受：审计故障变成**放行**（等于没有审计时安全性下降），
    或者变成 **500**（客户端看到的是"服务器坏了"而不是"scope 不够"，
    于是拿不到那个本该拿到的 scope 提示）。

    这里让**真实的**审计函数走一遍失败路径（把会话工厂换成会抛异常的东西），
    而不是把 ``record_mcp_call`` 整个替换掉 —— 后者测的是"调用方有没有 try"，
    前者才测得到"审计自己是不是真的不抛异常"。
    """
    import app.data.database as database

    def _explode() -> object:
        raise RuntimeError("audit db down")

    monkeypatch.setattr(database, "get_session_factory", _explode)
    response = mcp_client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {as_server.token()}"},
        json=_tools_call("create_hr_case", {"case_id": "c-1"}),
    )
    assert response.status_code == 403
