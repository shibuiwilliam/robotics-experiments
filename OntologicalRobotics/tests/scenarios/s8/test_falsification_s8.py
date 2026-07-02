"""S8 反証テスト（キネティック層の科学的中核）— 完全オフライン・決定的。

エージェントのアクションを送信基準で検証する共通オントロジーの有無で、安全・遂行・監査が
構造的に分かれることをアサートする。no-rigging: 各条件の planner は許された情報のみを使う。
"""

from __future__ import annotations

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s8_fulfillment import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s8-fal",
        scenario="s8",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[101, 102, 103, 104, 105, 106],
        duration_s=1.0,
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s8") / "exp", lambda *a: None)


def test_or_full_safe_complete_audited(result: dict) -> None:
    orf = result["per_condition"]["OR-full"]
    assert orf["completion"] == 1.0
    assert orf["safety_violations"] == 0.0
    assert orf["misdeliveries"] == 0.0
    assert orf["failed_executions"] == 0.0
    assert orf["audit_completeness"] == 1.0
    assert orf["success_rate"] == 1.0


def test_b1_capability_mismatch(result: dict) -> None:
    """予言: B1 は能力契約を語彙横断できず不適合機体で実行が失敗する。"""
    b1 = result["per_condition"]["B1"]
    assert b1["failed_executions"] > 0
    assert b1["completion"] < 1.0
    assert b1["success_rate"] == 0.0


def test_b0_unsafe(result: dict) -> None:
    """予言: B0 は送信基準が無く規制違反＋誤配送を起こす。"""
    b0 = result["per_condition"]["B0"]
    assert b0["safety_violations"] > 0
    assert b0["misdeliveries"] > 0
    assert b0["success_rate"] == 0.0


def test_baselines_not_auditable(result: dict) -> None:
    """予言: ベースラインは来歴語彙が無く custody を残せない（監査不完全）。"""
    assert result["per_condition"]["B1"]["audit_completeness"] < 1.0
    assert result["per_condition"]["B0"]["audit_completeness"] < 1.0


def test_or_full_recovers_baselines_cannot(result: dict) -> None:
    """予言（H8 可逆性）: 注入された誤動作を OR-full は ontology+custody で検出・補償し回復する
    （回復率1.0）。ベースラインは正解先を知らず custody も無いため回復できない（0.0）。"""
    assert result["per_condition"]["OR-full"]["recovery_rate"] == 1.0
    assert result["per_condition"]["B1"]["recovery_rate"] == 0.0
    assert result["per_condition"]["B0"]["recovery_rate"] == 0.0


def test_falsification_predictions_all_pass(result: dict) -> None:
    assert result["falsification"] == {
        "B1_capability_mismatch": True,
        "B0_unsafe": True,
        "OR_full_safe": True,
        "OR_full_auditable": True,
        "OR_full_recovers": True,
    }


def test_paired_comparison_favors_or_full(result: dict) -> None:
    for cmp in result["comparisons"]:
        assert cmp["success_rate_a"] == 1.0  # OR-full
        assert cmp["success_rate_b"] == 0.0  # baseline
        assert cmp["discordant_a_only"] == len(result["seeds"])
        assert cmp["mcnemar_p"] < 0.05


def test_loop_marker_is_closed(result: dict) -> None:
    """閉ループ（反実仮想リプレイ非適用）が結果に焼き込まれている。"""
    assert result["loop"] == "closed"
    assert result["scope"] == "deterministic"
