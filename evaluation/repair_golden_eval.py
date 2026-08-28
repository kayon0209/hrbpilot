"""Repair an incomplete golden eval result by re-running ONLY its error samples.

WHY THIS EXISTS
---------------
The full 250-sample run can lose a handful of samples to transient provider
transport failures (connection errors / timeouts) that outlast the in-run
retry budget. Re-running the whole eval for 1-3 lost samples wastes hours and
tokens. This script re-runs only the samples recorded as errors, through the
exact same pipeline and scoring as run_golden_eval.py, and merges the real
results back into the result file.

HONESTY RULES (enforced, not just documented)
---------------------------------------------
- Only per_sample entries carrying an "error" key are eligible. Scored
  entries are NEVER touched or re-run (no cherry-picking low scores).
- Repaired samples are executed for real (real guardrails, retrieval, LLM,
  output guard, golden metrics) and recorded via the same token ledger.
- The merge is disclosed: a "repair_history" entry is written into the
  result file, and the claimability header is recomputed from merged data.
- If a sample still fails after the retry budget, it stays an error entry
  and the file remains visibly unclaimable.

RUN
---
    python evaluation/repair_golden_eval.py [path/to/result.json]

Without a path it picks the newest evaluation/results/golden_eval_*.json.
"""

import asyncio
import json
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from run_golden_eval import (  # noqa: E402
    GOLDEN,
    REAL_LLM,
    TENANT,
    USER,
    _build_output_header,
    _git_commit,
)

from app.evaluation.golden_metrics import (  # noqa: E402
    citation_recall,
    estimate_token_split,
    keyword_recall,
)
from app.guardrails.input_guard import InputGuardrail  # noqa: E402
from app.guardrails.output_guard import OutputGuardrail  # noqa: E402
from app.rag.config_loader import load_scenario_config  # noqa: E402
from app.rag.llm.orchestrator import LLMOrchestrator, get_llm_client  # noqa: E402
from app.rag.pipeline import CapabilityPipeline  # noqa: E402
from app.rag.retrieval.retriever import Retriever  # noqa: E402
from app.shared.redis_client import close_redis  # noqa: E402
from app.shared.token_budget import DEFAULT_MONTHLY_BUDGET, record_token_usage  # noqa: E402

RETRY_WAITS_S = [15, 45, 90]


def locate_error_samples(result: dict) -> list[tuple[int, str, int]]:
    """Map per_sample error entries back to golden samples.

    Entries were appended in GOLDEN iteration order (per scenario, in sample
    order), so the k-th entry of a scenario corresponds to GOLDEN[sid][k].
    Returns (per_sample_index, scenario_id, golden_index). Refuses to run on
    entries that are neither errors nor scored rows.
    """
    per_scenario_seen: dict[str, int] = {sid: 0 for sid in GOLDEN}
    targets: list[tuple[int, str, int]] = []
    for i, entry in enumerate(result.get("per_sample", [])):
        sid = entry.get("scenario")
        if sid not in GOLDEN:
            raise SystemExit(f"[abort] per_sample[{i}] has unknown scenario {sid!r}")
        k = per_scenario_seen[sid]
        per_scenario_seen[sid] += 1
        if "error" in entry:
            if k >= len(GOLDEN[sid]):
                raise SystemExit(f"[abort] more entries than golden samples for {sid}")
            targets.append((i, sid, k))
        elif "keyword_recall" not in entry:
            raise SystemExit(f"[abort] per_sample[{i}] is neither error nor scored; refusing")
    return targets


async def rerun_sample(sid: str, sample) -> dict:
    """Run one golden sample through the real pipeline with retry."""
    pipeline = CapabilityPipeline(
        input_guard=InputGuardrail(),
        retriever=Retriever(),
        llm_generator=LLMOrchestrator(),
        output_guard=OutputGuardrail(),
        citation_binder=None,
        evaluator=None,
    )
    config = load_scenario_config(sid)
    t0 = time.time()
    result = None
    sample_error: Exception | None = None
    for attempt in range(len(RETRY_WAITS_S) + 1):
        try:
            result = await pipeline.execute(input=sample.input, config=config, tenant_id=TENANT, user_id=USER)
            sample_error = None
            break
        except Exception as e:
            sample_error = e
            if attempt < len(RETRY_WAITS_S):
                wait_s = RETRY_WAITS_S[attempt]
                print(f"  [retry {attempt + 1}/{len(RETRY_WAITS_S)}] {sid}: {e} — waiting {wait_s}s")
                await asyncio.sleep(wait_s)
    if result is None:
        return {"scenario": sid, "error": str(sample_error)}

    output = result.output or ""
    blocked = bool((result.guardrail_flags or {}).get("input", {}).get("blocked", False))
    kw = keyword_recall(output, sample.expected_output_contains)
    cit = citation_recall(output, sample.expected_citations)

    real_tokens = result.tokens_used
    if real_tokens:
        await record_token_usage(TENANT, int(real_tokens), model="eval")
        tok_total, tok_input, tok_output = int(real_tokens), None, None
    else:
        est = estimate_token_split(config.prompt_template, sample.input, output)
        await record_token_usage(TENANT, est["est_total_tokens"], model="eval-mock")
        tok_total = est["est_total_tokens"]
        tok_input = est["est_input_tokens"]
        tok_output = est["est_output_tokens"]

    return {
        "scenario": sid,
        "should_reject": sample.should_reject,
        "blocked": blocked,
        "keyword_recall": kw,
        "citation_recall": cit,
        "latency_ms": int((time.time() - t0) * 1000),
        "tokens_total": tok_total,
        "tokens_input": tok_input,
        "tokens_output": tok_output,
        "output_len": len(output),
    }


def rebuild_summaries(result: dict) -> None:
    """Recompute scenario summary, counts and token totals from per_sample."""
    acc = {sid: {"kw": [], "cit": [], "n": 0, "errors": 0} for sid in GOLDEN}
    for entry in result["per_sample"]:
        sid = entry["scenario"]
        if "error" in entry:
            acc[sid]["errors"] += 1
            continue
        acc[sid]["kw"].append(entry["keyword_recall"])
        acc[sid]["cit"].append(entry["citation_recall"])
        acc[sid]["n"] += 1
    result["quality_scenario_summary"] = {
        sid: {
            "n": a["n"],
            "errors": a["errors"],
            "avg_keyword_recall": round(sum(a["kw"]) / a["n"], 4) if a["n"] else 0.0,
            "avg_citation_recall": round(sum(a["cit"]) / a["n"], 4) if a["n"] else 0.0,
        }
        for sid, a in acc.items()
    }
    result["sample_count"] = sum(a["n"] for a in acc.values())
    result["error_count"] = sum(a["errors"] for a in acc.values())


async def repair(path: Path) -> None:
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("for_external_claims"):
        raise SystemExit("[abort] result file is already claimable; nothing to repair")
    if not REAL_LLM:
        raise SystemExit("[abort] no real LLM configured; repair must be a REAL-LLM run")

    targets = locate_error_samples(result)
    if not targets:
        raise SystemExit("[abort] no error samples found in result file")

    print(f"[repair] {path.name}: {len(targets)} error sample(s) to re-run")
    for i, sid, k in targets:
        print(f"  - per_sample[{i}] {sid} sample #{k}: {result['per_sample'][i]['error'][:80]}")

    repaired, still_failed, tokens_added = [], [], 0
    try:
        for i, sid, k in targets:
            sample = GOLDEN[sid][k]
            print(f"[repair] re-running {sid} sample #{k} ...")
            entry = await rerun_sample(sid, sample)
            result["per_sample"][i] = entry
            if "error" in entry:
                print(f"  [still failed] {entry['error'][:120]}")
                still_failed.append({"scenario": sid, "sample_index": k, "error": entry["error"]})
            else:
                print(
                    f"  [ok] kw={entry['keyword_recall']} cit={entry['citation_recall']} tokens={entry['tokens_total']}"
                )
                repaired.append({"scenario": sid, "sample_index": k})
                tokens_added += entry["tokens_total"] or 0
    finally:
        await close_redis()
        with suppress(Exception):
            await get_llm_client().close()

    rebuild_summaries(result)

    tt = result.get("token_totals", {})
    if repaired:
        tt["samples_scored"] = tt.get("samples_scored", 0) + len(repaired)
        tt["real_samples"] = tt.get("real_samples", 0) + len(repaired)
        tt["total_tokens"] = tt.get("total_tokens", 0) + tokens_added
        tt["real_tokens"] = tt.get("real_tokens", 0) + tokens_added
        tt["budget_pct"] = round(100 * tt["total_tokens"] / DEFAULT_MONTHLY_BUDGET, 4)
        result["token_totals"] = tt

    total_samples = sum(len(v) for v in GOLDEN.values())
    header = _build_output_header(
        result["run"]["mode"],
        total_samples,
        result["sample_count"],
        result["error_count"] + result.get("guardrail_error_count", 0),
    )
    for key in ("mode", "for_external_claims", "claims_notice", "mock_notice"):
        result.pop(key, None)
    result = {**header, **result}

    result.setdefault("repair_history", []).append(
        {
            "repaired_at": datetime.now().strftime("%Y%m%dT%H%M%SZ"),
            "git_commit": _git_commit(),
            "repaired": repaired,
            "still_failed": still_failed,
            "tokens_added": tokens_added,
            "note": (
                "Samples listed in 'repaired' failed in the original run due to transient "
                "provider transport errors and were re-run through the identical real pipeline; "
                "scored entries from the original run were not modified."
            ),
        }
    )

    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[done] repaired={len(repaired)} still_failed={len(still_failed)} "
        f"scored={result['sample_count']}/{total_samples} errors={result['error_count']} "
        f"claimable={result['for_external_claims']}"
    )
    print(f"[done] updated {path}")


def main() -> None:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        results = sorted((ROOT / "evaluation" / "results").glob("golden_eval_*.json"))
        if not results:
            raise SystemExit("[abort] no result files found")
        path = results[-1]
    asyncio.run(repair(path))


if __name__ == "__main__":
    main()
