"""Keyword retrieval tests: Chinese tokenization + tenant/KB filtering."""

from app.rag.retrieval.retriever import Retriever
from app.rag.retrieval.tokenizer import tokenize, tokenize_for_query


def test_tokenize_segments_chinese():
    out = tokenize("试用期请假流程")
    tokens = out.split()
    assert len(tokens) >= 2  # segmented into multiple meaningful tokens
    assert "试用期" in tokens


def test_tokenize_removes_punctuation():
    out = tokenize("第3.1条：工作时间")
    assert "：" not in out
    assert "工作" in out


def test_tokenize_empty_and_punct_only():
    assert tokenize("") == ""
    assert tokenize("！！！……") == ""


def test_tokenize_query_matches_index_tokenizer():
    # The index and the query MUST use the same segmentation for FTS to match.
    text = "员工离职时未休年假按日薪折算"
    assert tokenize_for_query(text) == tokenize(text)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.last_params = None

    async def execute(self, stmt, params):
        self.last_params = params
        self.last_stmt = stmt
        return _FakeResult(self._rows)

    async def close(self):
        pass


async def test_sparse_filters_tenant_and_kb(monkeypatch):
    fake = _FakeSession(rows=[])

    async def fake_make_tenant_session(tenant_id):
        fake.seen_tenant = tenant_id
        return fake

    monkeypatch.setattr("app.rag.retrieval.retriever.make_tenant_session", fake_make_tenant_session)
    retriever = Retriever()
    result = await retriever._sparse("请假流程", "kb-123", "tenant-A", 5)

    assert result == []
    assert fake.last_params["tenant_id"] == "tenant-A"
    assert fake.last_params["kb_id"] == "kb-123"
    # Terms are OR-ed to avoid an unrelated natural-language qualifier turning
    # the entire full-text query into a false negative.
    assert fake.last_params["q"].strip() != ""
    assert " | " in fake.last_params["q"]
    # the SQL must constrain both tenant and kb
    sql_text = str(fake.last_stmt)
    assert "c.tenant_id = :tenant_id" in sql_text
    assert "c.kb_id = :kb_id" in sql_text
