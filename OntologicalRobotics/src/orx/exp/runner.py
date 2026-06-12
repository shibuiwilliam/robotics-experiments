"""C10 実験ランナー — 条件×シード×タスクの直積を「記録→リプレイ→対比較」で実行。

物理シムの回し直しで条件比較をしない（CLAUDE.md §6）: シード毎に1回記録し、
全条件は同一観測列のリプレイで評価する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from orx.common.config import RunConfig, WorldConfig, config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import FidelityReport, StrictModel
from orx.common.seeding import SeedTree
from orx.exp.episode import CONDITIONS, record_episode
from orx.exp.stats import PairedComparison, compare_conditions
from orx.exp.suites import t1 as t1_suite
from orx.replay.io import next_available_run_dir

RESULTS = "results.json"


class ExperimentConfig(StrictModel):
    name: str
    task: Literal["t1"]
    world_config: str  # リポジトリルート相対
    conditions: list[str]
    seeds: list[int]
    duration_s: float
    claim_ttl_s: float = 5.0
    t1: t1_suite.T1Params


class EpisodeRow(StrictModel):
    seed: int
    condition: str
    success: bool
    identity_f1: float
    triple_f1: float


class ExperimentResult(StrictModel):
    exp_id: str
    name: str
    task: str
    config_hash: str
    seeds: list[int]
    conditions: list[str]
    episodes: list[EpisodeRow]
    comparisons: list[PairedComparison]
    success_rates: dict[str, float]
    identity_f1_means: dict[str, float]


def load_experiment(path: Path) -> ExperimentConfig:
    config = load_config(path, ExperimentConfig)
    unknown = [c for c in config.conditions if c not in CONDITIONS]
    if unknown:
        raise ValueError(f"未知の条件 {unknown}（対応: {sorted(CONDITIONS)}）")
    if len(config.conditions) < 2:
        raise ValueError("条件は2つ以上必要です（対比較のため）")
    return config


def run_experiment(
    config: ExperimentConfig, runs_root: Path, progress: object | None = None
) -> tuple[Path, ExperimentResult]:
    """実験を実行し、exp ディレクトリと結果を返す。

    progress: typer.echo 互換の callable（CLI用、Noneなら無音）。
    """
    notify = progress if callable(progress) else (lambda *_: None)
    exp_hash = config_hash(config)
    exp_dir = next_available_run_dir(runs_root, f"exp-{config.name}-{exp_hash[:8]}")
    exp_dir.mkdir(parents=True, exist_ok=True)
    base_world = load_config(repo_root() / config.world_config, WorldConfig)

    episodes: list[EpisodeRow] = []
    tasks: dict[int, t1_suite.T1Task] = {}
    run_dirs: dict[int, Path] = {}

    for seed in config.seeds:
        world, task = t1_suite.generate_episode(base_world, config.t1, seed)
        tasks[seed] = task
        run_config = RunConfig(
            world=world,
            duration_s=config.duration_s,
            root_seed=seed,
            claim_ttl_s=config.claim_ttl_s,
        )
        run_id, _ = record_episode(
            run_config, exp_dir / "episodes", run_id=f"seed{seed}"
        )
        run_dirs[seed] = exp_dir / "episodes" / run_id
        notify(f"  記録 seed={seed} target={task.target_barcode}")

    by_condition: dict[str, dict[int, t1_suite.T1EpisodeOutcome]] = {
        c: {} for c in config.conditions
    }
    for seed in config.seeds:
        for condition in config.conditions:
            outcome, fidelity = t1_suite.evaluate_condition(
                run_dirs[seed], condition, tasks[seed], config.t1, seed
            )
            by_condition[condition][seed] = outcome
            episodes.append(
                EpisodeRow(
                    seed=seed,
                    condition=condition,
                    success=outcome.success,
                    identity_f1=outcome.identity_f1,
                    triple_f1=outcome.triple_f1,
                )
            )
            _write_condition_fidelity(run_dirs[seed], condition, fidelity)
        notify(f"  リプレイ評価 seed={seed} 完了")

    stats_rng = SeedTree(config.seeds[0]).child("bootstrap").rng()
    baseline = config.conditions[0]
    comparisons: list[PairedComparison] = []
    for other in config.conditions[1:]:
        a = [by_condition[baseline][s].success for s in config.seeds]
        b = [by_condition[other][s].success for s in config.seeds]
        comparisons.append(
            compare_conditions(
                baseline, other, a, b, stats_rng,
                metric_a=[by_condition[baseline][s].identity_f1 for s in config.seeds],
                metric_b=[by_condition[other][s].identity_f1 for s in config.seeds],
                metric_name="identity_f1",
            )
        )

    result = ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        task=config.task,
        config_hash=exp_hash,
        seeds=list(config.seeds),
        conditions=list(config.conditions),
        episodes=episodes,
        comparisons=comparisons,
        success_rates={
            c: sum(o.success for o in by_condition[c].values()) / len(config.seeds)
            for c in config.conditions
        },
        identity_f1_means={
            c: sum(o.identity_f1 for o in by_condition[c].values()) / len(config.seeds)
            for c in config.conditions
        },
    )
    (exp_dir / RESULTS).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return exp_dir, result


def _write_condition_fidelity(run_dir: Path, condition: str, fidelity: FidelityReport) -> None:
    out = run_dir / "replays" / condition
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(
        fidelity.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def render_experiment_report(result: ExperimentResult) -> str:
    lines = [
        f"# ORX Experiment Report — `{result.exp_id}`",
        "",
        f"- タスク: {result.task} / シード数: {len(result.seeds)} / "
        f"構成ハッシュ: `{result.config_hash}`",
        "",
        "## 条件別サマリ",
        "",
        "| 条件 | タスク成功率 | 同一性F1 (平均) |",
        "|------|-------------|------------------|",
    ]
    for c in result.conditions:
        lines.append(
            f"| {c} | {result.success_rates[c]:.3f} | {result.identity_f1_means[c]:.3f} |"
        )
    lines += ["", "## 対比較（対応のある検定）", ""]
    for cmp in result.comparisons:
        lines += [
            f"### {cmp.condition_a} vs {cmp.condition_b}",
            "",
            f"- 成功率: {cmp.success_rate_a:.3f} vs {cmp.success_rate_b:.3f}"
            f"（差のCI95: [{cmp.diff_ci_low:.3f}, {cmp.diff_ci_high:.3f}]）",
            f"- 不一致ペア: {cmp.condition_a}のみ成功 {cmp.discordant_a_only} / "
            f"{cmp.condition_b}のみ成功 {cmp.discordant_b_only}",
            f"- McNemar p = {cmp.mcnemar_p:.2e}",
            f"- Wilcoxon ({cmp.wilcoxon_metric}) p = "
            + (f"{cmp.wilcoxon_p:.2e}" if cmp.wilcoxon_p is not None else "-"),
            "",
        ]
    return "\n".join(lines)


def write_experiment_report(exp_dir: Path, out_dir: Path) -> Path:
    result = ExperimentResult.model_validate_json(
        (exp_dir / RESULTS).read_text(encoding="utf-8")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{result.exp_id}.md"
    out_path.write_text(render_experiment_report(result), encoding="utf-8")
    return out_path
