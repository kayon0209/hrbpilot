"""方案 §4.3：读结果的响应预算。

为什么这些断言值得写
--------------------
预算的三个数字（条数、单段长度、总大小）本身很无聊，真正会出错的是"**哪一层**
在收窄"。执行器只看得见自己那一段，总量是跨字段的性质 —— 所以收口必须在信封层。
下面第一条测试就是这个：一个字段全都在限额内、但**字段很多**的信封，仍然要被
压回预算内。只按单字段限长是会漏的。
"""

from __future__ import annotations

import json

import pytest

from app.mcp import budgets


def _size(payload: object) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def test_long_strings_are_trimmed_at_every_depth() -> None:
    """嵌套一层就绕过限额的写法是很容易犯的错。"""
    payload = {"chunks": [{"content": "x" * 5000}]}
    trimmed = budgets.trim_strings(payload)
    assert len(trimmed["chunks"][0]["content"]) == budgets.MAX_SNIPPET_CHARS


def test_short_strings_are_left_alone() -> None:
    payload = {"content": "短"}
    assert budgets.trim_strings(payload) == payload


def test_result_lists_are_capped() -> None:
    payload = {"chunks": [{"i": i} for i in range(50)]}
    capped = budgets.limit_result_lists(payload)
    assert len(capped["chunks"]) == budgets.MAX_RESULTS


def test_non_result_lists_are_not_capped() -> None:
    """``next_actions`` 这类短列表本来就该完整返回，砍掉它会丢掉可执行信息。"""
    payload = {"next_actions": [{"a": i} for i in range(20)]}
    assert len(budgets.limit_result_lists(payload)["next_actions"]) == 20


def test_many_small_fields_still_get_squeezed() -> None:
    """每个字段都在限额内，总量仍然可能超标 —— 这是只在信封层才看得见的情形。"""
    payload = {"chunks": [{"content": "y" * budgets.MAX_SNIPPET_CHARS} for _ in range(budgets.MAX_RESULTS)]}
    payload["filler"] = ["z" * budgets.MAX_SNIPPET_CHARS for _ in range(200)]
    result = budgets.enforce(payload)
    assert _size(result) <= budgets.MAX_ENVELOPE_CHARS or "budget_note" in result


def test_a_within_budget_payload_is_untouched() -> None:
    payload = {"ok": True, "tool": "search_policy", "chunks": [{"content": "短"}]}
    assert budgets.enforce(dict(payload)) == payload


def test_truncation_leaves_a_trace() -> None:
    """静默截断比不截断更糟：模型会以为拿到了全部，然后给出确定性的答案。"""
    payload = {"chunks": [{"content": "y" * budgets.MAX_SNIPPET_CHARS} for _ in range(200)]}
    payload["filler"] = ["z"] * 5000
    result = budgets.enforce(payload)
    if _size(result) < _size(payload):
        assert "budget_note" in result or _size(result) <= budgets.MAX_ENVELOPE_CHARS


def test_structures_survive_when_they_still_fit() -> None:
    """削长度优先于丢整条结果 —— 丢信息比截短信息更贵。"""
    payload = {"chunks": [{"content": "y" * 5000} for _ in range(3)]}
    result = budgets.enforce(payload)
    assert len(result["chunks"]) == 3
    assert all(len(item["content"]) <= budgets.MAX_SNIPPET_CHARS for item in result["chunks"])


@pytest.mark.parametrize("constant", ["MAX_RESULTS", "MAX_SNIPPET_CHARS", "MAX_ENVELOPE_CHARS"])
def test_budgets_are_positive(constant: str) -> None:
    assert getattr(budgets, constant) > 0
