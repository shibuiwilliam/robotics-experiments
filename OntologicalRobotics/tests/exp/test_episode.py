"""記録→リプレイの統合テスト（P0完了基準）。

- 忠実度F1: ノイズ0で > 0.95
- リプレイ同一性: 記録→同条件リプレイでメトリクスがバイト一致
完全オフライン（stub埋め込み・LLM不使用）。
"""

from pathlib import Path

import pytest

from orx.common.config import RunConfig, WorldConfig, load_config
from orx.common.paths import repo_root
from orx.exp.episode import record_episode, replay_episode
from orx.replay.io import RunReader, metrics_json

WORLD_PATH = repo_root() / "configs" / "world" / "demo_tiny.yaml"


@pytest.fixture(scope="module")
def recorded_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    runs_root = tmp_path_factory.mktemp("runs")
    world = load_config(WORLD_PATH, WorldConfig)
    config = RunConfig(world=world, duration_s=12.0, root_seed=7)
    run_id, _ = record_episode(config, runs_root)
    return runs_root, run_id


def test_record_produces_complete_run(recorded_run: tuple[Path, str]) -> None:
    runs_root, run_id = recorded_run
    reader = RunReader(runs_root / run_id)
    assert reader.is_complete()
    manifest = reader.manifest()
    assert manifest.root_seed == 7
    assert manifest.config_hash
    assert manifest.llm_mode == "stub"
    assert len(list(reader.events())) > 0
    assert len(list(reader.truth_states())) == 12  # eval 1Hz × 12s
    assert len(list(reader.claims())) > 0


def test_fidelity_exceeds_p0_exit_criterion(recorded_run: tuple[Path, str]) -> None:
    runs_root, run_id = recorded_run
    report = RunReader(runs_root / run_id).metrics()
    assert report.triple_f1 > 0.95, report
    assert report.identity_f1 > 0.95, report
    assert report.position_rmse < 0.01  # ノイズ0
    assert report.transition_miss_rate == 0.0
    assert report.staleness_rate == 0.0


def test_replay_identity_byte_identical(recorded_run: tuple[Path, str]) -> None:
    runs_root, run_id = recorded_run
    run_dir = runs_root / run_id
    recorded = RunReader(run_dir).metrics()
    replayed = replay_episode(run_dir, condition="OR-full")
    assert metrics_json(replayed) == metrics_json(recorded)
    # 2回目のリプレイも同一（決定性）
    replayed2 = replay_episode(run_dir, condition="OR-full")
    assert metrics_json(replayed2) == metrics_json(recorded)


def test_counterfactual_condition_changes_result(recorded_run: tuple[Path, str]) -> None:
    runs_root, run_id = recorded_run
    run_dir = runs_root / run_id
    baseline = RunReader(run_dir).metrics()
    ablated = replay_episode(run_dir, condition="OR-no-identity")
    # 同一性解決オフ → 検出毎に新個体 → 同一性F1が大きく劣化する
    assert ablated.identity_f1 < baseline.identity_f1
    assert (run_dir / "replays" / "OR-no-identity" / "metrics.json").exists()


def test_unknown_condition_rejected(recorded_run: tuple[Path, str]) -> None:
    runs_root, run_id = recorded_run
    with pytest.raises(ValueError, match="未知の条件"):
        replay_episode(runs_root / run_id, condition="nope")


def test_rerecord_does_not_clobber(recorded_run: tuple[Path, str]) -> None:
    runs_root, run_id = recorded_run
    world = load_config(WORLD_PATH, WorldConfig)
    config = RunConfig(world=world, duration_s=2.0, root_seed=7)
    new_id, _ = record_episode(config, runs_root, run_id=run_id)
    assert new_id != run_id  # 既存runを上書きしない
