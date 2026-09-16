"""Ingestion tests: parsing, idempotent rebuild, failure status, no zero-vector fallback."""

import hashlib
import io

import pytest

from app.rag.ingestion.pipeline import (
    MAX_SECTION_CHARS,
    OCR_NEEDS_RATIO,
    Chunker,
    DocumentParser,
    IngestionService,
    _detect_table_continuations,
    _split_oversized,
    _stitch_pages,
    _stitch_pages_with_spans,
    page_numbers_for_offsets,
    sha256_hex,
)

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


def test_parse_pdf_reports_ocr_required_pages_and_needs_ocr():
    """Empty pages surface as page numbers + an explicit needs_ocr flag,
    instead of only a count that nothing downstream could act on."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)

    parser = DocumentParser()
    parser.parse(buf.getvalue(), "pdf")
    diag = parser.last_diagnostics

    assert diag["pages"] == 2
    assert diag["empty_pages"] == 2
    assert diag["ocr_required_pages"] == [1, 2]
    # every page is empty, so it is unambiguously over the OCR threshold
    assert diag["needs_ocr"] is True
    assert OCR_NEEDS_RATIO < 1.0


def test_extraction_error_page_is_counted_exactly_once(monkeypatch):
    """Regression guard: a page that RAISED during extraction used to be
    counted twice (once in the except branch, once by the empty-text check),
    which could push empty_pages above the real page count."""
    import pypdf

    class _BoomPage:
        def extract_text(self):  # pragma: no cover - called by the parser
            raise RuntimeError("boom")

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_BoomPage(), _BoomPage()]

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    parser = DocumentParser()
    parser._parse_pdf(b"%PDF-1.4 fake")

    assert parser.last_diagnostics["pages"] == 2
    assert parser.last_diagnostics["empty_pages"] == 2  # not 4
    assert parser.last_diagnostics["ocr_required_pages"] == [1, 2]


# --- cross-page stitching (§6.2) ---


def test_stitch_pages_rejoins_hyphenated_word_across_page_break():
    """'poli-' + 'cy' must come back as 'policy', not two broken tokens."""
    assert _stitch_pages(["报销标准按 poli-", "cy 执行"]) == "报销标准按 policy 执行"


def test_stitch_pages_rejoins_sentence_split_across_pages():
    """A sentence cut in half by a page break must read as one unit."""
    out = _stitch_pages(["第一章 考勤管理的适用范围如", "下：全体员工适用。"])
    assert out == "第一章 考勤管理的适用范围如下：全体员工适用。"


def test_stitch_pages_preserves_real_paragraph_boundary():
    """A page ending on terminal punctuation is a real boundary — keep the break."""
    assert _stitch_pages(["这是第一段。", "这是第二段。"]) == "这是第一段。\n这是第二段。"


def test_stitch_pages_skips_blank_pages():
    assert _stitch_pages(["", "唯一有内容的页"]) == "唯一有内容的页"
    assert _stitch_pages(["", ""]) == ""


def test_stitch_pages_with_spans_tracks_where_each_page_lands():
    """Spans must describe the joined text, so a chunk can be traced back."""
    text, spans = _stitch_pages_with_spans(["第一页内容。", "第二页内容。"])

    assert text == "第一页内容。\n第二页内容。"
    assert [span[0] for span in spans] == [1, 2]
    assert spans[0] == (1, 0, len("第一页内容。"))
    # page 2 starts at/after the newline that separates it from page 1
    assert spans[1][1] >= spans[0][2]


def test_stitch_pages_with_spans_handles_glued_pages():
    """A page glued onto its predecessor still gets a span (non-empty)."""
    _text, spans = _stitch_pages_with_spans(["适用范围如", "下：全体员工。"])

    assert [span[0] for span in spans] == [1, 2]
    assert spans[1][2] > spans[1][1]  # page 2 owns real characters


def test_page_numbers_for_offsets_maps_chunk_to_source_page():
    _text, spans = _stitch_pages_with_spans(["第一页内容。", "第二页内容。"])
    second_page_start = spans[1][1]

    assert page_numbers_for_offsets(spans, [0, second_page_start]) == [1, 2]


def test_page_numbers_for_offsets_without_spans_reports_unknown():
    """txt/docx have no pages: report None rather than inventing a number."""
    assert page_numbers_for_offsets([], [0, 10]) == [None, None]


def test_page_numbers_for_offsets_out_of_range_is_none():
    _text, spans = _stitch_pages_with_spans(["只有一页。"])

    assert page_numbers_for_offsets(spans, [9999]) == [None]


# --- cross-page table continuation (report-only) ---


def test_detect_table_continuations_flags_continuation_page():
    pages = ["表头 | 金额\n100 | 200", "300 | 400"]
    assert _detect_table_continuations(pages) == [2]


def test_detect_table_continuations_ignores_plain_text_pages():
    assert _detect_table_continuations(["普通正文", "更多正文"]) == []


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


def test_chunk_section_caps_a_marker_free_stretch():
    """回归：真实文件暴露的 400。

    ``kb_docs/05_hrtools_人力资源制度大全v2.docx`` 有一段没有任何章节标记的
    表单区（招聘费用登记表），旧实现把它整段 19236 字符装进一个 chunk，
    送进 bge-m3 报 ``400 ... exceeds the 8192 token limit (actual 12556)``，
    整篇文档入库失败。section 切分必须有单块上限。
    """
    text = "第一章 总则\n" + ("招聘费用登记表内容" * 3000)  # 24000 字符，无后续标记

    chunks = Chunker().chunk(text, strategy="section")

    assert len(chunks) > 1
    assert max(len(c["content"]) for c in chunks) <= MAX_SECTION_CHARS


def test_capped_chunks_keep_the_offset_invariant():
    """切分不能破坏 text[start:end].strip() == content，否则页码归属会错。"""
    text = "第一章 总则\n" + ("很多内容" * 2000)

    chunks = Chunker().chunk(text, strategy="section")

    assert all(text[c["start_char"] : c["end_char"]].strip() == c["content"] for c in chunks)
    assert [c["index"] for c in chunks] == list(range(len(chunks)))


def test_capped_chunks_keep_the_section_label():
    text = "第一章 总则\n" + ("内容" * 2000)

    chunks = Chunker().chunk(text, strategy="section")

    assert {c["section"] for c in chunks} == {"第一章"}


def test_split_oversized_returns_contiguous_pieces():
    raw = "甲" * 2000 + "\n" + "乙" * 2000

    pieces = _split_oversized(raw, 1200)

    expected_offsets: list[int] = []
    acc = 0
    for _, piece in pieces:
        expected_offsets.append(acc)
        acc += len(piece)

    assert "".join(piece for _, piece in pieces) == raw
    assert [offset for offset, _ in pieces] == expected_offsets
    assert max(len(p) for _, p in pieces) <= 1200


def test_split_oversized_short_text_is_untouched():
    assert _split_oversized("短文本", 1200) == [(0, "短文本")]


def test_split_oversized_prefers_a_paragraph_boundary():
    """在段落边界切开，而不是把句子劈成两半。"""
    raw = "甲" * 800 + "\n" + "乙" * 800

    pieces = _split_oversized(raw, 1200)

    assert pieces[0][1] == "甲" * 800 + "\n"
    assert pieces[1][1] == "乙" * 800


def test_split_oversized_ignores_a_boundary_in_the_early_window():
    """边界出现在窗口前半段时改用硬切：留一个 50 字的尾巴比 1200 字的整块更差。"""
    raw = "甲" * 100 + "\n" + "乙" * 1900

    pieces = _split_oversized(raw, 1200)

    assert pieces[0][1] == raw[:1200]


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
