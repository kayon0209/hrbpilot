"""Refresh golden-eval evidence after a guardrail code change.

A full 250-sample REAL-LLM re-run is wasteful when a guardrail fix only
changes the verdict of a handful of samples: blocked samples never reach
the LLM, and normal-sample verdicts are verified unchanged (0 false
positives). This script:

  1. recomputes the offline guardrail sweep (deterministic, all 250 rows);
  2. diffs verdicts against the base run to find flipped rows;
  3. re-runs ONLY the flipped samples through the real pipeline;
  4. replaces only those per_sample rows, recomputes summaries/tokens;
  5. rebuilds the claimability header and appends repair_history disclosure.

Safety rails: aborts when the dataset hash differs from the base run, when
a normal (non-injection) sample flips (a false-positive regression), or
when any re-run sample errors. Usage:

    python evaluation/refresh_blocked_samples.py evaluation/results/<base>.json
"""

import asyncio
import json
import sys
import time
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from run_golden_eval import (  # noqa: E402
    GOLDEN,
    TENANT,
    USER,
    _active_model_name,
    _build_output_header,
    _dataset_hash,
    _git_commit,
    guardrail_pass,
)

from app.evaluation.golden_metrics import (  # noqa: E402
    citation_recall,
    estimate_token_split,
    keyword_recall,
)
from app.guardrails.input_guard import InputGuardrail  # noqa: E402
from app.guardrails.output_guard import OutputGuardrail  # noqa: E402
from app.rag.config_loader import load_scenario_config  # noqa: E402
from app.rag.llm.orchestrator import LLMOrchestrator  # noqa: E402
from app.rag.pipeline import CapabilityPipeline  # noqa: E402
from app.rag.retrieval.retriever import Retriever  # noqa: E402
from app.shared.redis_client import close_redis  # noqa: E402
from app.shared.token_budget import record_token_usage  # noqa: E402


def _flat_samples() -> list:
    return [s for samples in GOLDEN.values() for s in samples]


async def _rerun_flipped(flipped: list[tuple[int, object]]) -> list[dict]:
    """Re-run flipped samples through the same pipeline as quality_pass."""
    input_guard = InputGuardrail()
    retriever = Retriever()  # dev mock KB — offline safe
    output_guard = OutputGuardrail()
    pipeline = CapabilityPipeline(
        input_guard=input_guard,
        retriever=retriever,
        llm_generator=LLMOrchestrator(),
        output_guard=output_guard,
        citation_binder=None,
        evaluator=None,
    )
    rows: list[dict] = []
    try:
        for idx, sample in flipped:
            config = load_scenario_config(sample.scenario_id)
            t0 = time.time()
            result = await pipeline.execute(
                input=sample.input, config=config, tenant_id=TENANT, user_id=USER
            )
            output = result.output or ""
            blocked = bool((result.guardrail_flags or {}).get("input", {}).get("blocked", False))
            if not blocked:
                raise SystemExit(
                    f"[abort] sample #{idx} ({sample.input!r}) still not blocked — "
                    "fix is ineffective; refusing to write evidence"
                )
            kw = keyword_recall(output, sample.expected_output_contains)
            cit = citation_recall(output, sample.expected_citations)
            est = estimate_token_split(config.prompt_template, sample.input, output)
            await record_token_usage(TENANT, est["est_total_tokens"], model="eval-mock")
            rows.append({
                "scenario": sample.scenario_id,
                "should_reject": sample.should_reject,
                "blocked": blocked,
                "keyword_recall": kw,
                "citation_recall": cit,
                "latency_ms": int((time.time() - t0) * 1000),
                "tokens_total": est["est_total_tokens"],
                "tokens_input": est["est_input_tokens"],
                "tokens_output": est["est_output_tokens"],
                "output_len": len(output),
            })
    finally:
        await close_redis()
        embedder = getattr(retriever, "_embedder", None)
        if embedder is not None:
            with suppress(Exception):
                await embedder.aclose()
    return rows


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: refresh_blocked_samples.py <base_result.json>")
    base_path = ROOT / sys.argv[1]
    base = json.loads(base_path.read_text(encoding="utf-8"))

    if base.get("mode") != "REAL-LLM" or not base.get("for_external_claims"):
        raise SystemExit("[abort] base run is not a claimable REAL-LLM run")
    current_hash = _dataset_hash()
    if base["run"]["dataset_hash"] != current_hash:
        raise SystemExit("[abort] golden dataset changed since the base run — do a full re-run instead")
    model = _active_model_name()
    if model != base["run"]["provider_model"]:
        raise SystemExit(f"[abort] configured model {model!r} != base run {base['run']['provider_model']!r}")

    print("[1/4] Offline guardrail sweep (all 250 inputs) ...")
    gr = asyncio.run(guardrail_pass())
    print(
        f"   injection_recall={gr['injection_recall']} ({gr['injection_cases']} cases), "
        f"false_positive_rate={gr['false_positive_rate']} ({gr['normal_cases']} normal)"
    )

    old_rows = base["guardrail_rows"]
    if len(old_rows) != len(gr["rows"]) or len(gr["rows"]) != len(base["per_sample"]):
        raise SystemExit("[abort] row count mismatch between base run and current dataset")
    samples = _flat_samples()
    flipped: list[tuple[int, object]] = []
    for i, (old, new) in enumerate(zip(old_rows, gr["rows"])):
        if old["should_reject"] != new["should_reject"]:
            raise SystemExit(f"[abort] should_reject flag drifted at row {i}")
        if bool(old["blocked"]) != bool(new["blocked"]):
            if not new["should_reject"]:
                raise SystemExit(f"[abort] normal sample #{i} newly blocked — false-positive regression")
            flipped.append((i, samples[i]))
    if not flipped:
        raise SystemExit("[abort] no verdict flipped — nothing to refresh")
    print(f"[2/4] Verdicts flipped: {len(flipped)} -> {[samples[i].input for i, _ in flipped]}")

    print("[3/4] Re-running flipped samples through the real pipeline ...")
    fresh_rows = asyncio.run(_rerun_flipped(flipped))

    per_sample = list(base["per_sample"])
    for (idx, _), row in zip(flipped, fresh_rows):
        per_sample[idx] = row

    scenario_summary: dict[str, dict] = {}
    for sid in GOLDEN:
        rows = [r for r in per_sample if r["scenario"] == sid]
        errors = sum(1 for r in rows if "error" in r)
        scored = [r for r in rows if "error" not in r]
        scenario_summary[sid] = {
            "n": len(scored),
            "errors": errors,
            "avg_keyword_recall": round(sum(r["keyword_recall"] for r in scored) / len(scored), 4) if scored else 0.0,
            "avg_citation_recall": round(sum(r["citation_recall"] for r in scored) / len(scored), 4) if scored else 0.0,
        }
    total_tokens = sum(r["tokens_total"] for r in per_sample)
    real_samples = sum(1 for r in per_sample if r.get("tokens_input") is None)
    error_count = sum(s["errors"] for s in scenario_summary.values())
    scored_count = sum(s["n"] for s in scenario_summary.values())
    total_samples = sum(len(v) for v in GOLDEN.values())

    ts = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    out: dict = {
        **_build_output_header("REAL-LLM", total_samples, scored_count, error_count + gr.get("error_count", 0)),
        "run": {
            "run_id": uuid.uuid4().hex[:12],
            "run_at": ts,
            "git_commit": _git_commit(),
            "dataset_hash": current_hash,
            "dataset_sizes": base["run"]["dataset_sizes"],
            "mode": "REAL-LLM",
            "provider_model": model,
            "tenant": base["run"]["tenant"],
            "monthly_budget": base["run"]["monthly_budget"],
        },
        "sample_count": scored_count,
        "error_count": error_count,
        "guardrail_error_count": gr.get("error_count", 0),
        "monthly_budget": base["monthly_budget"],
        "guardrail": {k: v for k, v in gr.items() if k != "rows"},
        "quality_scenario_summary": scenario_summary,
        "token_totals": {
            "samples_scored": len(per_sample),
            "real_samples": real_samples,
            "estimated_samples": len(per_sample) - real_samples,
            "total_tokens": total_tokens,
            "real_tokens": sum(r["tokens_total"] for r in per_sample if r.get("tokens_input") is None),
            "estimated_tokens": sum(r["tokens_total"] for r in per_sample if r.get("tokens_input") is not None),
            "budget_pct": round(100 * total_tokens / base["monthly_budget"], 4),
        },
        "per_sample": per_sample,
        "guardrail_rows": gr["rows"],
        "repair_history": base.get("repair_history", []) + [{
            "at": ts,
            "action": "guardrail_fix_partial_rerun",
            "base_run_id": base["run"]["run_id"],
            "base_run_at": base["run"]["run_at"],
            "base_git_commit": base["run"]["git_commit"],
            "git_commit": _git_commit(),
            "reason": (
                "Guardrail fix restored injection detection for three attack shapes "
                "(forget-everything hijack, role-play impersonation, disregard-all-constraints "
                "jailbreak). Only samples whose guardrail verdict flipped were re-run through "
                "the real pipeline (blocked samples never reach the LLM); all other per_sample "
                "rows are carried over unchanged from the base run. Guardrail section was "
                "recomputed offline over all 250 inputs."
            ),
            "rerun_samples": [
                {"index": idx, "scenario": s.scenario_id, "input": s.input} for idx, s in flipped
            ],
            "carried_over_samples": len(per_sample) - len(flipped),
        }],
    }

    result_path = base_path.parent / f"golden_eval_{ts}.json"
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[4/4] wrote {result_path}")
    print(
        f"   claimable={out['for_external_claims']} scored={scored_count}/{total_samples} "
        f"errors={error_count} tokens={total_tokens} ({out['token_totals']['budget_pct']}% budget)"
    )


if __name__ == "__main__":
    main()
