"""Structured citation metrics per docs/evaluation/CITATION_METRIC_SPEC.md.

These metrics read the PRODUCTION structured citations list
(``QAResponse.citations`` shape) — never the answer text — and implement:

  - ``source_recall``: expected sources covered by the cited documents
  - ``source_precision``: cited documents that actually support the answer
    (here: that were present in the retrieval evidence used to produce it)
  - ``citation_completeness``: every citation object carries the fields
    required by the spec (document_name + section/chunk binding + snippet)

See the spec for N/A vs 0.0 semantics: missing expected labels mean the
metric is NOT APPLICABLE (excluded from aggregation), while a real answer
without citations scores a true 0.0.
"""

import re

# Document-name normalization per spec §4.2: strip whitespace/punctuation,
# unify full/half width, casefold. No fuzzy matching allowed.
_STRIP_CHARS = "《》「」『』<>\"' 　\t（）()[]【】"


def normalize_doc_name(name: str | None) -> str:
    """Normalize a document name for deterministic matching.

    Production chunks carry filenames ("员工手册.pdf") while golden labels
    carry policy titles ("员工手册") — the extension is stripped so both
    sides meet on the title (spec §4.2 normalization).
    """
    if not name:
        return ""
    text = str(name)
    # Full-width to half-width for ASCII range
    text = text.translate({0xFF01 + i: chr(0x21 + i) for i in range(94)})
    text = text.casefold()
    text = text.strip(_STRIP_CHARS)
    # Strip file extension only after punctuation stripping, e.g. ".pdf" / ".docx"
    dot = text.rfind(".")
    if dot > 0 and (len(text) - dot) <= 5 and text[dot + 1:].isalnum():
        text = text[:dot]
    return text.strip(_STRIP_CHARS)


def cited_docs(citations: list[dict]) -> set[str]:
    """Normalized document names present in the structured citations."""
    return {
        normalize_doc_name(c.get("document_name") or c.get("doc") or "")
        for c in citations
        if normalize_doc_name(c.get("document_name") or c.get("doc") or "")
    }


def source_recall(citations: list[dict], expected_sources: list[str] | None) -> float | None:
    """Fraction of expected sources present among the cited documents.

    Returns None (N/A) when the sample expects no sources — excluded from
    aggregation rather than counted as 0.
    """
    if not expected_sources:
        return None
    cited = cited_docs(citations)
    hits = sum(1 for e in expected_sources if normalize_doc_name(e) in cited)
    return round(hits / len(expected_sources), 4)


def source_precision(citations: list[dict], evidence_docs: list[str] | None) -> float | None:
    """Fraction of cited documents that appear in the supporting evidence.

    ``evidence_docs`` are the document names of the retrieval chunks that were
    actually fed to generation (the chunks ``citations`` must mirror). A
    citation pointing outside the evidence set is unbound / fabricated.

    Returns None (N/A) when there are no citations and no evidence (nothing
    to judge). When the answer used evidence but carries no citations, or
    citations exist but no evidence supports them, this is a true 0.0.
    """
    cited = cited_docs(citations)
    if not cited and not evidence_docs:
        return None  # nothing was cited and nothing supported the answer
    if not cited:
        return 0.0  # evidence existed but no citation was given
    evidence = {normalize_doc_name(d) for d in evidence_docs or [] if normalize_doc_name(d)}
    if not evidence:
        return 0.0
    supported = sum(1 for d in cited if d in evidence)
    return round(supported / len(cited), 4)


def citation_completeness(citations: list[dict]) -> float | None:
    """Fraction of citation objects with all spec-required fields bound.

    Required per spec §2: document name plus at least one of
    section / chunk binding, and a content snippet.

    Returns None (N/A) when there are no citations at all — a no-evidence
    answer legitimately has zero citation objects to inspect.
    """
    if not citations:
        return None
    complete = 0
    for c in citations:
        has_doc = bool(normalize_doc_name(c.get("document_name") or c.get("doc") or ""))
        has_binding = bool(
            (c.get("section") or "").strip()
            or (c.get("chunk_id") or "").strip()
        )
        has_snippet = bool((c.get("content_snippet") or c.get("quote") or "").strip())
        if has_doc and has_binding and has_snippet:
            complete += 1
    return round(complete / len(citations), 4)


_CITATION_REF = re.compile(r"【(.+?)】|\[(\d+)\]")


def answer_cites_extra_sources(answer: str, citations: list[dict]) -> list[str]:
    """Detect answer text referencing sources absent from structured citations.

    Anti-cheating check per spec §6: appending document names to the answer
    must not change the structured metrics, and in-text references that the
    structured citations do not back are flagged for the error analysis.
    """
    cited = cited_docs(citations)
    extra = []
    for match in _CITATION_REF.finditer(answer or ""):
        name = match.group(1)
        if name:
            normalized = normalize_doc_name(name)
            if normalized and normalized not in cited:
                extra.append(name)
    return extra
