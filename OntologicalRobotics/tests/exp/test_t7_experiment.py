"""T7 SOP検索の統合テスト（オフライン）。"""

from pathlib import Path

import pytest

from orx.business.sop import SITUATIONS, generate_sops
from orx.common.seeding import SeedTree
from orx.exp.runner import T7ExperimentResult, load_experiment, run_experiment

EXP_CONFIG = Path("configs/experiments/t7_sop.yaml")


def test_sop_generation_deterministic() -> None:
    skus = [{"sku": "SKU-GLS", "name": "glass panel", "weight_kg": 1.2, "fragile": 1}]
    d1 = generate_sops(skus, SeedTree(1))
    d2 = generate_sops(skus, SeedTree(1))
    assert d1 == d2
    assert len(d1) == len(SITUATIONS)
    damage = next(d for d in d1 if d.situation == "damage")
    assert "壊れやすい" in damage.body  # fragile由来の注意書き


def test_t7_onto_guided_ceiling(tmp_path: Path) -> None:
    config = load_experiment(EXP_CONFIG)
    config = config.model_copy(update={"seeds": [301]})
    _, result = run_experiment(config, tmp_path)
    assert isinstance(result, T7ExperimentResult)
    # オントロジー誘導はオフラインで満点（受注→SKU→appliesTo＋situation）
    assert result.accuracies["onto-guided"] == 1.0
    # stub埋め込みの vector-rag はハーネスが完走すること（数値は無意味）
    assert "vector-rag" in result.accuracies
    assert result.embedding_mode == "stub"
    assert result.n_queries >= 20


def test_t7_config_requires_section(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\ntask: t7\nworld_config: configs/world/t2_warehouse.yaml\n"
        "conditions: [onto-guided, vector-rag]\nseeds: [1]\nduration_s: 0.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="t7"):
        load_experiment(bad)
