"""S1 反証テスト（科学的中核）— B0/B1 が構造的に失敗し OR-full が成功する。

完全オフライン（stub・LLM不使用・決定的リファレンスソルバ）。
no-rigging: 各ソルバはその条件が許す情報のみを引数に取る（ADR-014）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s1_lot_recall import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s1-fal",
        scenario="s1",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[101, 102, 103, 104, 105, 106],
        duration_s=18.0,
        claim_ttl_s=8.0,
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s1") / "exp", lambda *a: None)


def test_or_full_succeeds_noise0(result: dict) -> None:
    """受入: ノイズ0で列挙F1=1.0かつ完遂率100%。"""
    orf = result["per_condition"]["OR-full"]
    assert orf["membership_f1"] == 1.0
    assert orf["completion"] == 1.0
    assert orf["location_accuracy"] == 1.0
    assert orf["false_quarantine_total"] == 0
    assert orf["success_rate"] == 1.0


def test_b0_misses_in_transit(result: dict) -> None:
    """予言: B0 は搬送中・ID不可読個体のロット帰属を解けない（列挙recall<1）。"""
    b0 = result["per_condition"]["B0"]
    assert b0["membership_recall"] < 1.0
    assert b0["completion"] < 1.0
    assert b0["success_rate"] == 0.0


def test_b1_stale_location(result: dict) -> None:
    """予言: B1 は横断同一性が無く搬送済み個体の現在地が陳腐化（membershipは取れても loc<1）。"""
    b1 = result["per_condition"]["B1"]
    assert b1["membership_f1"] == 1.0  # メンバーは業務DBで分かる
    assert b1["location_accuracy"] < 1.0  # しかし現在地は陳腐化
    assert b1["completion"] < 1.0
    assert b1["success_rate"] == 0.0


def test_falsification_predictions_all_pass(result: dict) -> None:
    assert result["falsification"] == {
        "B0_misses_in_transit": True,
        "B1_stale_location": True,
        "OR_full_succeeds": True,
    }


def test_paired_comparison_favors_or_full(result: dict) -> None:
    for cmp in result["comparisons"]:
        assert cmp["success_rate_a"] == 1.0  # OR-full
        assert cmp["success_rate_b"] == 0.0  # baseline
        assert cmp["discordant_a_only"] == len(result["seeds"])
        assert cmp["mcnemar_p"] < 0.05  # 6シードで有意
