"""T2実験の統合テスト（縮小版・完全オフライン）。

リファレンスソルバ（期待SPARQL/SQL）が表現の上限 ≈1.0 を達成することが
P2の中核検証: 世界グラフ＋アイデンティティ・スレッドは業務‐物理クエリに
答えるのに十分である（H6の表現側）。LLMエージェントの本計測は live で行う。
"""

from pathlib import Path

import pytest

from orx.exp.runner import T2ExperimentResult, load_experiment, run_experiment

EXP_CONFIG = Path("configs/experiments/t2_business.yaml")


@pytest.fixture(scope="module")
def mini_result(tmp_path_factory: pytest.TempPathFactory) -> T2ExperimentResult:
    config = load_experiment(EXP_CONFIG)
    config = config.model_copy(update={"seeds": [201]})
    runs_root = tmp_path_factory.mktemp("t2_runs")
    _, result = run_experiment(config, runs_root)
    assert isinstance(result, T2ExperimentResult)
    return result


def test_reference_solver_achieves_ceiling(mini_result: T2ExperimentResult) -> None:
    """OR表現の上限: 期待SPARQL/SQLで全問正答（識別子スレッド＋同一性解決）。"""
    assert mini_result.accuracies["OR-reference"] >= 0.94, mini_result.accuracies


def test_questions_cover_types_and_negatives(mini_result: T2ExperimentResult) -> None:
    qtypes = {a.qtype for a in mini_result.answers}
    assert {
        "where_order",
        "where_order_negative",
        "orders_in_zone",
        "count_in_zone",
        "fragile_in_zone",
        "destinations_in_zone",
    } <= qtypes
    # 負例（unknown が正答）が含まれる
    negatives = [a for a in mini_result.answers if a.qtype == "where_order_negative"]
    assert negatives and all(a.truth == "unknown" for a in negatives)


def test_stub_agents_run_offline_harness(mini_result: T2ExperimentResult) -> None:
    """stubモードでもエージェント3条件のハーネスが完走する（正答は期待しない）。"""
    for condition in ("OR-full", "B1", "B0"):
        answers = [a for a in mini_result.answers if a.condition == condition]
        assert len(answers) == mini_result.n_questions
    assert mini_result.llm_mode == "stub"


def test_moved_boxes_answered_via_identity_bridge(mini_result: T2ExperimentResult) -> None:
    """搬送された箱（B視界のみ・ID不可読）の受注も reference が正答する（H2×H6）。"""
    moved = [
        a for a in mini_result.answers if a.condition == "OR-reference" and a.truth == "handoff"
    ]
    assert moved, "handoff正答の質問が存在するはず"
    assert all(a.correct for a in moved)
