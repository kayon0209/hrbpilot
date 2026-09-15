"""``_check_summary`` 的单元测试。

刻意只测**纯函数**：``_check_summary`` 不碰数据库，所以 CI 一定跑到它。聚合查询
（``_call_summary``）需要真实审计表与 RLS 上下文，由探针覆盖
（`D:/demo/output/probe_my_connections.py`，14 项全过，含用户隔离与回滚无残留）。
"""

from __future__ import annotations

from typing import Any

from app.mcp.connection_status import _check_summary


def _install(
    family_id: str,
    *,
    status: str = "active",
    last_call: dict[str, Any] | None = None,
    last_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "family_id": family_id,
        "status": status,
        "last_call": last_call,
        "last_failure": last_failure,
    }


def test_no_installations_is_not_healthy() -> None:
    # "一个都没接"不能说成"一切正常" —— 那会让用户以为已经接好了。
    got = _check_summary([], tool_count=7)
    assert got["code"] == "no_installations"
    assert got["installs"] == 0
    assert got["latest_call"] is None


def test_all_inactive_is_an_error_state() -> None:
    got = _check_summary([_install("f1", status="revoked")], tool_count=7)
    assert got["code"] == "all_inactive"
    assert got["live"] == 0
    assert got["installs"] == 1


def test_no_calls_yet_is_not_healthy() -> None:
    # 接上了但一次都没调过：通道还没被证明过，不能算健康。
    got = _check_summary([_install("f1")], tool_count=7)
    assert got["code"] == "no_calls_yet"


def test_uncovered_failure_wins_over_a_healthy_sibling() -> None:
    # 一个实例最近失败（且未被后续成功覆盖）→ 整体不能报 healthy，
    # 否则用户会以为全都好了，而实际上其中一个正在报错。
    got = _check_summary(
        [
            _install("f1", last_call={"at": "2026-01-01T00:10:00+00:00", "outcome": "FOUND"}),
            _install(
                "f2",
                last_call={"at": "2026-01-01T00:10:00+00:00", "outcome": "FORBIDDEN"},
                last_failure={"at": "2026-01-01T00:11:00+00:00", "outcome": "FORBIDDEN"},
            ),
        ],
        tool_count=7,
    )
    assert got["code"] == "recent_failure"
    assert got["failing"] == 1
    assert got["live"] == 2


def test_healthy_when_the_latest_call_succeeded() -> None:
    got = _check_summary(
        [_install("f1", last_call={"at": "2026-01-01T00:10:00+00:00", "outcome": "FOUND"})],
        tool_count=7,
    )
    assert got["code"] == "healthy"
    assert got["latest_call"] is not None
    assert got["latest_call"]["outcome"] == "FOUND"


def test_latest_call_is_picked_by_time_not_by_order() -> None:
    got = _check_summary(
        [
            _install("f1", last_call={"at": "2026-01-01T00:01:00+00:00", "outcome": "FOUND"}),
            _install("f2", last_call={"at": "2026-01-01T00:09:00+00:00", "outcome": "SUCCEEDED"}),
        ],
        tool_count=7,
    )
    assert got["latest_call"] is not None
    assert got["latest_call"]["outcome"] == "SUCCEEDED"


def test_returns_structured_facts_only() -> None:
    # 结论文案由前端出（只有那里知道 client_id 该叫 "Codex" 还是"AI 助手"）。
    # 后端在这里拼句子就等于再维护一份"名字映射"，改了前端没改后端时结论里的名字会先错。
    got = _check_summary([_install("f1")], tool_count=7)
    assert "conclusion" not in got
    assert {"code", "installs", "live", "failing", "tool_count", "latest_call"} <= set(got)
