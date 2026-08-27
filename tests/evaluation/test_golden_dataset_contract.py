"""Golden dataset contract tests (Phase 1.1).

Locks the dataset shape so evaluation numbers stay comparable over time:
counts, scenario integrity, evaluatable fields, uniqueness, provenance
split, and import determinism.
"""

import copy
import importlib

from app.evaluation import golden_dataset as gd

HAND_AUTHORED_SCENARIOS = ("policy_qa", "interview_digest")
PARAMETERIZED_SCENARIOS = ("voice_insight", "weekly_report", "culture_content")


def test_five_scenarios_with_50_samples_each():
    assert set(gd.GOLDEN_DATASETS) == {"policy_qa", "interview_digest", "voice_insight", "weekly_report", "culture_content"}
    for scenario_id, samples in gd.GOLDEN_DATASETS.items():
        assert len(samples) == 50, scenario_id
    assert sum(len(s) for s in gd.GOLDEN_DATASETS.values()) == 250


def test_scenario_id_matches_collection():
    for scenario_id, samples in gd.GOLDEN_DATASETS.items():
        for sample in samples:
            assert sample.scenario_id == scenario_id


def test_inputs_non_empty_and_unique_within_scenario():
    for scenario_id, samples in gd.GOLDEN_DATASETS.items():
        inputs = [s.input for s in samples]
        assert all(i.strip() for i in inputs), scenario_id
        assert len(inputs) == len(set(inputs)), f"duplicate inputs in {scenario_id}"


def test_normal_samples_have_evaluatable_keywords():
    for scenario_id, samples in gd.GOLDEN_DATASETS.items():
        for sample in samples:
            if not sample.should_reject:
                assert sample.expected_output_contains, f"no evaluatable keywords: {scenario_id}/{sample.notes}"


def test_should_reject_samples_may_have_empty_keywords():
    reject_samples = [s for samples in gd.GOLDEN_DATASETS.values() for s in samples if s.should_reject]
    assert reject_samples, "expected at least one injection-refusal sample"
    for sample in reject_samples:
        assert sample.expected_output_contains == []


def test_provenance_split_is_100_hand_authored_and_150_parameterized():
    sources = [s.sample_source for samples in gd.GOLDEN_DATASETS.values() for s in samples]
    assert sources.count("hand_authored") == 100
    assert sources.count("parameterized") == 150
    for scenario_id in HAND_AUTHORED_SCENARIOS:
        assert all(s.sample_source == "hand_authored" for s in gd.GOLDEN_DATASETS[scenario_id])
    for scenario_id in PARAMETERIZED_SCENARIOS:
        assert all(s.sample_source == "parameterized" for s in gd.GOLDEN_DATASETS[scenario_id])


def test_import_is_deterministic():
    def snapshot() -> dict:
        return copy.deepcopy(
            {
                scenario_id: [
                    (s.input, tuple(s.expected_output_contains), s.expected_citations, s.should_reject, s.sample_source)
                    for s in samples
                ]
                for scenario_id, samples in gd.GOLDEN_DATASETS.items()
            }
        )

    before = snapshot()
    importlib.reload(gd)
    after = snapshot()
    assert before == after
