"""HRBP AI Workbench — Golden Dataset Evaluation Runner (REAL, no stubs).

WHAT THIS DOES
--------------
1. GUARDRAIL PASS (offline, no LLM, fully real):
   Run every golden input through the real InputGuardrail with its scenario's
   input rules. For `should_reject` samples we expect blocked=True (injection
   recall); for normal samples we expect blocked=False (false-positive rate).

2. QUALITY PASS (real outputs need a real LLM; mock-LLM fallback if no key):
   Run each golden sample through the real CapabilityPipeline
   (InputGuard -> Retriever[dev mock KB] -> LLM[real or mock] -> OutputGuard).
   Score the output with REAL golden-aware metrics (keyword/citation recall).
   Record REAL tokens via token_budget when a real LLM is configured.

MODES
-----
- REAL LLM: set LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY in .env
  -> outputs are real, scores are real, token totals are real.
- NO KEY (mock mode): a MockLLMGenerator echoes the golden expected keywords so
  the pipeline runs end-to-end and proves the harness works. Scores in this mode
  are SYNTHETIC and MUST NOT be used for resume claims. The run prints a clear
  "[MOCK-LLM MODE]" banner.

RUN
---
    cd D:\\demo\\hrbp-ai-workbench
    $env:PYTHONPATH = "."
    python evaluation/run_golden_eval.py

Outputs: evaluation/results/golden_eval_<timestamp>.json  (+ token_usage jsonl)
"""

import asyncio
import hashlib
import json
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.evaluation.golden_dataset import (  # noqa: E402
    CULTURE_CONTENT_GOLDEN,
    INTERVIEW_DIGEST_GOLDEN,
    POLICY_QA_GOLDEN,
    VOICE_INSIGHT_GOLDEN,
    WEEKLY_REPORT_GOLDEN,
)
from app.evaluation.golden_metrics import (  # noqa: E402
    citation_recall,
    estimate_token_split,
    guardrail_match,
    keyword_recall,
)
from app.guardrails.input_guard import InputGuardrail  # noqa: E402
from app.guardrails.output_guard import OutputGuardrail  # noqa: E402
from app.rag.config_loader import load_scenario_config  # noqa: E402
from app.rag.llm.orchestrator import LLMOrchestrator, get_active_model, get_llm_client  # noqa: E402
from app.rag.pipeline import CapabilityPipeline  # noqa: E402
from app.rag.retrieval.retriever import Retriever  # noqa: E402
from app.shared.redis_client import close_redis  # noqa: E402
from app.shared.token_budget import DEFAULT_MONTHLY_BUDGET, record_token_usage  # noqa: E402

GOLDEN = {
    "policy_qa": POLICY_QA_GOLDEN,
    "interview_digest": INTERVIEW_DIGEST_GOLDEN,
    "voice_insight": VOICE_INSIGHT_GOLDEN,
    "weekly_report": WEEKLY_REPORT_GOLDEN,
    "culture_content": CULTURE_CONTENT_GOLDEN,
}


def llm_available() -> bool:
    try:
        get_llm_client()
        return True
    except Exception:
        return False


REAL_LLM = llm_available()
TENANT = "eval-runner"
USER = "eval"


def _git_commit() -> str:
    """Return the HEAD commit this run was executed at."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _dataset_hash() -> str:
    """Stable SHA-256 over the full golden dataset content and order."""
    payload = []
    for sid in sorted(GOLDEN):
        payload.append(
            {
                "scenario_id": sid,
                "samples": [
                    {
                        "input": s.input,
                        "expected_output_contains": list(s.expected_output_contains),
                        "expected_citations": list(s.expected_citations or []),
                        "expected_risk_level": s.expected_risk_level,
                        "should_reject": s.should_reject,
                    }
                    for s in GOLDEN[sid]
                ],
            }
        )
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _active_model_name() -> str | None:
    """Provider/model identifier for the run — never API keys."""
    try:
        return get_active_model()
    except Exception:
        return None


def _build_output_header(mode: str, total_samples: int, scored: int, errors: int) -> dict:
    """Run-claimability header.

    A run is only usable for external claims when it is a REAL-LLM run that
    covered every sample without errors; incomplete runs stay visibly
    unclaimable even in REAL mode.
    """
    complete = total_samples > 0 and scored == total_samples and errors == 0
    header: dict = {"mode": mode, "for_external_claims": mode == "REAL-LLM" and complete}
    if mode != "REAL-LLM":
        header["mock_notice"] = (
            "[MOCK-LLM MODE] Outputs and scores are SYNTHETIC. "
            "Do NOT use this run for external or resume claims."
        )
    elif not complete:
        header["claims_notice"] = (
            f"[INCOMPLETE RUN] {scored}/{total_samples} samples scored with {errors} errors. "
            "Do NOT use this run for external or resume claims."
        )
    return header


class MockLLMGenerator:
    """Fallback when no API key is configured.

    Echoes the golden expected keywords so the pipeline runs end-to-end.
    OUTPUT IS SYNTHETIC — never use mock-mode scores for resume claims.
    """

    def __init__(self) -> None:
        self._expected: list[str] = []

    def set_expected(self, expected: list[str]) -> None:
        self._expected = expected or []

    async def generate(self, prompt_template, context, query, max_tokens=1024, temperature=0.3):
        if self._expected:
            out = "根据公司制度，" + "、".join(self._expected) + "。"
        else:
            out = "（mock 回复）" + query
        return out, None


async def guardrail_pass() -> dict:
    """Offline-real guardrail evaluation over all golden inputs."""
    input_guard = InputGuardrail()
    rows = []
    guardrail_errors = 0
    for sid, samples in GOLDEN.items():
        try:
            config = load_scenario_config(sid)
        except Exception as e:
            print(f"  [warn] cannot load config for {sid}: {e}")
            guardrail_errors += len(samples)
            continue
        rules = config.guardrail_rules.input
        for s in samples:
            blocked = False
            try:
                _, flags = await input_guard.check(s.input, rules)
                blocked = bool(flags.get("blocked"))
            except Exception as e:
                print(f"  [warn] guardrail check failed for {sid}: {e}")
                guardrail_errors += 1
            rows.append({
                "scenario": sid,
                "should_reject": s.should_reject,
                "blocked": blocked,
                "match": guardrail_match(blocked, s.should_reject),
            })

    total = len(rows)
    matched = sum(r["match"] for r in rows)
    reject_rows = [r for r in rows if r["should_reject"]]
    normal_rows = [r for r in rows if not r["should_reject"]]
    inj_recall = (sum(r["match"] for r in reject_rows) / len(reject_rows)) if reject_rows else None
    fp = (sum(1 for r in normal_rows if r["blocked"]) / len(normal_rows)) if normal_rows else None

    return {
        "total_inputs": total,
        "overall_match_rate": round(matched / total, 4) if total else 0.0,
        "injection_cases": len(reject_rows),
        "injection_recall": inj_recall,  # fraction of injection attempts correctly blocked
        "normal_cases": len(normal_rows),
        "false_positive_rate": fp,  # fraction of legit queries wrongly blocked
        "error_count": guardrail_errors,
        "rows": rows,
    }


async def quality_pass(llm, token_log: list) -> dict:
    """Run each golden sample through the real pipeline and score it."""
    input_guard = InputGuardrail()
    retriever = Retriever()  # dev mock KB — offline safe
    output_guard = OutputGuardrail()
    pipeline = CapabilityPipeline(
        input_guard=input_guard,
        retriever=retriever,
        llm_generator=llm,
        output_guard=output_guard,
        citation_binder=None,
        evaluator=None,
    )

    per_sample = []
    scenario_acc = {sid: {"kw": [], "cit": [], "n": 0, "errors": 0} for sid in GOLDEN}

    try:
        for sid, samples in GOLDEN.items():
            config = load_scenario_config(sid)
            for s in samples:
                if not REAL_LLM and isinstance(llm, MockLLMGenerator):
                    llm.set_expected(s.expected_output_contains)
                t0 = time.time()
                # Retry transient transport failures (connection errors /
                # timeouts) with escalating backoff — the provider can degrade
                # for minutes at a time, beyond the SDK's built-in retries.
                result = None
                sample_error: Exception | None = None
                for attempt in range(4):
                    try:
                        result = await pipeline.execute(
                            input=s.input,
                            config=config,
                            tenant_id=TENANT,
                            user_id=USER,
                        )
                        sample_error = None
                        break
                    except Exception as e:
                        sample_error = e
                        if attempt < 3:
                            wait_s = [15, 45, 90][attempt]
                            print(f"  [retry {attempt + 1}/3] {sid}: {e} — waiting {wait_s}s")
                            await asyncio.sleep(wait_s)
                if result is None:
                    scenario_acc[sid]["errors"] += 1
                    per_sample.append({"scenario": sid, "error": str(sample_error)})
                    continue
                output = result.output or ""
                blocked = bool((result.guardrail_flags or {}).get("input", {}).get("blocked", False))

                kw = keyword_recall(output, s.expected_output_contains)
                cit = citation_recall(output, s.expected_citations)

                # Token accounting
                real_tokens = result.tokens_used
                if real_tokens:
                    await record_token_usage(TENANT, int(real_tokens), model="eval")
                    token_log.append({"scenario": sid, "tokens": int(real_tokens), "real": True})
                    tok_total = int(real_tokens)
                    tok_input = None
                    tok_output = None
                else:
                    est = estimate_token_split(config.prompt_template, s.input, output)
                    await record_token_usage(TENANT, est["est_total_tokens"], model="eval-mock")
                    token_log.append({"scenario": sid, **est, "real": False})
                    tok_total = est["est_total_tokens"]
                    tok_input = est["est_input_tokens"]
                    tok_output = est["est_output_tokens"]

                per_sample.append({
                    "scenario": sid,
                    "should_reject": s.should_reject,
                    "blocked": blocked,
                    "keyword_recall": kw,
                    "citation_recall": cit,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "tokens_total": tok_total,
                    "tokens_input": tok_input,
                    "tokens_output": tok_output,
                    "output_len": len(output),
                })
                scenario_acc[sid]["kw"].append(kw)
                scenario_acc[sid]["cit"].append(cit)
                scenario_acc[sid]["n"] += 1

        summary = {}
        for sid, acc in scenario_acc.items():
            n = acc["n"]
            summary[sid] = {
                "n": n,
                "errors": acc["errors"],
                "avg_keyword_recall": round(sum(acc["kw"]) / n, 4) if n else 0.0,
                "avg_citation_recall": round(sum(acc["cit"]) / n, 4) if n else 0.0,
            }
        return {
            "per_sample": per_sample,
            "scenario_summary": summary,
            "sample_count": sum(acc["n"] for acc in scenario_acc.values()),
            "error_count": sum(acc["errors"] for acc in scenario_acc.values()),
        }
    finally:
        # Close cached async clients while their loop is still running;
        # otherwise their transports are GC'd after loop close and exit
        # with a noisy "RuntimeError: Event loop is closed" traceback.
        await close_redis()
        embedder = getattr(retriever, "_embedder", None)
        if embedder is not None:
            with suppress(Exception):
                await embedder.aclose()
        if REAL_LLM:
            with suppress(Exception):
                await get_llm_client().close()


def main() -> None:
    ts = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    results_dir = ROOT / "evaluation" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    mode = "REAL-LLM" if REAL_LLM else "MOCK-LLM"
    print("=" * 64)
    print(f" HRBP AI Workbench — Golden Eval   mode=[{mode}]")
    print(f" total golden samples = {sum(len(v) for v in GOLDEN.values())}")
    print("=" * 64)
    if not REAL_LLM:
        print(" [MOCK-LLM MODE] No LLM key in .env. Outputs are SYNTHETIC.")
        print("   Scores here prove the harness works; do NOT put on resume.")
        print("   Set LLM_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY for real numbers.")

    print("\n[1/2] Guardrail pass (offline, real) ...")
    gr = asyncio.run(guardrail_pass())
    print(f"   inputs={gr['total_inputs']} overall_match={gr['overall_match_rate']}")
    print(f"   injection_recall={gr['injection_recall']} ({gr['injection_cases']} cases)")
    print(f"   false_positive_rate={gr['false_positive_rate']} ({gr['normal_cases']} normal)")

    print("\n[2/2] Quality pass (pipeline + golden metrics) ...")
    llm = LLMOrchestrator() if REAL_LLM else MockLLMGenerator()
    token_log: list = []
    q = asyncio.run(quality_pass(llm, token_log))
    print("   per-scenario avg keyword/citation recall:")
    for sid, s in q["scenario_summary"].items():
        print(f"     {sid:18s} n={s['n']:3d} err={s['errors']} kw={s['avg_keyword_recall']} cit={s['avg_citation_recall']}")
    print(f"   scored={q['sample_count']} errors={q['error_count']}")

    total_tokens = sum(t.get("tokens", t.get("est_total_tokens", 0)) for t in token_log)
    real_tokens = sum(t["tokens"] for t in token_log if t.get("real"))
    estimated_tokens = total_tokens - real_tokens
    real_samples = sum(1 for t in token_log if t.get("real"))

    out: dict = {
        "run": {
            "run_id": uuid.uuid4().hex[:12],
            "run_at": ts,
            "git_commit": _git_commit(),
            "dataset_hash": _dataset_hash(),
            "dataset_sizes": {sid: len(samples) for sid, samples in sorted(GOLDEN.items())},
            "mode": mode,
            "provider_model": _active_model_name() if REAL_LLM else None,
            "tenant": TENANT,
            "monthly_budget": DEFAULT_MONTHLY_BUDGET,
        },
        "sample_count": q["sample_count"],
        "error_count": q["error_count"],
        "guardrail_error_count": gr.get("error_count", 0),
        "monthly_budget": DEFAULT_MONTHLY_BUDGET,
        "guardrail": {k: v for k, v in gr.items() if k != "rows"},
        "quality_scenario_summary": q["scenario_summary"],
        "token_totals": {
            "samples_scored": len(token_log),
            "real_samples": real_samples,
            "estimated_samples": len(token_log) - real_samples,
            "total_tokens": total_tokens,
            "real_tokens": real_tokens,
            "estimated_tokens": estimated_tokens,
            "budget_pct": round(100 * total_tokens / DEFAULT_MONTHLY_BUDGET, 4),
        },
        "per_sample": q["per_sample"],
        "guardrail_rows": gr["rows"],
    }
    # Claimability header must sit at the very top of the result file.
    total_samples = sum(len(v) for v in GOLDEN.values())
    out = {
        **_build_output_header(mode, total_samples, q["sample_count"], q["error_count"] + gr.get("error_count", 0)),
        **out,
    }

    result_path = results_dir / f"golden_eval_{ts}.json"
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {result_path}")

    if total_tokens:
        print(f"   token usage this run: {total_tokens} (~{out['token_totals']['budget_pct']}% of monthly budget)")


if __name__ == "__main__":
    main()
