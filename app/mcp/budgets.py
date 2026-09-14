"""读结果的响应预算：条数、片段长度、总大小。

为什么必须显式
--------------
在此之前限额散落在各处（``[:180]`` / ``[:200]`` / ``[:600]``），每一个都是**局部**
决定。它们的和是多少没人知道 —— 一个工具返回 3 条、每条 600 字，加上引用与元数据，
最后可能有几万字符被交到外部模型手里，而那个决定从来没有被谁做过。

方案 §4.3 要求"限制结果条数、单段长度和总字符预算，避免把大量敏感上下文交给外部
模型"。所以这里把三件事集中成三组常量，并提供一个统一的收口函数。

为什么在**信封**层收口而不是在各执行器里
----------------------------------------
执行器只知道自己那一段。总预算是一个跨字段的性质 —— 只有拿到完整信封才知道。
放在这里，将来新增读工具自动受约束；放在执行器里，新工具会默认没有上限，
而那正是"不知道总共多少"重新出现的方式。

截断要**留下痕迹**
------------------
被截断的信封会带 ``budget_note``。静默截断比不截断更糟：模型会以为它拿到了全部，
于是基于缺失的内容给出一个确定性的答案。
"""

from __future__ import annotations

import json
from typing import Any

#: 单次读调用最多返回多少条结果。与工具 schema 的 ``top_k`` 上限互为兜底 ——
#: schema 约束的是**请求**，这里约束的是**响应**（执行器可能因为内部逻辑多返回）。
MAX_RESULTS = 5

#: 单个引用片段的最大字符数。政策正文是给模型读的，不是给人复制的；
#: 600 字足以承载一段完整条款，再多就是把它当成文档传输通道用。
MAX_SNIPPET_CHARS = 600

#: 整个信封序列化后的最大字符数。这是最后一道闸：即使每个字段都在各自的限额内，
#: 字段**数量**仍可能把响应推大（例如新增一个返回很多小字段的工具）。
MAX_ENVELOPE_CHARS = 24_000

#: 会被按 MAX_RESULTS 收窄的列表字段。其余列表（例如 ``next_actions``）本来就短。
_RESULT_LIST_KEYS = ("chunks", "results", "items", "cases", "documents", "approvals")

#: 收窄时优先从**尾部**丢弃的字段顺序：读结果的相关性是从高到低排的，
#: 丢掉排在后面的，比丢掉排第一的合理。
_TRIM_NOTE = "内容较多，已按响应预算截断；如需更多，请缩小查询范围。"


def trim_strings(payload: Any, limit: int = MAX_SNIPPET_CHARS) -> Any:
    """把 payload 里所有超长字符串截到 ``limit``。

    递归处理，因为片段可能嵌在 ``{"chunks": [{"content": "..."}]}`` 这类结构里 ——
    只处理顶层的话，嵌套一层就绕过去了。
    """
    if isinstance(payload, str):
        return payload if len(payload) <= limit else payload[:limit]
    if isinstance(payload, dict):
        return {key: trim_strings(value, limit) for key, value in payload.items()}
    if isinstance(payload, list):
        return [trim_strings(item, limit) for item in payload]
    return payload


def limit_result_lists(payload: dict[str, Any], limit: int = MAX_RESULTS) -> dict[str, Any]:
    """按 ``MAX_RESULTS`` 收窄已知的结果列表字段。"""
    for key in _RESULT_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list) and len(value) > limit:
            payload[key] = value[:limit]
    return payload


def enforce(envelope_payload: dict[str, Any]) -> dict[str, Any]:
    """把信封收进预算内，必要时记下截断痕迹。

    顺序是"先局部、再整体"：先按字段限额削，剩下的如果还超总量，才动结构（丢列表
    尾部）。反过来做的话，会先丢掉整条结果，而其实只要把其中一段截短就够了 ——
    丢信息比截短信息更贵。
    """
    result = limit_result_lists(trim_strings(envelope_payload))
    if _size(result) <= MAX_ENVELOPE_CHARS:
        return result

    for key in _RESULT_LIST_KEYS:
        items = result.get(key)
        if not isinstance(items, list) or not items:
            continue
        while items and _size(result) > MAX_ENVELOPE_CHARS:
            items.pop()
        if not items:
            result.pop(key, None)
        if _size(result) <= MAX_ENVELOPE_CHARS:
            break

    if _size(result) > MAX_ENVELOPE_CHARS:
        # 结构已经削不动了（说明超长的是若干标量字段）。这种情况不应该出现，
        # 但真出现时宁可能被看见地降级，也不要抛异常 —— 那会让一次读调用变成 500。
        result.setdefault("budget_note", _TRIM_NOTE)

    if "budget_note" in result:
        result["budget_note"] = _TRIM_NOTE
    return result


def _size(payload: dict[str, Any]) -> int:
    """信封的真实大小：按最终 JSON 形态算，不是字段长度相加。

    两者差别不小（键名、转义、分隔符），而外部模型收到的正是后者。
    """
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))
