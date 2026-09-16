"""kb_docs 批量导入脚本的纯逻辑约束。

只测不需要数据库的部分 —— 但它们恰好是"静默丢文件"最容易发生的地方：
格式过滤、拒绝原因、排序稳定性、对象键是否保留相对路径。
入库本身由 scripts/import_kb_docs.py 的集成运行验证（见 manifest）。
"""

import pytest

from app.rag.ingestion.pipeline import SUPPORTED_TYPES as PIPELINE_SUPPORTED
from scripts.import_kb_docs import (
    CONTENT_TYPES,
    FORM_TEMPLATE_EXTENSIONS,
    discover_documents,
    file_type_of,
    s3_key_for,
    sha256_hex,
)
from scripts.import_kb_docs import SUPPORTED_TYPES as SCRIPT_SUPPORTED


def _touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_supported_types_cannot_drift_from_the_pipeline():
    """脚本的"可入库"定义必须就是 pipeline 的，不能各维护一份。"""
    assert SCRIPT_SUPPORTED == PIPELINE_SUPPORTED


def test_content_types_cover_every_supported_type():
    assert set(CONTENT_TYPES) == set(PIPELINE_SUPPORTED)


def test_unsupported_files_are_reported_not_dropped(tmp_path):
    _touch(tmp_path / "a.txt")
    _touch(tmp_path / "b.doc")

    importable, rejected = discover_documents(tmp_path)

    assert [p.name for p in importable] == ["a.txt"]
    assert [r["path"] for r in rejected] == ["b.doc"]
    # 每个被拒绝的文件都必须带可读原因，否则等于静默丢弃
    assert all(r["reason"] for r in rejected)


def test_form_template_extensions_get_their_own_reason(tmp_path):
    """能转格式 与 本来就不该入库 是两种处置，不能被混成一个数字。"""
    _touch(tmp_path / "考核表.xls")
    _touch(tmp_path / "讲义.ppt")
    _touch(tmp_path / "手册.wps")  # 既非支持格式，也不是表单/课件

    _, rejected = discover_documents(tmp_path)
    by_ext = {r["ext"]: r["reason"] for r in rejected}

    assert "制度库" in by_ext["xls"]
    assert "制度库" in by_ext["ppt"]
    assert "不受支持" in by_ext["wps"]
    for ext in ("xls", "ppt"):
        assert ext in FORM_TEMPLATE_EXTENSIONS


def test_nested_paths_are_walked_recursively(tmp_path):
    _touch(tmp_path / "top.pdf")
    _touch(tmp_path / "pack" / "deep" / "inner.docx")

    importable, _ = discover_documents(tmp_path)

    assert {p.name for p in importable} == {"top.pdf", "inner.docx"}


def test_discovery_order_is_stable(tmp_path):
    """两次运行的 manifest 与 chunk_index 必须一致：稳定 chunk id 依赖稳定顺序。"""
    for name in ("c.txt", "a.txt", "b.txt"):
        _touch(tmp_path / name)

    first, _ = discover_documents(tmp_path)
    second, _ = discover_documents(tmp_path)

    assert [p.name for p in first] == ["a.txt", "b.txt", "c.txt"]
    assert first == second


def test_dotfiles_are_not_treated_as_documents(tmp_path):
    _touch(tmp_path / ".DS_Store")
    _touch(tmp_path / ".hidden.txt")

    importable, rejected = discover_documents(tmp_path)

    assert importable == []
    assert rejected == []


def test_file_type_of_normalises_case_and_strips_the_dot(tmp_path):
    _touch(tmp_path / "UPPER.PDF")

    assert file_type_of(tmp_path / "UPPER.PDF") == "pdf"
    assert file_type_of(tmp_path / "noext") == ""


def test_s3_key_preserves_relative_path_so_basenames_do_not_collide(tmp_path):
    """两个模板包里同名文件必须落在不同对象键上，否则后导入的覆盖先导入的。"""
    a = tmp_path / "pack1" / "制度.docx"
    b = tmp_path / "pack2" / "制度.docx"

    key_a = s3_key_for("eval-runner", "policy_kb", a, tmp_path)
    key_b = s3_key_for("eval-runner", "policy_kb", b, tmp_path)

    assert key_a == "eval-runner/policy_kb/pack1/制度.docx"
    assert key_a != key_b


def test_sha256_matches_known_vector():
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.parametrize("name", ["a.txt", "a.pdf", "a.docx"])
def test_known_good_formats_are_importable(tmp_path, name):
    _touch(tmp_path / name)
    importable, rejected = discover_documents(tmp_path)
    assert len(importable) == 1 and rejected == []
