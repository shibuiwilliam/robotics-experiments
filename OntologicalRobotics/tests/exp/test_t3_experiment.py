"""T3/T6 実験の統合テスト（縮小版・完全オフライン）。

P3完了基準: 能力台帳のBrier（信頼性項）がエピソードと共に改善すること。
T3: capability計画がround-robinを割当正答率で有意に上回ること。
"""

from pathlib import Path

import pytest

from orx.common import iri
from orx.exp.runner import T3ExperimentResult, load_experiment, run_experiment

EXP_CONFIG = Path("configs/experiments/t3_capability.yaml")


@pytest.fixture(scope="module")
def mini_result(tmp_path_factory: pytest.TempPathFactory) -> T3ExperimentResult:
    config = load_experiment(EXP_CONFIG)
    config = config.model_copy(update={"seeds": [401, 402]})
    runs_root = tmp_path_factory.mktemp("t3_runs")
    _, result = run_experiment(config, runs_root)
    assert isinstance(result, T3ExperimentResult)
    return result


def test_brier_reliability_improves(mini_result: T3ExperimentResult) -> None:
    """P3完了基準: 較正（Brier信頼性項）がエピソードと共に改善。"""
    assert mini_result.brier_last5_mean < mini_result.brier_first5_mean
    assert mini_result.calibration_mae_last5 < mini_result.calibration_mae_first5


def test_capability_beats_round_robin(mini_result: T3ExperimentResult) -> None:
    acc = mini_result.allocation_accuracy
    assert acc["capability"] > acc["round-robin"] + 0.15
    assert mini_result.comparisons[0].mcnemar_p < 0.01


def test_replanning_happens_on_failure(mini_result: T3ExperimentResult) -> None:
    assert mini_result.replans > 0


def test_ledger_claims_in_world_graph(tmp_path: Path) -> None:
    """能力台帳が世界グラフの主張（来歴付き）として存在すること。"""
    from orx.common.config import WorldConfig, load_config
    from orx.common.paths import repo_root
    from orx.common.seeding import SeedTree
    from orx.exp.suites.t3 import CapabilityLedger
    from orx.kg.world_graph import WorldGraph

    world = load_config(repo_root() / "configs/world/t3_capability.yaml", WorldConfig)
    graph = WorldGraph()
    ledger = CapabilityLedger(graph, SeedTree(7))
    ledger.declare(world.robots[0])
    ledger.record_outcome("arm_light", "reflective", True, t=1.0)
    ledger.record_outcome("arm_light", "reflective", False, t=2.0)
    graph.refresh_current_graph(at_time=3.0)
    rows = graph.query(
        f"""
        PREFIX orx-cap: <https://orx.local/onto/cap#>
        SELECT ?n ?s WHERE {{
          GRAPH <https://orx.local/id/graph/current> {{
            <{iri.entity("capability", "arm_light-pick_and_place-reflective")}>
              orx-cap:trials ?n ; orx-cap:successes ?s .
          }}
        }}
        """
    )
    assert rows and rows[0]["n"] == "2" and rows[0]["s"] == "1"
    # 推定は宣言事前分布とのベイズ混合
    assert 0.4 < ledger.estimate("arm_light", "reflective") < 0.8
