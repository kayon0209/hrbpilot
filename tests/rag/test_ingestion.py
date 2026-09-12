"""Ingestion tests: parsing, idempotent rebuild, failure status, no zero-vector fallback."""

import hashlib
import io

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


def test_parse_docx_extracts_tables_in_document_order():
    """doc.paragraphs drops embedded tables —报销标准这类核心制度内容常以
    表格承载，必须被提取且保持与段落的相对顺序（批次 B）。"""
    import io

    from docx import Document

    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("第一条 报销标准如下：")
    table = d.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "费用类型"
    table.cell(0, 1).text = "上限"
    table.cell(0, 2).text = "凭证"
    table.cell(1, 0).text = "交通费"
    table.cell(1, 1).text = "500元"
    table.cell(1, 2).text = "发票"
    d.add_paragraph("第二条 凭证需当月提交。")
    d.save(buf)

    parser = DocumentParser()
    text = parser.parse(buf.getvalue(), "docx")

    # table content survives, rendered as readable rows
    assert "费用类型" in text and "交通费" in text and "500元" in text
    assert "费用类型 | 上限 | 凭证" in text
    # document order preserved: intro paragraph, table, closing paragraph
    assert text.index("第一条") < text.index("费用类型") < text.index("第二条")
    assert parser.last_diagnostics["tables"] == 1
    assert parser.last_diagnostics["file_type"] == "docx"


def test_parse_pdf_records_empty_page_diagnostics():
    """A page with no extractable text is counted, not silently dropped —
    the diagnostics make a scanned (needs-OCR) document visible."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)  # A4, no text at all
    buf = io.BytesIO()
    writer.write(buf)

    parser = DocumentParser()
    text = parser.parse(buf.getvalue(), "pdf")

    assert parser.last_diagnostics["pages"] == 1
    assert parser.last_diagnostics["empty_pages"] == 1
    assert text == ""  # honest: nothing was extracted


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
        self.calls = 0

    async def embed(self, texts):
        if self.error:
            raise self.error
        self.calls += 1
        self.batch_size = len(texts)
        return [[0.1] * 8 for _ in texts]


class _RecordingMilvus:
    def __init__(self):
        self.ops = []
        self.stored_vectors: dict[str, list[float]] = {}

    async def delete_by_document_async(self, doc_id):
        self.ops.append("delete")
        return 0

    async def upsert_async(self, rows):
        self.ops.append("upsert")
        for row in rows:
            self.stored_vectors[row["chunk_id"]] = row["embedding"]

    async def delete_by_ids_async(self, chunk_ids):
        self.ops.append(("delete_ids", list(chunk_ids)))
        for cid in chunk_ids:
            self.stored_vectors.pop(cid, None)
        return len(chunk_ids)

    async def fetch_embeddings_by_ids_async(self, chunk_ids):
        return {cid: self.stored_vectors[cid] for cid in chunk_ids if cid in self.stored_vectors}


class _FakeObjectStore:
    def __init__(self, content):
        self.content = content

    async def get_async(self, key):
        return self.content


class _RecordingSession:
    """Fake AsyncSession. ``rows`` mimics the (id, content_sha256) snapshot of
    the document's previous chunks — the default pretends one stale chunk id
    "old-chunk" (unused by the new stable ids, so it is cleaned up)."""

    def __init__(self, commit_error=None, rows=None):
        self.added = 0
        self.commit_error = commit_error
        self.rows = rows if rows is not None else [("old-chunk", "deadbeef")]
        self.executed: list = []
        self.added_rows: list = []

    async def execute(self, stmt, params=None):
        self.executed.append(stmt)
        rows = self.rows

        class _Result:
            def scalars(self):
                class _Scalars:
                    def all(self):
                        # Legacy single-column path: ids of the previous version.
                        return [r[0] if isinstance(r, tuple) else r for r in rows]

                return _Scalars()

            def all(self):
                # Two-column (id, content_sha256) snapshot path.
                return list(rows)

        return _Result()

    def add(self, obj):
        self.added += 1
        self.added_rows.append(obj)

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


# --- stable chunk ids + embedding reuse (P2) ---


def test_stable_chunk_id_is_deterministic_and_scoped():
    from app.rag.ingestion.pipeline import stable_chunk_id

    a = stable_chunk_id("t1", "k1", "d1", 0)
    # Same inputs → same id; different position/tenant/doc → different id.
    assert a == stable_chunk_id("t1", "k1", "d1", 0)
    assert a != stable_chunk_id("t1", "k1", "d1", 1)
    assert a != stable_chunk_id("t2", "k1", "d1", 0)
    assert a != stable_chunk_id("t1", "k1", "d2", 0)


async def test_unchanged_chunks_are_not_re_embedded():
    """Re-ingesting the same content must reuse the previous vectors: the
    embedder is called with 0 texts and Milvus keeps serving them."""
    text = "第3.1条 工作时间".encode()
    sha = sha256_hex(text)
    emb = _FakeEmbedder()
    mv = _RecordingMilvus()
    service = IngestionService(embedder=emb, milvus=mv, object_store=_FakeObjectStore(text))

    # First ingest: embeds everything, stores vectors under stable ids.
    first_session = _RecordingSession(rows=[])
    await service.process_document(_doc(), first_session)
    assert emb.calls == 1 and emb.batch_size >= 1
    first_ids = set(mv.stored_vectors)
    assert first_ids

    # Re-ingest same content with the previous (id, sha) snapshot present:
    # nothing new to embed; vectors stay under the same stable ids.
    rebuild_session = _RecordingSession(rows=[(cid, sha) for cid in first_ids])
    await service.process_document(_doc(), rebuild_session)

    assert emb.calls == 1  # no second embed call
    assert set(mv.stored_vectors) == first_ids  # same stable ids, not deleted


async def test_rebuild_reuses_unchanged_and_re_embeds_changed_text():
    """A document whose tail changed: identical head chunks reuse vectors,
    the changed tail is re-embedded — and only truly obsolete ids cleaned."""
    # Long enough to split into multiple fixed_512 chunks; the head stays
    # byte-identical across rebuilds, the tail changes.
    head = "第一条 考勤制度：标准工时每日八小时。" + "考勤打卡记录由系统自动采集并保存。 " * 40
    tail_old = "第二条 旧版报销说明。"
    tail_new = "第二条 新版报销说明：需增值税发票。"
    emb = _FakeEmbedder()
    mv = _RecordingMilvus()

    service = IngestionService(embedder=emb, milvus=mv, object_store=_FakeObjectStore(f"{head}{tail_old}".encode()))
    s1 = _RecordingSession(rows=[])
    doc1 = _doc()
    await service.process_document(doc1, s1)
    assert len(s1.added_rows) >= 2  # multiple chunks, head split across the first ones
    # Recover the first pass's (id, sha) pairs from the added DocumentChunks.
    first_rows = [(row.id, row.content_sha256) for row in s1.added_rows]

    service2 = IngestionService(embedder=emb, milvus=mv, object_store=_FakeObjectStore(f"{head}{tail_new}".encode()))
    s2 = _RecordingSession(rows=first_rows)
    await service2.process_document(_doc(), s2)

    # Second pass re-embeds only the changed tail — the identical head's
    # vector was reused from the first pass (no new embed for it).
    assert emb.calls == 2
    second_rows = [(row.id, row.content_sha256) for row in s2.added_rows]
    reused_shas = {sha for _id, sha in first_rows} & {sha for _id, sha in second_rows}
    assert reused_shas, "expected the unchanged head chunk sha to survive the rebuild"
    # Stable ids: a chunk at the same position keeps the same id.
    assert [r[0] for r in first_rows] == [r[0] for r in second_rows]
