"""来源级别映射的解析规则（``scripts/classify_kb_authority.py``）。

这一层值得单独测，因为映射文件是**人写的**，而它决定的正是"哪些文档可以被当作
制度依据"。它出错的形态很安静：写错一个取值、漏掉一段，脚本要么崩、要么把一份
第三方模板悄悄标成 company_policy —— 后者比崩溃危险得多。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from classify_kb_authority import load_map  # noqa: E402

from app.data.models.knowledge_base import SourceAuthority  # noqa: E402


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "map.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_map_parses_filename_to_authority(tmp_path):
    path = _write(
        tmp_path,
        {
            "documents": {
                "09_条例.txt": {"authority": "national_law", "why": "国务院令第514号"},
                "模板.docx": {"authority": "vendor_template", "why": "第三方样稿"},
            }
        },
    )
    parsed = load_map(path)
    assert parsed["09_条例.txt"] is SourceAuthority.NATIONAL_LAW
    assert parsed["模板.docx"] is SourceAuthority.VENDOR_TEMPLATE


def test_load_map_rejects_an_unknown_authority(tmp_path):
    """写错取值必须**当场报错**，不能默默落成 unknown ——
    否则映射看着有 11 条，实际生效的只有 10 条。"""
    path = _write(tmp_path, {"documents": {"a.txt": {"authority": "national-law"}}})
    with pytest.raises(SystemExit) as excinfo:
        load_map(path)
    assert "未知的 authority" in str(excinfo.value)
    assert "national_law" in str(excinfo.value), "报错要列出可用取值"


def test_load_map_requires_a_non_empty_documents_section(tmp_path):
    for payload in ({}, {"documents": {}}, {"note": "只有说明没有映射"}):
        with pytest.raises(SystemExit):
            load_map(_write(tmp_path, payload))
