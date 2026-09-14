"""T6 路由黄金集与评测器的**结构**回归（不依赖真实服务）。

真实端点的基线数字由 ``scripts/eval_tool_routing.py`` 现场产出并写入
``docs/ops/2026-09-15-tool-routing-baseline.md``；这里锁的是数据与代码的
结构不变量 —— 集合本身不能悄悄变质（重复 id、非法 expect、held-out 缺失、
判分逻辑把"未验"当"失败"）。
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_tool_routing import GOLDEN_PATH, SampleResult, _grade

VALID_EXPECTS = {"tool", "clarify", "refuse", "multi"}
KNOWN_TOOLS = {
    "search_policy",
    "get_policy_source",
    "search_cases",
    "get_case_summary",
    "get_approval_status",
    "get_my_access_profile",
    "create_hr_case",
    "assign_case_owner",
    "send_case_notification",
    "update_case_status",
    "create_work_task",
}


def _load() -> list[dict]:
    return [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_golden_set_has_at_least_fifty_entries() -> None:
    assert len(_load()) >= 50, "任务书要求黄金集 ≥50 条"


def test_golden_ids_are_unique() -> None:
    ids = [s["id"] for s in _load()]
    assert len(ids) == len(set(ids)), "id 重复会让复跑对账错位"


def test_golden_expect_values_are_valid_and_tools_known() -> None:
    for s in _load():
        assert s["expect"] in VALID_EXPECTS, f"{s['id']} expect 非法"
        if s["expect"] == "tool":
            assert s["tool"] in KNOWN_TOOLS, f"{s['id']} 指向未知工具 {s.get('tool')}"
        if s["expect"] == "multi":
            assert set(s["tools"]) <= KNOWN_TOOLS, f"{s['id']} multi 含未知工具"


def test_golden_set_has_a_held_out_subset() -> None:
    """held-out ≥ 总量 10% —— 少于这个比例，基线就退化成"只优化演示话术"。"""
    samples = _load()
    held = [s for s in samples if s.get("split") == "held-out"]
    assert len(held) >= max(5, len(samples) // 10), "held-out 子集太小或缺失"


def test_golden_covers_all_four_behaviours() -> None:
    """四类期望都要有 —— 缺一类说明集合偏科，基线对那类问题失去判别力。"""
    expects = {s["expect"] for s in _load()}
    assert expects == VALID_EXPECTS


def test_golden_covers_every_tool_at_least_once() -> None:
    """11 个工具都要有正例 —— 没有正例的工具等于没进基线。"""
    mentioned: set[str] = set()
    for s in _load():
        if s["expect"] == "tool":
            mentioned.add(s["tool"])
        elif s["expect"] == "multi":
            mentioned.update(s["tools"])
    missing = KNOWN_TOOLS - mentioned
    assert not missing, f"这些工具在黄金集里没有正例: {sorted(missing)}"


def _live_stub(sample_id: str) -> SampleResult:
    return SampleResult(
        id=sample_id,
        utterance="",
        expect="tool",
        expected_tool=None,
        split="train",
    )


def test_grade_correct_tool_selection_passes() -> None:
    sample = {"id": "t1", "utterance": "年假", "expect": "tool", "tool": "search_policy", "split": "train"}
    prediction = {"action": "call", "tools": ["search_policy"]}
    graded = _grade(sample, prediction, _live_stub("t1"))
    assert graded.passed


def test_grade_wrong_tool_fails_with_reason() -> None:
    sample = {"id": "t2", "utterance": "案件", "expect": "tool", "tool": "search_cases", "split": "train"}
    prediction = {"action": "call", "tools": ["search_policy"]}
    graded = _grade(sample, prediction, _live_stub("t2"))
    assert not graded.passed
    assert "search_cases" in graded.reason


def test_grade_clarify_requires_the_expected_followup() -> None:
    sample = {"id": "t3", "utterance": "跟进一下", "expect": "clarify", "must_ask": ["哪个人", "哪件事"]}
    good = _grade(sample, {"action": "clarify", "ask": ["请问是哪个人、哪件事？"]}, _live_stub("t3"))
    assert good.passed
    bad = _grade(sample, {"action": "clarify", "ask": ["什么时间？"]}, _live_stub("t3"))
    assert not bad.passed


def test_grade_refuse_rejects_tool_call_and_invented_data() -> None:
    sample = {"id": "t4", "utterance": "查工资", "expect": "refuse", "split": "train"}
    assert _grade(sample, {"action": "refuse"}, _live_stub("t4")).passed
    assert not _grade(sample, {"action": "call", "tools": ["search_policy"]}, _live_stub("t4")).passed
    invented = _grade(sample, {"action": "refuse", "invented_data": True}, _live_stub("t4"))
    assert not invented.passed, "拒绝但编数据必须判负"


def test_grade_multi_requires_all_tools() -> None:
    sample = {"id": "t5", "utterance": "制度+案件", "expect": "multi", "tools": ["search_policy", "search_cases"]}
    assert _grade(sample, {"action": "call", "tools": ["search_policy", "search_cases"]}, _live_stub("t5")).passed
    assert not _grade(sample, {"action": "call", "tools": ["search_policy"]}, _live_stub("t5")).passed


def test_unverified_live_call_is_not_a_failure() -> None:
    """live=None（需要真实案件 id 才能验的工具）不得把路由正确的条目判负。

    路由对不对与端点调用成不成功是两件事；把"没验"当"失败"会让基线数字
    混入与路由无关的噪声。
    """
    sample = {"id": "t6", "utterance": "案件详情", "expect": "tool", "tool": "get_case_summary"}
    live = _live_stub("t6")
    live.live_call_ok = None  # 未验证
    graded = _grade(sample, {"action": "call", "tools": ["get_case_summary"]}, live)
    assert graded.passed


def test_failed_live_call_fails_the_row() -> None:
    sample = {"id": "t7", "utterance": "制度", "expect": "tool", "tool": "search_policy"}
    live = _live_stub("t7")
    live.live_call_ok = False
    live.live_call_outcome = "call_error: ConnectError"
    graded = _grade(sample, {"action": "call", "tools": ["search_policy"]}, live)
    assert not graded.passed


def test_results_dir_is_gitignored_or_written_under_results() -> None:
    """明细必须落在 evaluation/results/（既有目录，不往仓库根撒文件）。"""
    from scripts.eval_tool_routing import RESULTS_DIR

    assert Path("evaluation") / "results" == RESULTS_DIR or RESULTS_DIR.match("*/evaluation/results")
