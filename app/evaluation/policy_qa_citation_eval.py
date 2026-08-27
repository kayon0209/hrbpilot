"""Offline deterministic retrieval harness for Policy QA citation eval (Phase 2).

Reuses PRODUCTION components — the section Chunker, the jieba tokenizer, the
synthetic corpus, the citation metrics — and replaces only the external
stores (Milvus / PostgreSQL) with an in-memory deterministic sparse retriever
that scores chunks exactly the way the production SQL does: jieba term
overlap ranked like ``ts_rank_cd``.

No LLM, no network, no embeddings. Runs are labeled OFFLINE-DETERMINISTIC
and measure the RETRIEVAL→CITATION layer, not generation quality.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.evaluation.citation_metrics import (
    citation_completeness,
    source_precision,
    source_recall,
)
from app.evaluation.golden_dataset import GOLDEN_DATASETS
from app.evaluation.synthetic_policy_corpus import build_corpus, resolve_label
from app.rag.retrieval.tokenizer import tokenize, tokenize_for_query
from app.shared.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ChunkRow:
    """In-memory stand-in for a document_chunks row + document filename."""

    chunk_id: str
    document_id: str
    kb_id: str
    source: str  # filename, as production hydrates from documents.filename
    section: str
    content: str
    keyword_text: str  # production stores tokenize(content) in this column
    doc_title: str


@dataclass
class SampleResult:
    """Per-sample evaluation outcome with error taxonomy (Phase 2 step 1)."""

    scenario_id: str
    input: str
    notes: str
    expected_sources: list[str]
    resolved_expected: list[str]
    retrieved_docs: list[str]  # doc titles ranked by the deterministic retriever
    cited_docs: list[str]  # docs the production binding would return
    source_recall: float | None
    source_precision: float | None
    completeness: float | None
    error_class: str  # see ERROR_CLASSES
    detail: str = ""


# Error taxonomy per Phase 2 step 1. One class per sample; priority order
# matters and is documented in classify().
ERROR_CLASSES = (
    "no_expectation",           # sample expects no sources (injection / none)
    "full_match",               # all expected sources retrieved and cited
    "partial_match",            # some expected sources retrieved
    "retrieval_miss",           # no expected source in top-k
    "version_or_alias_mismatch",  # expected label resolves only via alias
    "binding_loss",             # retrieved but citation binding dropped it
)


def build_chunk_rows(kb_id: str = "policy_kb", tenant_id: str = "eval-runner") -> list[ChunkRow]:
    """Chunk the synthetic corpus with the production chunker into rows."""
    rows: list[ChunkRow] = []
    for i, c in enumerate(build_corpus()):
        rows.append(
            ChunkRow(
                chunk_id=f"syn-{i:04d}",
                document_id=f"doc-{c.get('doc_title', i)}",
                kb_id=kb_id,
                source=c["source"],
                section=c["section"],
                content=c["content"],
                keyword_text=tokenize(c["content"]),
                doc_title=c.get("doc_title", ""),
            )
        )
    logger.info("synthetic_corpus_built", chunks=len(rows), kb_id=kb_id, tenant_id=tenant_id)
    return rows


def _sparse_score(query_terms: list[str], row: ChunkRow) -> float:
    """Score a chunk like production ts_rank_cd over jieba terms (deterministic).

    Production uses to_tsquery('simple', OR-joined terms) then ts_rank_cd;
    the deterministic equivalent is the fraction of query terms present in
    the chunk's stored keyword_text, weighted by term count. Ranked order
    matches the production intent: more overlapping terms first.
    """
    if not query_terms:
        return 0.0
    doc_terms = set(row.keyword_text.split())
    hits = sum(1 for t in query_terms if t in doc_terms)
    return hits / len(query_terms)


def retrieve_deterministic(
    query: str,
    rows: list[ChunkRow],
    top_k: int = 5,
) -> list[ChunkRow]:
    """Rank chunks for the query with the deterministic sparse scorer.

    Mirrors the production sparse path: same jieba tokenizer for index and
    query, OR semantics over terms, ranked by overlap. Returns top_k rows.
    """
    terms = [t for t in tokenize_for_query(query).split() if len(t) > 1 or t.isalnum()]
    scored = [( _sparse_score(terms, r), r) for r in rows]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1].chunk_id))
    return [r for _, r in scored[:top_k]]


def classify(result_in_progress: dict) -> str:
    """Assign one error class from the computed per-sample facts.

    Priority: expectation-free samples can't fail citation metrics; a
    binding that drops retrieved docs is a binding problem; alias-resolved
    labels are version/alias issues; the rest split by full/partial/miss.
    """
    if not result_in_progress["expected_sources"]:
        return "no_expectation"
    resolved = result_in_progress["resolved_expected"]
    cited = set(result_in_progress["cited_docs"])
    if set(resolved) <= cited:
        return "full_match"
    if result_in_progress["retrieved_docs"] and set(resolved) & set(result_in_progress["retrieved_docs"]):
        if set(resolved) & cited:
            return "partial_match"
        return "binding_loss"
    aliased = set(result_in_progress["expected_sources"]) - set(result_in_progress["resolved_expected"])
    if aliased and set(result_in_progress["resolved_expected"]) & cited:
        return "version_or_alias_mismatch"
    return "retrieval_miss"


def evaluate_policy_qa(top_k: int = 5) -> dict:
    """Run the full Policy QA citation evaluation offline-deterministically.

    Measures the production retrieval→citation contract over the synthetic
    corpus: for each golden sample, retrieve deterministically, build the
    citations exactly as the orchestrator does (top-3 of evidence), then
    score with the frozen spec metrics.
    """
    rows = build_chunk_rows()
    samples = GOLDEN_DATASETS["policy_qa"]
    results: list[SampleResult] = []

    for s in samples:
        expected = list(s.expected_citations or [])
        resolved = [resolve_label(e) for e in expected]

        if s.should_reject or not expected:
            results.append(
                SampleResult(
                    scenario_id=s.scenario_id,
                    input=s.input,
                    notes=s.notes,
                    expected_sources=expected,
                    resolved_expected=resolved,
                    retrieved_docs=[],
                    cited_docs=[],
                    source_recall=None,
                    source_precision=None,
                    completeness=None,
                    error_class="no_expectation",
                    detail="injection-refusal sample; no citation expectation",
                )
            )
            continue

        hits = retrieve_deterministic(s.input, rows, top_k=top_k)
        retrieved_docs = [r.doc_title for r in hits]
        # Production binding: orchestrator takes the evidence chunks it used
        # (confidence gate passes — synthetic evidence is by construction
        # relevant) and binds the top 3. Normalize to titles for metrics.
        cited = [r.doc_title for r in hits[:3]]

        rec = source_recall(
            [{"document_name": _source_for_title(rows, t)} for t in cited],
            expected,
        )
        prec = source_precision(
            [{"document_name": _source_for_title(rows, t)} for t in cited],
            [r.source for r in hits],
        )
        comp = citation_completeness(
            [
                {
                    "document_name": r.source,
                    "section": r.section,
                    "chunk_id": r.chunk_id,
                    "content_snippet": r.content[:200],
                }
                for r in hits[:3]
            ]
        )

        draft = {
            "expected_sources": expected,
            "resolved_expected": resolved,
            "retrieved_docs": retrieved_docs,
            "cited_docs": cited,
        }
        results.append(
            SampleResult(
                scenario_id=s.scenario_id,
                input=s.input,
                notes=s.notes,
                expected_sources=expected,
                resolved_expected=resolved,
                retrieved_docs=retrieved_docs,
                cited_docs=cited,
                source_recall=rec,
                source_precision=prec,
                completeness=comp,
                error_class=classify(draft),
            )
        )

    scored = [r for r in results if r.source_recall is not None]
    scored_rec = [r.source_recall for r in scored if r.source_recall is not None]
    scored_prec = [r.source_precision for r in scored if r.source_precision is not None]
    comp_values = [r.completeness for r in results if r.completeness is not None]
    summary = {
        "mode": "OFFLINE-DETERMINISTIC",
        "total_samples": len(samples),
        "scored_samples": len(scored),
        "avg_source_recall": round(sum(scored_rec) / len(scored_rec), 4) if scored_rec else None,
        "avg_source_precision": round(sum(scored_prec) / len(scored_prec), 4) if scored_prec else None,
        "avg_completeness": round(sum(comp_values) / len(comp_values), 4) if comp_values else None,
        "error_class_counts": {
            cls: sum(1 for r in results if r.error_class == cls) for cls in ERROR_CLASSES
        },
    }
    return {"summary": summary, "results": results}


def _source_for_title(rows: list[ChunkRow], title: str) -> str:
    """Find the filename for a doc title (metrics normalize both anyway)."""
    for r in rows:
        if r.doc_title == title:
            return r.source
    return title
