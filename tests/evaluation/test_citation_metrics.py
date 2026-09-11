"""Structured citation metric tests (Phase 2, spec §3).

Locks the deterministic semantics of source_recall, source_precision,
citation_completeness, and the N/A vs true-0.0 boundary.
"""

from app.evaluation.citation_metrics import (
    answer_cites_extra_sources,
    citation_completeness,
    cited_docs,
    normalize_doc_name,
    source_precision,
    source_recall,
)


def _c(doc, section="第一条", chunk="c1", snippet="内容"):
    return {"document_name": doc, "section": section, "chunk_id": chunk, "content_snippet": snippet}


def test_normalize_doc_name_unifies_width_case_and_marks():
    assert normalize_doc_name("《员工手册》") == normalize_doc_name("员工手册")
    assert normalize_doc_name("Ｅmployee Handbook") == normalize_doc_name("employee handbook")
    assert normalize_doc_name("  休假 制度 ") == normalize_doc_name("休假 制度")
    assert normalize_doc_name(None) == ""


def test_normalize_doc_name_strips_file_extensions():
    # Production chunk sources are filenames; golden labels are titles.
    assert normalize_doc_name("员工手册.pdf") == normalize_doc_name("员工手册")
    assert normalize_doc_name("考勤管理制度.DOCX") == normalize_doc_name("考勤管理制度")
    # A title that merely contains a dot with a long suffix stays intact.
    assert normalize_doc_name("制度 v1.2 特别版") == normalize_doc_name("制度 v1.2 特别版")


def test_cited_docs_dedupes_normalized_names():
    assert cited_docs([_c("员工手册.pdf"), _c("《员工手册》")]) == {normalize_doc_name("员工手册")}


def test_source_recall_counts_expected_sources_present():
    cites = [_c("员工手册"), _c("考勤管理制度")]
    assert source_recall(cites, ["员工手册", "考勤管理制度"]) == 1.0
    assert source_recall(cites, ["员工手册", "休假管理制度"]) == 0.5
    assert source_recall(cites, ["休假管理制度"]) == 0.0


def test_source_recall_na_when_no_expectation():
    assert source_recall([_c("员工手册")], None) is None
    assert source_recall([], []) is None


def test_source_precision_true_zero_when_evidence_but_no_citations():
    # Answer produced from evidence but citations lost -> real 0.0, not skip.
    assert source_precision([], ["员工手册.pdf"]) == 0.0


def test_source_precision_na_when_neither_citations_nor_evidence():
    assert source_precision([], []) is None
    assert source_precision([], None) is None


def test_source_precision_partial_and_full_support():
    evidence = ["员工手册.pdf", "考勤管理制度.docx"]
    full = [_c("员工手册"), _c("考勤管理制度")]
    half = [_c("员工手册"), _c("休假管理制度")]
    assert source_precision(full, evidence) == 1.0
    assert source_precision(half, evidence) == 0.5


def test_source_precision_zero_when_cited_outside_evidence():
    assert source_precision([_c("虚构文档")], ["员工手册.pdf"]) == 0.0


def test_citation_completeness_requires_all_fields():
    good = _c("员工手册", section="休假", chunk="c9", snippet="年假天数")
    assert citation_completeness([good]) == 1.0
    broken = [
        {"document_name": "", "section": "s", "chunk_id": "c", "content_snippet": "x"},
        {"document_name": "员工手册", "section": "", "chunk_id": "", "content_snippet": "x"},
        {"document_name": "员工手册", "section": "s", "chunk_id": "c", "content_snippet": ""},
    ]
    assert citation_completeness([good, *broken]) == 0.25


def test_citation_completeness_na_when_no_citations():
    assert citation_completeness([]) is None


def test_answer_extra_sources_flags_unbacked_names():
    cites = [_c("员工手册")]
    assert answer_cites_extra_sources("依据【员工手册】作答", cites) == []
    assert answer_cites_extra_sources("依据【员工手册】【考勤管理制度】作答", cites) == ["考勤管理制度"]
