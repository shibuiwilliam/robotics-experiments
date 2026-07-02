"""T5 オンボーディングの統合テスト（オフライン）。"""

from pathlib import Path

import pytest

from orx.common.seeding import SeedTree
from orx.exp.runner import T5ExperimentResult, load_experiment, run_experiment
from orx.exp.suites.t5 import heuristic_infer, onboard, score_against_truth
from orx.sim.fuzz import generate_fuzz_spec, make_samples, truth_mapping_yaml


def test_fuzz_deterministic() -> None:
    r1 = SeedTree(601).child("fuzz").rng()
    r2 = SeedTree(601).child("fuzz").rng()
    assert generate_fuzz_spec(r1, 0) == generate_fuzz_spec(r2, 0)


def test_heuristic_infer_recovers_truth_mapping() -> None:
    rng = SeedTree(601).child("fuzz").rng()
    correct = 0
    total = 10
    for i in range(total):
        spec = generate_fuzz_spec(rng, i)
        samples = make_samples(spec, rng)
        mapping = heuristic_infer(spec.schema_name, samples)
        from orx.exp.suites.t5 import mapping_to_yaml

        accuracy, _, _ = score_against_truth(mapping_to_yaml(mapping), truth_mapping_yaml(spec))
        correct += 1 if accuracy == 1.0 else 0
    assert correct >= 8  # 大半のファズスキーマを完全復元


def test_onboard_result_fields() -> None:
    rng = SeedTree(602).child("fuzz").rng()
    spec = generate_fuzz_spec(rng, 0)
    samples = make_samples(spec, rng)
    result = onboard(spec, samples, mode="heuristic")
    assert result.valid, result.validation_errors
    assert result.field_accuracy > 0.8
    assert result.hand_fix_lines < result.handwritten_lines  # 統合コスト削減 (H1)


def test_t5_experiment_offline(tmp_path: Path) -> None:
    config = load_experiment(Path("configs/experiments/t5_onboarding.yaml"))
    config = config.model_copy(update={"seeds": [601, 602]})
    _, result = run_experiment(config, tmp_path)
    assert isinstance(result, T5ExperimentResult)
    assert result.mean_field_accuracy["heuristic"] > 0.9
    assert result.mean_hand_fix_lines["heuristic"] < result.mean_handwritten_lines / 3
    assert result.valid_rate["heuristic"] > 0.9


def test_llm_mode_with_stub_fails_validation() -> None:
    """stub LLMの提案はJSONにならず、検証エラーとして安全に失敗する。"""
    from orx.common.providers import StubLLMClient

    rng = SeedTree(603).child("fuzz").rng()
    spec = generate_fuzz_spec(rng, 0)
    samples = make_samples(spec, rng)
    result = onboard(spec, samples, mode="llm", llm=StubLLMClient("m"))
    assert not result.valid
    assert result.field_accuracy == 0.0


def test_unknown_mode_rejected() -> None:
    rng = SeedTree(604).child("fuzz").rng()
    spec = generate_fuzz_spec(rng, 0)
    result = onboard(spec, make_samples(spec, rng), mode="bogus")
    assert not result.valid


@pytest.mark.parametrize("units,values", [("m", 1.5), ("cm", 150.0), ("mm", 1500.0)])
def test_unit_inference(units: str, values: float) -> None:
    samples = [{"dets": [{"x": values, "y": values / 2, "z": values / 10, "cf": 0.9}]}]
    mapping = heuristic_infer("u", samples)
    assert mapping.position.units == units
