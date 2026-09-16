"""来源级别（``SourceAuthority``）的数据契约与回填。

背景：知识库里**同时**存在三类东西，而系统此前对它们一视同仁——
  1. 国务院令第514号 / 人社部令第1号：国家法规，法定最低标准；
  2. hrtools 员工手册模板：第三方样稿，其"带薪年假"条款写的是 ``×天`` 占位符；
  3. 酒店工程部技能考核表：表单，与制度无关。
一份格式完全合法但性质是"别家公司的模板"的文档，会被当作"公司制度"答出去。
格式过滤挡不住这一类，只有"来源级别"能。

这组测试锁住三件事：取值口径、默认值必须是 unknown（让"没人声明过"可见）、
以及回填是从 ``documents`` 统一取一次（不依赖任何一条检索腿）。
"""

from __future__ import annotations

from app.data.models.knowledge_base import AUTHORITATIVE_AUTHORITIES, SourceAuthority
from app.rag.retrieval import retriever as retriever_module
from app.rag.retrieval.retriever import Retriever
from app.rag.retrieval.types import RetrievedChunk


def _chunk(document_id: str, **overrides) -> RetrievedChunk:
    payload = {
        "chunk_id": f"c-{document_id}",
        "document_id": document_id,
        "kb_id": "kb-1",
        "source": "某制度.docx",
        "section": "第一章",
        "content": "正文",
        "score": 1.0,
    }
    payload.update(overrides)
    return RetrievedChunk(**payload)


class _FakeResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows) -> None:
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self._rows)

    async def close(self) -> None:
        pass


def _install_session(monkeypatch, rows) -> None:
    async def _factory(_tenant: str) -> _FakeSession:
        return _FakeSession(rows)

    monkeypatch.setattr(retriever_module, "make_tenant_session", _factory)


def test_chunk_carries_authority_and_defaults_to_unknown():
    """默认必须是 unknown，不能是 reference —— 后者会把"没人声明过"伪装成已知。"""
    payload = _chunk("d1").to_dict()
    assert payload["authority"] == SourceAuthority.UNKNOWN.value


def test_only_national_law_and_company_policy_count_as_authoritative():
    assert {"national_law", "company_policy"} == AUTHORITATIVE_AUTHORITIES
    for value in ("vendor_template", "reference", "unknown"):
        assert value not in AUTHORITATIVE_AUTHORITIES


async def test_attach_authority_fills_each_chunk_from_its_document(monkeypatch):
    _install_session(monkeypatch, [("d-law", "national_law"), ("d-template", "vendor_template")])
    chunks = [_chunk("d-law"), _chunk("d-template")]

    out = await Retriever()._attach_authority(chunks, "tenant-1", kb_id="kb-1")

    assert [c.authority for c in out] == ["national_law", "vendor_template"]


async def test_attach_authority_leaves_unknown_when_the_row_is_missing(monkeypatch):
    """查不到的文档落到 unknown，而不是沿用别的片段的级别。

    这一条防的是"回填时把整批设成同一个值"这类省事写法：一旦发生，
    第三方模板就会被染成国家法规的级别。
    """
    _install_session(monkeypatch, [("d-law", "national_law")])
    chunks = [_chunk("d-law"), _chunk("d-ghost")]

    out = await Retriever()._attach_authority(chunks, "tenant-1", kb_id="kb-1")

    assert [c.authority for c in out] == ["national_law", "unknown"]


async def test_attach_authority_skips_the_query_when_there_are_no_chunks(monkeypatch):
    def _explode(_tenant: str):
        raise AssertionError("空结果不应发起数据库查询")

    monkeypatch.setattr(retriever_module, "make_tenant_session", _explode)
    assert await Retriever()._attach_authority([], "tenant-1") == []
