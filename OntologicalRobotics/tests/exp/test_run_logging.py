"""per-run 構造化ログ（D-2）とコンフィグアーカイブ・記録メタデータ（D-3）の統合テスト。

`run_experiment` は全シナリオ共通のチョークポイントとして、run ディレクトリに
log.jsonl（run_start / progress / run_end）・experiment_config.json・world_config.yaml を
残し、results.json に recording ブロックを焼き込む（CLAUDE.md §4・IMPROVEMENT.md D-2/D-3）。
"""

from __future__ import annotations

import json
from pathlib import Path

from orx.exp.scenario import ScenarioExperimentConfig, run_experiment


def _small_s6_config() -> ScenarioExperimentConfig:
    return ScenarioExperimentConfig(
        name="s6-logging-test",
        scenario="s6",
        world_config="configs/world/s6_recycling.yaml",
        conditions=["OR-full", "OR-vec", "OR-sym"],
        seeds=[601, 602],
        duration_s=0.0,
        knob="visual_noise",
        knob_values=[0.1, 2.0],
        params={
            "primary_sigma": 0.2,
            "confidence_threshold": 0.15,
            "high_cost_threshold": 50.0,
        },
    )


def _events(exp_dir: Path) -> list[dict]:
    lines = (exp_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def test_run_experiment_writes_structured_log(tmp_path: Path) -> None:
    exp_dir, result = run_experiment(_small_s6_config(), tmp_path)

    events = _events(exp_dir)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start"
    assert kinds[-1] == "run_end"
    assert "progress" in kinds  # suite の進捗行が構造化記録される

    start = events[0]
    assert start["scenario"] == "s6"
    assert start["seeds"] == [601, 602]
    assert start["conditions"] == ["OR-full", "OR-vec", "OR-sym"]
    assert start["config_hash"] == result["config_hash"]
    assert all("timestamp" in e for e in events)

    end = events[-1]
    assert end["falsification"] == result["falsification"]
    assert end["scope"] == "deterministic"


def test_run_experiment_archives_configs_and_recording_meta(tmp_path: Path) -> None:
    exp_dir, result = run_experiment(_small_s6_config(), tmp_path)

    # D-3: run ディレクトリ単独で再実行が規定される（コンフィグの自己完結アーカイブ）
    archived = json.loads((exp_dir / "experiment_config.json").read_text(encoding="utf-8"))
    assert archived["scenario"] == "s6"
    assert archived["seeds"] == [601, 602]
    assert (exp_dir / "world_config.yaml").exists()

    # 記録方式の機械可読メタデータ（s6 はエピソード入力を永続化する）
    rec = result["recording"]
    assert rec["scheme"] == "episodes"
    assert rec["experiment_config"] == "experiment_config.json"
    assert rec["world_config"] == "world_config.yaml"
    on_disk = json.loads((exp_dir / "results.json").read_text(encoding="utf-8"))
    assert on_disk["recording"] == rec

    # 主評価＋掃引の全エピソード入力が S1 命名規約で永続化される
    episodes = sorted(p.name for p in (exp_dir / "episodes").glob("*.json"))
    assert "visual_noise-0.2-seed601.json" in episodes  # 主評価（primary_sigma）
    assert "visual_noise-2.0-seed602.json" in episodes  # 掃引端点
