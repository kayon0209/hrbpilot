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
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.evaluation.golden_dataset import (  # noqa: E402
    POLICY_QA_GOLDEN,
    INTERVIEW_DIGEST_GOLDEN,
    VOICE_INSIGHT_GOLDEN,
    WEEKLY_REPORT_GOLDEN,
    CULTURE_CONTENT_GOLDEN,
)
from app.rag.pipeline import CapabilityPipeline  # noqa: E402
from app.rag.config_loader import load_scenario_config  # noqa: E402
from app.rag.retrieval.retriever import Retriever  # noqa: E402
from app.rag.llm.orchestrator import LLMOrchestrator, get_llm_client  # noqa: E402
from app.guardrails.input_guard import InputGuardrail  # noqa: E402
from app.guardrails.output_guard import OutputGuardrail  # noqa: E402
from app.evaluation.golden_metrics import (  # noqa: E402
    keyword_recall,
    citation_recall,
    guardrail_match,
    estimate_token_split,
)
from app.shared.token_budget import record_token_usage, DEFAULT_MONTHLY_BUDGET  # noqa: E402

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
    for sid, samples in GOLDEN.items():
        try:
            config = load_scenario_config(sid)
        except Exception as e:
            print(f"  [warn] cannot load config for {sid}: {e}")
            continue
        rules = config.guardrail_rules.input
        for s in samples:
            blocked = False
            try:
                _, flags = await input_guard.check(s.input, rules)
                blocked = bool(flags.get("blocked"))
            except Exception as e:
                print(f"  [warn] guardrail check failed for {sid}: {e}")
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
    scenario_acc = {sid: {"kw": [], "cit": [], "n": 0} for sid in GOLDEN}

    for sid, samples in GOLDEN.items():
        config = load_scenario_config(sid)
        for s in samples:
            if not REAL_LLM and isinstance(llm, MockLLMGenerator):
                llm.set_expected(s.expected_output_contains)
            t0 = time.time()
            try:
                result = await pipeline.execute(
                    input=s.input,
                    config=config,
                    tenant_id=TENANT,
                    user_id=USER,
                )
                output = result.output or ""
                blocked = bool(
                    (result.guardrail_flags or {})
                    .get("input", {})
                    .get("blocked", False)
                )
            except Exception as e:
                per_sample.append({"scenario": sid, "error": str(e)})
                continue

            kw = keyword_recall(output, s.expected_output_contains)
            cit = citation_recall(output, s.expected_citations)

            # Token accounting
            real_tokens = result.tokens_used
            if real_tokens:
                rec = record_token_usage(TENANT, int(real_tokens), model="eval")
                token_log.append({"scenario": sid, "tokens": int(real_tokens), "real": True})
                tok_total = int(real_tokens)
                tok_input = None
                tok_output = None
            else:
                est = estimate_token_split(config.prompt_template, s.input, output)
                rec = record_token_usage(TENANT, est["est_total_tokens"], model="eval-mock")
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
            "avg_keyword_recall": round(sum(acc["kw"]) / n, 4) if n else 0.0,
            "avg_citation_recall": round(sum(acc["cit"]) / n, 4) if n else 0.0,
        }
    return {"per_sample": per_sample, "scenario_summary": summary}


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
        print(f"     {sid:18s} n={s['n']:3d}  kw={s['avg_keyword_recall']}  cit={s['avg_citation_recall']}")

    total_tokens = sum(t.get("tokens", t.get("est_total_tokens", 0)) for t in token_log)
    real_tokens = sum(t["tokens"] for t in token_log if t.get("real"))
    out = {
        "run_at": ts,
        "mode": mode,
        "monthly_budget": DEFAULT_MONTHLY_BUDGET,
        "guardrail": {k: v for k, v in gr.items() if k != "rows"},
        "quality_scenario_summary": q["scenario_summary"],
        "token_totals": {
            "samples_scored": len(token_log),
            "total_tokens": total_tokens,
            "real_tokens": real_tokens,
            "budget_pct": round(100 * total_tokens / DEFAULT_MONTHLY_BUDGET, 4),
        },
        "per_sample": q["per_sample"],
        "guardrail_rows": gr["rows"],
    }

    result_path = results_dir / f"golden_eval_{ts}.json"
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {result_path}")

    if total_tokens:
        print(f"   token usage this run: {total_tokens} (~{out['token_totals']['budget_pct']}% of monthly budget)")


if __name__ == "__main__":
    main()
