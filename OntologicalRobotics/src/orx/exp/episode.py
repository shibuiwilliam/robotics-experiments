"""C10 — エピソードの記録と反実仮想リプレイのオーケストレーション。

「記録 → 多条件リプレイ → 対比較」が実験の基本単位（PROJECT.md §7.2）。
真値（world.truth / oracle）に触れるのは本モジュールの採点部と oracle のみ。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from orx.anchoring.anchorer import Anchorer
from orx.common.config import RunConfig, config_hash
from orx.common.logging import make_run_logger
from orx.common.schemas import (
    AnchorObservation,
    FidelityReport,
    PerceptionEvent,
    RunManifest,
    StateSnapshot,
)
from orx.common.seeding import SeedTree
from orx.kg.world_graph import WorldGraph
from orx.oracle.fidelity import fidelity_report
from orx.oracle.truth import truth_snapshot
from orx.perception.embedder import make_visual_embedder
from orx.perception.lifting import load_mapping
from orx.perception.pipeline import PerceptionPipeline
from orx.replay.io import RunReader, RunWriter, next_available_run_dir
from orx.sim.sensors import observe
from orx.sim.world import SimWorld

# P0 のリプレイ条件。アブレーション条件の本格化は P1（OR−identity 等）。
CONDITIONS: dict[str, dict[str, object]] = {
    "OR-full": {},
    "OR-no-identity": {"anchoring_enabled": False},
}


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def _timeline(
    settle_s: float, duration_s: float, perception_hz: float, eval_hz: float
) -> list[tuple[float, bool, bool]]:
    """(時刻, 知覚するか, 評価するか) の決定的タイムライン。"""
    ticks: dict[int, tuple[bool, bool]] = {}
    n_perc = int(round(duration_s * perception_hz))
    n_eval = int(round(duration_s * eval_hz))
    # 時刻はミリ秒整数に正規化して浮動小数の合流誤差を避ける
    for k in range(1, n_perc + 1):
        ms = round((settle_s + k / perception_hz) * 1000)
        perc, ev = ticks.get(ms, (False, False))
        ticks[ms] = (True, ev)
    for j in range(1, n_eval + 1):
        ms = round((settle_s + j / eval_hz) * 1000)
        perc, ev = ticks.get(ms, (False, False))
        ticks[ms] = (perc, True)
    return [(ms / 1000.0, p, e) for ms, (p, e) in sorted(ticks.items())]


class _Pipeline:
    """1条件分の知覚後段パイプライン（anchoring→kg）。記録とリプレイで共用。"""

    def __init__(self, config: RunConfig, seeds: SeedTree) -> None:
        self.anchorer = Anchorer(
            config.anchoring, config.world.zones, config.claim_ttl_s, seeds
        )
        self.graph = WorldGraph()
        self.anchor_observations: list[AnchorObservation] = []
        self.world_snapshots: list[StateSnapshot] = []

    def consume_event(self, event: PerceptionEvent, writer: RunWriter | None) -> None:
        result = self.anchorer.process(event)
        for claim in result.claims:
            self.graph.assert_claim(claim)
            if writer:
                writer.append_claim(claim)
        for record in result.records:
            if writer:
                writer.append_anchor_record(record)
        for entity, true_id in zip(
            result.assignments, event.oracle_truth_ids, strict=True
        ):
            if true_id is None:
                continue
            obs = AnchorObservation(
                entity_iri=entity, true_object_id=true_id, sim_time=event.sim_time
            )
            self.anchor_observations.append(obs)
            if writer:
                writer.append_anchor_observation(obs)

    def snapshot(self, at_time: float, writer: RunWriter | None) -> None:
        snap = self.graph.snapshot(at_time)
        self.world_snapshots.append(snap)
        if writer:
            writer.append_world_snapshot(snap)

    def fidelity(self, truth_snaps: list[StateSnapshot], end_time: float) -> FidelityReport:
        return fidelity_report(
            self.world_snapshots,
            truth_snaps,
            self.anchor_observations,
            staleness_rate=self.graph.staleness_rate(end_time),
        )


def record_episode(
    config: RunConfig,
    runs_root: Path,
    run_id: str | None = None,
    condition: str = "OR-full",
) -> tuple[str, FidelityReport]:
    """エピソードを記録し、忠実度レポートを返す。

    出力: data/runs/<run_id>/ に全ストリーム＋マニフェスト＋メトリクス。
    """
    _apply_condition(config, condition)
    base_id = run_id or f"{config.world.name}-seed{config.root_seed}-{config_hash(config)[:8]}"
    run_dir = next_available_run_dir(runs_root, base_id)
    actual_run_id = run_dir.name

    seeds = SeedTree(config.root_seed)
    world = SimWorld(config.world, seeds.child("sim"))
    embedder = make_visual_embedder(config.visual_embedder)
    pipelines = {
        r.name: PerceptionPipeline(
            load_mapping(r.vendor_schema), embedder if r.visual_embedding else None
        )
        for r in config.world.robots
    }
    sense_rngs = {r.name: seeds.child("sense").child(r.name).rng() for r in config.world.robots}
    stage = _Pipeline(config, seeds)

    from datetime import UTC, datetime

    manifest = RunManifest(
        run_id=actual_run_id,
        created_at=datetime.now(UTC).isoformat(),
        orx_version=_orx_version(),
        git_commit=_git_commit(),
        config_hash=config_hash(config),
        root_seed=config.root_seed,
        world_config_name=config.world.name,
        llm_mode=config.provider.mode,
        llm_model=config.provider.llm_model,
        visual_embedder=config.visual_embedder,
        duration_s=config.duration_s,
        condition=condition,
    )

    truth_snaps: list[StateSnapshot] = []
    end_time = config.world.settle_s
    with RunWriter(run_dir) as writer:
        writer.write_manifest(manifest)
        writer.write_config(config)
        logger = make_run_logger(run_dir, run_id=actual_run_id)
        logger.info("record_start", world=config.world.name, seed=config.root_seed)

        world.step_to(config.world.settle_s)
        seq = dict.fromkeys(pipelines, 0)
        timeline = _timeline(
            config.world.settle_s,
            config.duration_s,
            config.world.perception_hz,
            config.world.eval_hz,
        )
        for t, do_perception, do_eval in timeline:
            world.step_to(t)
            end_time = t
            if do_perception:
                for robot in config.world.robots:
                    obs = observe(world, robot, seq[robot.name], sense_rngs[robot.name])
                    seq[robot.name] += 1
                    writer.append_observation(obs)
                    if robot.visual_embedding:
                        image, view = world.render(robot.name)
                    else:
                        image, view = None, None  # LiDAR系: 描画・埋め込み不要
                    event = pipelines[robot.name].process(obs, image, view)
                    writer.append_event(event)
                    stage.consume_event(event, writer)
            if do_eval:
                truth = world.truth()
                writer.append_truth(truth)
                truth_snaps.append(truth_snapshot(truth))
                stage.snapshot(t, writer)
        world.close()

        report = stage.fidelity(truth_snaps, end_time)
        writer.write_metrics(report)
        logger.info("record_done", triple_f1=report.triple_f1, ticks=report.n_eval_ticks)
    return actual_run_id, report


def replay_episode(run_dir: Path, condition: str = "OR-full") -> FidelityReport:
    """記録済みrunの観測列を指定条件で再生し、忠実度を再計算する。

    物理は再実行しない（記録済み知覚イベントを後段に再投入する）。
    出力は run_dir/replays/<condition>/metrics.json。
    """
    reader = RunReader(run_dir)
    config = reader.config()
    _apply_condition(config, condition)
    seeds = SeedTree(config.root_seed)
    stage = _Pipeline(config, seeds)

    truth_states = list(reader.truth_states())
    events = list(reader.events())
    truth_snaps = [truth_snapshot(s) for s in truth_states]

    event_idx = 0
    end_time = truth_states[-1].sim_time if truth_states else 0.0
    for truth in truth_states:
        while event_idx < len(events) and events[event_idx].sim_time <= truth.sim_time + 1e-9:
            stage.consume_event(events[event_idx], writer=None)
            event_idx += 1
        stage.snapshot(truth.sim_time, writer=None)
    # 最終評価ティック後の残イベントも処理する（陳腐化率の整合）
    while event_idx < len(events):
        end_time = max(end_time, events[event_idx].sim_time)
        stage.consume_event(events[event_idx], writer=None)
        event_idx += 1

    report = stage.fidelity(truth_snaps, end_time)
    out_dir = run_dir / "replays" / condition
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return report


def _apply_condition(config: RunConfig, condition: str) -> None:
    if condition not in CONDITIONS:
        raise ValueError(
            f"未知の条件 {condition!r}（対応: {sorted(CONDITIONS)}）"
        )
    overrides = CONDITIONS[condition]
    if overrides.get("anchoring_enabled") is False:
        config.anchoring.enabled = False


def _orx_version() -> str:
    from orx import __version__

    return __version__
