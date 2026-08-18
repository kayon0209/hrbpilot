"""Ingestion tests: parsing, idempotent rebuild, failure status, no zero-vector fallback."""

import hashlib

import pytest

from app.rag.ingestion.pipeline import Chunker, DocumentParser, IngestionService, sha256_hex

# --- parser ---


def test_parse_txt():
    text = DocumentParser().parse("第3.1条 工作时间".encode(), "txt")
    assert "工作时间" in text


def test_parse_unsupported_type_raises():
    with pytest.raises(ValueError):
        DocumentParser().parse(b"anything", "doc")
    with pytest.raises(ValueError):
        DocumentParser().parse(b"anything", "xls")


def test_parse_docx():
    import io

    from docx import Document

    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("制度第一条 工作时间")
    d.save(buf)
    text = DocumentParser().parse(buf.getvalue(), "docx")
    assert "制度第一条" in text


# --- chunker ---


def test_chunk_fixed_size():
    text = "制度内容" * 200  # 800 chars -> >1 chunk at 512
    chunks = Chunker().chunk(text, strategy="fixed_512", chunk_size=512, overlap=50)
    assert len(chunks) >= 2
    assert all(c["content"].strip() for c in chunks)


def test_chunk_section():
    text = "第一章 总则\n第一条 工作时间\n第二条 请假流程"
    chunks = Chunker().chunk(text, strategy="section")
    assert len(chunks) >= 1
    assert any("第一章" in c["content"] for c in chunks)


def test_chunk_section_offsets_are_document_offsets():
    text = "前言\n第一章 总则\n第一条 工作时间\n第二章 附则"
    chunks = Chunker().chunk(text, strategy="section")
    assert all(text[c["start_char"] : c["end_char"]].strip() == c["content"] for c in chunks)


# --- sha256 ---


def test_sha256_hex_deterministic():
    assert sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()
    assert sha256_hex(b"abc") == sha256_hex(b"abc")
    assert sha256_hex(b"abc") != sha256_hex(b"abd")


# --- ingestion service (fakes) ---


class _Doc:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeEmbedder:
    def __init__(self, error=None):
        self.error = error

    async def embed(self, texts):
        if self.error:
            raise self.error
        return [[0.1] * 8 for _ in texts]


class _RecordingMilvus:
    def __init__(self):
        self.ops = []

    async def delete_by_document_async(self, doc_id):
        self.ops.append("delete")
        return 0

    async def upsert_async(self, rows):
        self.ops.append("upsert")

    async def delete_by_ids_async(self, chunk_ids):
        self.ops.append(("delete_ids", list(chunk_ids)))
        return len(chunk_ids)


class _FakeObjectStore:
    def __init__(self, content):
        self.content = content

    async def get_async(self, key):
        return self.content


class _RecordingSession:
    def __init__(self, commit_error=None):
        self.added = 0
        self.commit_error = commit_error

    async def execute(self, stmt, params=None):
        class _Scalars:
            def all(self):
                return ["old-chunk"]

        class _Result:
            def scalars(self):
                return _Scalars()

        return _Result()

    def add(self, obj):
        self.added += 1

    async def flush(self):
        pass

    async def commit(self):
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        pass


def _doc():
    return _Doc(
        id="d1",
        tenant_id="t",
        kb_id="k",
        s3_key="key",
        file_type="txt",
        filename="a.txt",
        status="uploaded",
        error_message=None,
        indexed_at=None,
    )


async def test_embedding_failure_propagates_no_side_effects():
    emb = _FakeEmbedder(error=RuntimeError("embedding down"))
    mv = _RecordingMilvus()
    store = _FakeObjectStore("第3.1条 工作时间".encode())
    service = IngestionService(embedder=emb, milvus=mv, object_store=store)
    doc = _doc()
    session = _RecordingSession()

    with pytest.raises(RuntimeError):
        await service.process_document(doc, session)

    assert mv.ops == []  # nothing deleted/upserted
    assert session.added == 0  # no chunks written
    assert doc.status != "indexed"


async def test_rebuild_keeps_old_vectors_until_new_version_commits():
    emb = _FakeEmbedder()
    mv = _RecordingMilvus()
    store = _FakeObjectStore("第3.1条 工作时间".encode())
    service = IngestionService(embedder=emb, milvus=mv, object_store=store)
    doc = _doc()
    session = _RecordingSession()

    await service.process_document(doc, session)

    assert mv.ops[0] == "upsert"
    assert mv.ops[1] == ("delete_ids", ["old-chunk"])
    assert doc.status == "indexed"
    assert doc.indexed_at is not None
    assert session.added >= 1  # chunks written to PG


async def test_parse_failure_marks_error_via_worker(monkeypatch):
    # A malformed/unsupported parse should raise and never produce zero vectors.
    emb = _FakeEmbedder()
    mv = _RecordingMilvus()
    store = _FakeObjectStore(b"binary")
    service = IngestionService(embedder=emb, milvus=mv, object_store=store)
    doc = _doc()
    doc.file_type = "doc"  # unsupported type -> parser raises ValueError
    session = _RecordingSession()

    with pytest.raises(ValueError):
        await service.process_document(doc, session)
    assert session.added == 0


async def test_postgres_commit_failure_removes_new_milvus_vectors():
    service = IngestionService(
        embedder=_FakeEmbedder(),
        milvus=(milvus := _RecordingMilvus()),
        object_store=_FakeObjectStore("第3.1条 工作时间".encode()),
    )
    session = _RecordingSession(commit_error=RuntimeError("postgres commit failed"))

    with pytest.raises(RuntimeError, match="postgres commit failed"):
        await service.process_document(_doc(), session)

    assert milvus.ops[0] == "upsert"
    assert milvus.ops[1][0] == "delete_ids"
    assert milvus.ops[1][1] != ["old-chunk"]
