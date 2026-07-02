"""S4 反証テスト（H2/H5）— 4条件が構造的に異なる失敗をし OR-full が最良。

完全オフライン・決定的。no-rigging: 各機構のオン/オフはシグネチャで強制（ADR-014）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s4_inspection import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s4-fal",
        scenario="s4",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[401, 402, 403, 404, 405, 406],
        duration_s=0.0,
        knob="position_noise",
        knob_values=[0.2, 0.4, 0.6, 0.8],
        params={"position_noise": 0.4},
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s4") / "exp", lambda *a: None)


def test_no_identity_cannot_anchor(result: dict) -> None:
    """同一性解決なしは台帳↔観測を結べない（H2）。"""
    assert result["per_condition"]["OR-no-identity"]["anchor_accuracy"] == 0.0


def test_no_belief_misses_anomalies(result: dict) -> None:
    """信念調停なしは矛盾観測を解けず真の異常を見逃す（H5）。"""
    assert result["per_condition"]["OR-no-belief"]["missed_anomalies"] > 0


def test_or_sym_appearance_fail(result: dict) -> None:
    """署名なし（位置のみ）は位置曖昧で対応付け精度が OR-full に劣る（H2/H4）。"""
    pc = result["per_condition"]
    assert pc["OR-sym"]["anchor_accuracy"] < pc["OR-full"]["anchor_accuracy"]


def test_or_full_best(result: dict) -> None:
    """OR-full は対応・異常正答が最高かつ見逃し0。"""
    pc = result["per_condition"]
    assert pc["OR-full"]["missed_anomalies"] == 0
    assert pc["OR-full"]["anchor_accuracy"] >= pc["OR-sym"]["anchor_accuracy"]
    assert pc["OR-full"]["anomaly_accuracy"] >= pc["OR-no-belief"]["anomaly_accuracy"]
    assert pc["OR-full"]["anomaly_accuracy"] >= pc["OR-no-identity"]["anomaly_accuracy"]


def test_robustness_or_full_beats_sym_across_sweep(result: dict) -> None:
    """位置ノイズ掃引の全域で OR-full の対応付け ≥ OR-sym（署名同定の優位）。"""
    rob = result["robustness"]
    full = rob["anchor_accuracy"]["OR-full"]
    sym = rob["anchor_accuracy"]["OR-sym"]
    assert all(f >= s - 1e-9 for f, s in zip(full, sym, strict=True))


def test_hardcase_i500_i501_position_ambiguity(result: dict) -> None:
    """ハードケース(R-1C): 近接した異常資産 I-501／正常 I-500 で、位置単独の OR-sym は
    取り違えて**異常を見逃す**（missed>0）。OR-full は固有署名で識別を維持し見逃し0。
    位置曖昧で署名が効く、という H2/H4 の論拠を強める（決定的・反証ゲート不変）。"""
    pc = result["per_condition"]
    assert pc["OR-sym"]["missed_anomalies"] > 0  # 位置のみは近接ペアで異常を取りこぼす
    assert pc["OR-full"]["missed_anomalies"] == 0  # 署名で識別 → 見逃さない


def test_all_predictions_pass(result: dict) -> None:
    assert result["falsification"] == {
        "no_identity_cannot_anchor": True,
        "no_belief_misses_anomalies": True,
        "or_sym_appearance_fail": True,
        "or_full_best": True,
    }
