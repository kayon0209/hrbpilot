"""HRBP AI Workbench — Real, golden-aware evaluation metrics (no stubs).

These are DETERMINISTIC, label-based metrics computed against the hand-authored
golden dataset (expected_output_contains / expected_citations / should_reject).
They are real algorithms, not the 0.7/0.5 placeholder constants that used to live
in auto_eval.py.

NOTE: "real" here means "real computation over real golden labels". The *output*
being scored is real only when produced by a real LLM; in mock-LLM mode the
outputs are synthetic and the resulting scores must NOT be used for resume claims.
"""


def _norm(text: str) -> str:
    return (text or "").lower()


def keyword_recall(output: str, expected: list[str] | None) -> float:
    """Fraction of expected key phrases present (as substrings) in the output.

    Real containment metric: hits / len(expected). If expected is empty, the
    sample imposes no keyword requirement -> trivially satisfied (1.0).
    """
    if not expected:
        return 1.0
    out = _norm(output)
    hits = sum(1 for kw in expected if kw and kw.lower() in out)
    return round(hits / len(expected), 4)


def citation_recall(output: str, expected_citations: list[str] | None) -> float:
    """Fraction of expected citation sources referenced in the output."""
    if not expected_citations:
        return 1.0
    out = _norm(output)
    hits = sum(1 for c in expected_citations if c and c.lower() in out)
    return round(hits / len(expected_citations), 4)


def guardrail_match(blocked: bool, should_reject: bool) -> int:
    """1 when prediction matches expectation, else 0.

    For should_reject samples we expect blocked=True (injection caught).
    For normal samples we expect blocked=False (no false block).
    """
    return 1 if bool(blocked) == bool(should_reject) else 0


def estimate_token_split(system_prompt: str, query: str, output: str) -> dict:
    """Heuristic token split (chars / 4). This is an ESTIMATE, not a measured count.

    Used only in mock-LLM mode where the LLM does not return a real usage total.
    Clearly labeled as estimated wherever reported.
    """
    inp_chars = len(system_prompt or "") + len(query or "")
    out_chars = len(output or "")
    return {
        "est_input_tokens": inp_chars // 4,
        "est_output_tokens": out_chars // 4,
        "est_total_tokens": (inp_chars + out_chars) // 4,
    }
