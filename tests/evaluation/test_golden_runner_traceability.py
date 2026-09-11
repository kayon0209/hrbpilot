"""Golden runner traceability tests (Phase 1.3).

The offline runner must produce result files that are traceable (run id,
commit, dataset hash) and honest about mode: MOCK runs are permanently
marked as not usable for external claims.
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "evaluation" / "run_golden_eval.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_golden_eval", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_dataset_hash_is_deterministic_and_content_bound(runner):
    from app.evaluation.golden_dataset import GOLDEN_DATASETS

    first = runner._dataset_hash()
    assert first == runner._dataset_hash()

    payload = []
    for sid in sorted(GOLDEN_DATASETS):
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
                    for s in GOLDEN_DATASETS[sid]
                ],
            }
        )
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    assert first == expected


def test_git_commit_is_real_sha(runner):
    assert len(runner._git_commit()) == 40


def test_mock_header_marks_run_unclaimable(runner):
    header = runner._build_output_header("MOCK-LLM", 250, 250, 0)
    assert header["mode"] == "MOCK-LLM"
    assert header["for_external_claims"] is False
    assert "SYNTHETIC" in header["mock_notice"]


def test_real_full_run_is_claimable(runner):
    header = runner._build_output_header("REAL-LLM", 250, 250, 0)
    assert header["for_external_claims"] is True
    assert "mock_notice" not in header
    assert "claims_notice" not in header


def test_real_incomplete_run_is_not_claimable(runner):
    header = runner._build_output_header("REAL-LLM", 250, 2, 248)
    assert header["for_external_claims"] is False
    assert "INCOMPLETE RUN" in header["claims_notice"]


def test_active_model_name_never_raises(runner):
    assert runner._active_model_name() is None or isinstance(runner._active_model_name(), str)
