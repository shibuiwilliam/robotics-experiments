"""C10 実験ランナー — 条件×シード×タスクの直積を「記録→リプレイ→対比較」で実行。

物理シムの回し直しで条件比較をしない（CLAUDE.md §6）: シード毎に1回記録し、
全条件は同一観測列のリプレイで評価する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from orx.business.db import generate_wms
from orx.common.config import RunConfig, WorldConfig, config_hash, load_config
from orx.common.paths import repo_root
from orx.common.providers import ProviderConfig, make_llm_client
from orx.common.schemas import FidelityReport, StrictModel
from orx.common.seeding import SeedTree
from orx.exp.episode import CONDITIONS, record_episode
from orx.exp.stats import PairedComparison, compare_conditions
from orx.exp.suites import t1 as t1_suite
from orx.exp.suites import t2 as t2_suite
from orx.exp.suites import t7 as t7_suite
from orx.replay.io import RunReader, next_available_run_dir

RESULTS = "results.json"

# T2のエージェント条件（OR-referenceは決定的ソルバ、他はLLM）
T2_CONDITIONS = ("OR-reference", "OR-full", "B1", "B0")
T7_CONDITIONS = ("onto-guided", "vector-rag")


class T2RunParams(StrictModel):
    provider: ProviderConfig = ProviderConfig()


class T3RunParams(StrictModel):
    n_episodes: int = 30
    epsilon: float = 0.10
    dest_zone: str = "dock"


class T4RunParams(StrictModel):
    knob: str  # DegradationConfig のフィールド名
    values: list[float]


class T5RunParams(StrictModel):
    n_schemas_per_seed: int = 4
    provider: ProviderConfig = ProviderConfig()


class ExperimentConfig(StrictModel):
    name: str
    task: Literal["t1", "t2", "t3", "t4", "t5", "t7"]
    world_config: str  # リポジトリルート相対
    conditions: list[str]
    seeds: list[int]
    duration_s: float
    claim_ttl_s: float = 5.0
    t1: t1_suite.T1Params | None = None
    t2: T2RunParams | None = None
    t3: T3RunParams | None = None
    t4: T4RunParams | None = None
    t5: T5RunParams | None = None
    t7: T2RunParams | None = None  # providerのみ（T2と同形）


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


class T2ExperimentResult(StrictModel):
    task: Literal["t2"] = "t2"
    exp_id: str
    name: str
    config_hash: str
    seeds: list[int]
    conditions: list[str]
    n_questions: int
    accuracies: dict[str, float]
    total_tokens: dict[str, int]
    accuracy_per_1k_tokens: dict[str, float]  # H7 知識効率
    comparisons: list[PairedComparison]
    answers: list[t2_suite.T2Answer]
    llm_mode: str


_VALID_CONDITIONS = {
    "t1": set(CONDITIONS),
    "t2": set(T2_CONDITIONS),
    "t3": {"capability", "round-robin"},
    "t4": set(CONDITIONS),
    "t5": {"heuristic", "llm", "handwritten"},
    "t7": set(T7_CONDITIONS),
}


def load_experiment(path: Path) -> ExperimentConfig:
    config = load_config(path, ExperimentConfig)
    valid = _VALID_CONDITIONS[config.task]
    unknown = [c for c in config.conditions if c not in valid]
    if unknown:
        raise ValueError(f"未知の条件 {unknown}（対応: {sorted(valid)}）")
    if len(config.conditions) < 2:
        raise ValueError("条件は2つ以上必要です（対比較のため）")
    for task_name in ("t1", "t2", "t3", "t4", "t5", "t7"):
        if config.task == task_name and getattr(config, task_name) is None:
            raise ValueError(f"task={task_name} には {task_name}: セクションが必要です")
    if config.task == "t4":
        assert config.t4 is not None
        from orx.common.config import DegradationConfig

        if config.t4.knob not in DegradationConfig.model_fields:
            raise ValueError(
                f"未知の劣化ノブ {config.t4.knob!r}"
                f"（対応: {sorted(DegradationConfig.model_fields)}）"
            )
    return config


def run_experiment(
    config: ExperimentConfig, runs_root: Path, progress: object | None = None
) -> tuple[Path, ExperimentResult | T2ExperimentResult]:
    """実験を実行し、exp ディレクトリと結果を返す。

    progress: typer.echo 互換の callable（CLI用、Noneなら無音）。
    """
    notify = progress if callable(progress) else (lambda *_: None)
    exp_hash = config_hash(config)
    exp_dir = next_available_run_dir(runs_root, f"exp-{config.name}-{exp_hash[:8]}")
    exp_dir.mkdir(parents=True, exist_ok=True)
    base_world = load_config(repo_root() / config.world_config, WorldConfig)
    if config.task == "t2":
        return _run_t2(config, exp_dir, base_world, notify)
    if config.task == "t3":
        return _run_t3(config, exp_dir, base_world, notify)
    if config.task == "t4":
        return _run_t4(config, exp_dir, base_world, notify)
    if config.task == "t5":
        return _run_t5(config, exp_dir, notify)
    if config.task == "t7":
        return _run_t7(config, exp_dir, base_world, notify)
    return _run_t1(config, exp_dir, base_world, notify)


class T5ExperimentResult(StrictModel):
    task: Literal["t5"] = "t5"
    exp_id: str
    name: str
    config_hash: str
    seeds: list[int]
    conditions: list[str]
    n_schemas: int
    mean_field_accuracy: dict[str, float]
    mean_hand_fix_lines: dict[str, float]
    valid_rate: dict[str, float]
    mean_handwritten_lines: float  # 手書きベースラインのコスト（全行）
    mean_elapsed_s: dict[str, float]
    llm_mode: str


def _run_t5(
    config: ExperimentConfig, exp_dir: Path, notify: object
) -> tuple[Path, T5ExperimentResult]:
    """T5: ファズスキーマ群に対する支援マッピング生成の統合コスト測定。"""
    assert config.t5 is not None and callable(notify)
    from orx.exp.suites import t5 as t5_suite
    from orx.sim.fuzz import generate_fuzz_spec, make_samples

    params = config.t5
    modes = [c for c in config.conditions if c != "handwritten"]
    results: dict[str, list[t5_suite.OnboardResult]] = {m: [] for m in modes}
    n_schemas = 0
    for seed in config.seeds:
        rng = SeedTree(seed).child("fuzz").rng()
        for index in range(params.n_schemas_per_seed):
            spec = generate_fuzz_spec(rng, index + seed * 100)
            samples = make_samples(spec, rng)
            n_schemas += 1
            for mode in modes:
                llm = make_llm_client(params.provider) if mode == "llm" else None
                outcome = t5_suite.onboard(spec, samples, mode, llm)
                results[mode].append(outcome)
        notify(f"  seed={seed}: {params.n_schemas_per_seed} スキーマ処理完了")

    handwritten_lines = [r.handwritten_lines for m in modes for r in results[m]]
    result = T5ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        seeds=list(config.seeds),
        conditions=list(config.conditions),
        n_schemas=n_schemas,
        mean_field_accuracy={
            m: round(sum(r.field_accuracy for r in rs) / len(rs), 4) for m, rs in results.items()
        },
        mean_hand_fix_lines={
            m: round(sum(r.hand_fix_lines for r in rs) / len(rs), 3) for m, rs in results.items()
        },
        valid_rate={m: round(sum(r.valid for r in rs) / len(rs), 4) for m, rs in results.items()},
        mean_handwritten_lines=round(sum(handwritten_lines) / len(handwritten_lines), 3),
        mean_elapsed_s={
            m: round(sum(r.elapsed_s for r in rs) / len(rs), 4) for m, rs in results.items()
        },
        llm_mode=params.provider.mode,
    )
    (exp_dir / RESULTS).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return exp_dir, result


class T4ExperimentResult(StrictModel):
    task: Literal["t4"] = "t4"
    exp_id: str
    name: str
    config_hash: str
    seeds: list[int]
    conditions: list[str]
    knob: str
    values: list[float]
    fidelity_curves: dict[str, list[float]]  # 条件 → ノブ値毎の平均トリプルF1
    identity_curves: dict[str, list[float]]
    diff_ci: list[tuple[float, float]]  # (baseline−other) F1差のCI95（ノブ値毎）
    divergence_values: list[float]  # CIが0を跨がないノブ値（乖離領域）


def _run_t4(
    config: ExperimentConfig, exp_dir: Path, base_world: WorldConfig, notify: object
) -> tuple[Path, T4ExperimentResult]:
    """劣化ノブ掃引: 各ノブ値で記録1回→全条件リプレイ→忠実度曲線（H5）。"""
    assert config.t4 is not None and callable(notify)
    import numpy as np

    from orx.exp.episode import replay_episode

    params = config.t4
    baseline, other = config.conditions[0], config.conditions[1]
    f1: dict[str, dict[float, list[float]]] = {
        c: {v: [] for v in params.values} for c in config.conditions
    }
    idf1: dict[str, dict[float, list[float]]] = {
        c: {v: [] for v in params.values} for c in config.conditions
    }

    for value in params.values:
        degradation = base_world.degradation.model_copy(update={params.knob: value})
        world = base_world.model_copy(update={"degradation": degradation})
        for seed in config.seeds:
            run_config = RunConfig(
                world=world,
                duration_s=config.duration_s,
                root_seed=seed,
                claim_ttl_s=config.claim_ttl_s,
            )
            run_id, _ = record_episode(
                run_config,
                exp_dir / "episodes",
                run_id=f"{params.knob}-{value}-seed{seed}",
            )
            run_dir = exp_dir / "episodes" / run_id
            for condition in config.conditions:
                report = replay_episode(run_dir, condition=condition)
                f1[condition][value].append(report.triple_f1)
                idf1[condition][value].append(report.identity_f1)
        means = {c: sum(f1[c][value]) / len(f1[c][value]) for c in config.conditions}
        notify(
            f"  {params.knob}={value}: "
            + " ".join(f"{c}={means[c]:.3f}" for c in config.conditions)
        )

    rng = SeedTree(config.seeds[0]).child("bootstrap").rng()
    diff_ci: list[tuple[float, float]] = []
    divergence: list[float] = []
    for value in params.values:
        diffs = np.array(f1[baseline][value]) - np.array(f1[other][value])
        idx = rng.integers(0, len(diffs), size=(10_000, len(diffs)))
        samples = diffs[idx].mean(axis=1)
        lo, hi = float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))
        diff_ci.append((round(lo, 6), round(hi, 6)))
        if lo > 0:
            divergence.append(value)

    result = T4ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        seeds=list(config.seeds),
        conditions=list(config.conditions),
        knob=params.knob,
        values=list(params.values),
        fidelity_curves={
            c: [round(sum(f1[c][v]) / len(f1[c][v]), 6) for v in params.values]
            for c in config.conditions
        },
        identity_curves={
            c: [round(sum(idf1[c][v]) / len(idf1[c][v]), 6) for v in params.values]
            for c in config.conditions
        },
        diff_ci=diff_ci,
        divergence_values=divergence,
    )
    (exp_dir / RESULTS).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return exp_dir, result


class T3ExperimentResult(StrictModel):
    task: Literal["t3"] = "t3"
    exp_id: str
    name: str
    config_hash: str
    seeds: list[int]
    conditions: list[str]
    n_tasks_total: int
    allocation_accuracy: dict[str, float]
    replans: int
    brier_first5_mean: float
    brier_last5_mean: float
    calibration_mae_first5: float
    calibration_mae_last5: float
    episode_curves: dict[str, list[float]]
    comparisons: list[PairedComparison]


def _run_t3(
    config: ExperimentConfig, exp_dir: Path, base_world: WorldConfig, notify: object
) -> tuple[Path, T3ExperimentResult]:
    assert config.t3 is not None and callable(notify)
    from orx.business.db import WmsRecord
    from orx.exp.suites import t3 as t3_suite
    from orx.exp.suites.t2 import prepare_graph
    from orx.skills.server import SkillServer

    params = config.t3
    paired_all: list[tuple[bool, bool]] = []
    replans_total = 0
    per_episode_brier: list[list[float]] = []  # Brier信頼性項（較正の本体）
    per_episode_raw_brier: list[list[float]] = []
    per_episode_mae: list[list[float]] = []

    for seed in config.seeds:
        run_config = RunConfig(
            world=base_world,
            duration_s=config.duration_s,
            root_seed=seed,
            claim_ttl_s=config.claim_ttl_s,
        )
        run_id, _ = record_episode(run_config, exp_dir / "episodes", run_id=f"seed{seed}")
        run_dir = exp_dir / "episodes" / run_id
        reader = RunReader(run_dir)
        final_truth = list(reader.truth_states())[-1]
        # 知覚→グラフ（計画の窓）。WMSは不要なので空レコードで構築。
        graph = prepare_graph(
            run_dir,
            "OR-full",
            WmsRecord(orders=[], skus=[], instructions=[]),
            at_time=final_truth.sim_time,
        )
        seeds_tree = SeedTree(seed)
        server = SkillServer(base_world, seeds_tree.child("skills").rng())
        ledger = t3_suite.CapabilityLedger(graph, seeds_tree)
        metrics, paired, replans = t3_suite.run_learning_episodes(
            base_world,
            graph,
            server,
            ledger,
            seeds_tree,
            params.n_episodes,
            params.epsilon,
            params.dest_zone,
            graph_time=final_truth.sim_time,
        )
        paired_all.extend(paired)
        replans_total += replans
        per_episode_brier.append([m.brier_reliability for m in metrics])
        per_episode_raw_brier.append([m.brier for m in metrics])
        per_episode_mae.append([m.calibration_mae for m in metrics])
        notify(
            f"  seed={seed}: brier(reliability) "
            f"{metrics[0].brier_reliability:.3f}→{metrics[-1].brier_reliability:.3f} "
            f"calib {metrics[0].calibration_mae:.3f}→{metrics[-1].calibration_mae:.3f}"
        )

    n_episodes = params.n_episodes
    mean_brier = [
        sum(c[m] for c in per_episode_brier) / len(per_episode_brier) for m in range(n_episodes)
    ]
    mean_mae = [
        sum(c[m] for c in per_episode_mae) / len(per_episode_mae) for m in range(n_episodes)
    ]
    cap_ok = [p[0] for p in paired_all]
    rr_ok = [p[1] for p in paired_all]
    stats_rng = SeedTree(config.seeds[0]).child("bootstrap").rng()
    comparisons = [compare_conditions("capability", "round-robin", cap_ok, rr_ok, stats_rng)]
    result = T3ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        seeds=list(config.seeds),
        conditions=list(config.conditions),
        n_tasks_total=len(paired_all),
        allocation_accuracy={
            "capability": sum(cap_ok) / len(cap_ok),
            "round-robin": sum(rr_ok) / len(rr_ok),
        },
        replans=replans_total,
        brier_first5_mean=round(sum(mean_brier[:5]) / 5, 6),
        brier_last5_mean=round(sum(mean_brier[-5:]) / 5, 6),
        calibration_mae_first5=round(sum(mean_mae[:5]) / 5, 6),
        calibration_mae_last5=round(sum(mean_mae[-5:]) / 5, 6),
        episode_curves={
            "brier": [round(v, 6) for v in mean_brier],
            "raw_brier": [
                round(sum(c[m] for c in per_episode_raw_brier) / len(per_episode_raw_brier), 6)
                for m in range(n_episodes)
            ],
            "calibration_mae": [round(v, 6) for v in mean_mae],
        },
        comparisons=comparisons,
    )
    (exp_dir / RESULTS).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return exp_dir, result


class T7ExperimentResult(StrictModel):
    task: Literal["t7"] = "t7"
    exp_id: str
    name: str
    config_hash: str
    seeds: list[int]
    conditions: list[str]
    n_queries: int
    accuracies: dict[str, float]
    comparisons: list[PairedComparison]
    embedding_mode: str


def _run_t7(
    config: ExperimentConfig, exp_dir: Path, base_world: WorldConfig, notify: object
) -> tuple[Path, T7ExperimentResult]:
    """T7はWMS＋SOP＋識別子スレッドのグラフのみで完結する（物理記録不要）。"""
    assert config.t7 is not None and callable(notify)
    from orx.business.db import BusinessDB
    from orx.business.lifting import wms_claims
    from orx.business.sop import generate_sops, sop_claims
    from orx.common.providers import make_text_embedding_client
    from orx.kg.world_graph import WorldGraph

    provider = config.t7.provider
    correctness: dict[str, list[bool]] = {c: [] for c in config.conditions}
    for seed in config.seeds:
        seed_dir = exp_dir / "episodes" / f"seed{seed}"
        seed_dir.mkdir(parents=True, exist_ok=True)
        wms = generate_wms(base_world, SeedTree(seed), seed_dir / "wms.sqlite")
        db = BusinessDB(seed_dir / "wms.sqlite")
        docs = generate_sops(wms.skus, SeedTree(seed))
        graph = WorldGraph()
        for claim in wms_claims(wms, SeedTree(seed)):
            graph.assert_claim(claim)
        for claim in sop_claims(docs, SeedTree(seed)):
            graph.assert_claim(claim)
        graph.refresh_current_graph(at_time=0.0)
        queries = t7_suite.generate_queries(wms, docs)
        embedder = make_text_embedding_client(provider)
        for condition in config.conditions:
            answers = t7_suite.evaluate(
                condition,
                queries,
                graph,
                db,
                docs,
                embedder if condition == "vector-rag" else None,
            )
            correctness[condition].extend(a.correct for a in answers)
        notify(f"  seed={seed}: {len(queries)} クエリ評価完了")

    stats_rng = SeedTree(config.seeds[0]).child("bootstrap").rng()
    baseline = config.conditions[0]
    comparisons = [
        compare_conditions(baseline, other, correctness[baseline], correctness[other], stats_rng)
        for other in config.conditions[1:]
    ]
    result = T7ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        seeds=list(config.seeds),
        conditions=list(config.conditions),
        n_queries=len(correctness[baseline]),
        accuracies={c: sum(correctness[c]) / len(correctness[c]) for c in config.conditions},
        comparisons=comparisons,
        embedding_mode=provider.mode,
    )
    (exp_dir / RESULTS).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return exp_dir, result


def _run_t1(
    config: ExperimentConfig, exp_dir: Path, base_world: WorldConfig, notify: object
) -> tuple[Path, ExperimentResult]:
    assert config.t1 is not None and callable(notify)

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
        run_id, _ = record_episode(run_config, exp_dir / "episodes", run_id=f"seed{seed}")
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
                baseline,
                other,
                a,
                b,
                stats_rng,
                metric_a=[by_condition[baseline][s].identity_f1 for s in config.seeds],
                metric_b=[by_condition[other][s].identity_f1 for s in config.seeds],
                metric_name="identity_f1",
            )
        )

    result = ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        task=config.task,
        config_hash=config_hash(config),
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


def _run_t2(
    config: ExperimentConfig, exp_dir: Path, base_world: WorldConfig, notify: object
) -> tuple[Path, T2ExperimentResult]:
    assert config.t2 is not None and callable(notify)
    provider = config.t2.provider
    all_answers: list[t2_suite.T2Answer] = []
    correctness: dict[str, list[bool]] = {c: [] for c in config.conditions}

    for seed in config.seeds:
        run_config = RunConfig(
            world=base_world,
            duration_s=config.duration_s,
            root_seed=seed,
            claim_ttl_s=config.claim_ttl_s,
        )
        run_id, _ = record_episode(run_config, exp_dir / "episodes", run_id=f"seed{seed}")
        run_dir = exp_dir / "episodes" / run_id
        reader = RunReader(run_dir)
        truth_states = list(reader.truth_states())
        final_truth = truth_states[-1]
        wms = generate_wms(base_world, SeedTree(seed), run_dir / "wms.sqlite")
        from orx.business.db import BusinessDB

        db = BusinessDB(run_dir / "wms.sqlite")
        questions = t2_suite.generate_questions(wms, base_world, final_truth)
        notify(f"  記録 seed={seed}: 質問 {len(questions)} 件")

        for condition in config.conditions:
            graph = t2_suite.prepare_graph(run_dir, condition, wms, at_time=final_truth.sim_time)
            llm = None if condition == "OR-reference" else make_llm_client(provider)
            answers = t2_suite.answer_questions(
                condition, questions, graph, db, base_world, reader, llm
            )
            all_answers.extend(answers)
            correctness[condition].extend(a.correct for a in answers)
            accuracy = sum(a.correct for a in answers) / len(answers)
            notify(f"  {condition:<14} accuracy={accuracy:.3f}")

    n_questions = len(correctness[config.conditions[0]])
    stats_rng = SeedTree(config.seeds[0]).child("bootstrap").rng()
    baseline = config.conditions[0]
    comparisons = [
        compare_conditions(baseline, other, correctness[baseline], correctness[other], stats_rng)
        for other in config.conditions[1:]
    ]
    tokens = {
        c: sum(a.prompt_tokens + a.completion_tokens for a in all_answers if a.condition == c)
        for c in config.conditions
    }
    accuracies = {c: sum(correctness[c]) / len(correctness[c]) for c in config.conditions}
    result = T2ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        seeds=list(config.seeds),
        conditions=list(config.conditions),
        n_questions=n_questions,
        accuracies=accuracies,
        total_tokens=tokens,
        accuracy_per_1k_tokens={
            c: (accuracies[c] / (tokens[c] / 1000.0)) if tokens[c] else 0.0
            for c in config.conditions
        },
        comparisons=comparisons,
        answers=all_answers,
        llm_mode=provider.mode,
    )
    (exp_dir / RESULTS).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return exp_dir, result


def _write_condition_fidelity(run_dir: Path, condition: str, fidelity: FidelityReport) -> None:
    out = run_dir / "replays" / condition
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(fidelity.model_dump_json(indent=2) + "\n", encoding="utf-8")


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
        lines.append(f"| {c} | {result.success_rates[c]:.3f} | {result.identity_f1_means[c]:.3f} |")
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


def render_t2_report(result: T2ExperimentResult) -> str:
    from orx.exp import scope

    scopes = [scope.CEILING, scope.AGENT]  # OR-reference=ceiling, OR-full/B1/B0=agent
    lines = [
        f"# ORX Experiment Report — `{result.exp_id}`",
        "",
        f"- タスク: t2（業務‐物理クエリ） / 質問数: {result.n_questions} / "
        f"LLMモード: {result.llm_mode} / 構成ハッシュ: `{result.config_hash}`",
        "",
        scope.scope_section(scopes, scope.agent_status_for(result.llm_mode)),
        "## 条件別サマリ（OR-reference=表現上限 / OR-full・B1・B0=エージェント）",
        "",
        "| 条件 | 射程 | 正答率 | 総トークン | 正答率/1kトークン |",
        "|------|------|--------|-----------|---------------------|",
    ]
    for c in result.conditions:
        eff = result.accuracy_per_1k_tokens[c]
        cat = "ceiling" if c == "OR-reference" else "agent"
        lines.append(
            f"| {c} | {cat} | {result.accuracies[c]:.3f} | {result.total_tokens[c]} | {eff:.4f} |"
        )
    if result.llm_mode == "stub":
        lines += [
            "",
            scope.stub_warning("正答率・p値・トークン効率"),
            "",
            "> H6 は OR-reference が**表現上限 1.000** を示すのみ（オントロジーが当該クエリを"
            "表現・解決できる証明）。**H6/H7 のエージェントレベル検証は未実行**"
            "（mode=openai・要コスト承認）。H7（トークン効率）は stub では 0 トークンで未計測。",
        ]
    lines += ["", "## 対比較（質問単位のMcNemar）", ""]
    if result.llm_mode == "stub":
        lines.append(
            "> stub モードでは agent 条件が決定的ダミー出力のため、以下の p 値は"
            "**ceiling vs ダミー**の比較で無意味（C2）。live でのみ有効。"
        )
    for cmp in result.comparisons:
        lines += [
            f"- {cmp.condition_a} vs {cmp.condition_b}: "
            f"{cmp.success_rate_a:.3f} / {cmp.success_rate_b:.3f}, "
            f"McNemar p = {cmp.mcnemar_p:.2e}, "
            f"差CI95 [{cmp.diff_ci_low:.3f}, {cmp.diff_ci_high:.3f}]",
        ]
    # 質問タイプ別の内訳
    lines += ["", "## 質問タイプ別正答率", ""]
    qtypes = sorted({a.qtype for a in result.answers})
    header = "| タイプ | " + " | ".join(result.conditions) + " |"
    lines += [header, "|------|" + "------|" * len(result.conditions)]
    for qt in qtypes:
        cells = []
        for c in result.conditions:
            sub = [a for a in result.answers if a.qtype == qt and a.condition == c]
            cells.append(f"{sum(a.correct for a in sub) / len(sub):.2f}" if sub else "-")
        lines.append(f"| {qt} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


AnyResult = (
    ExperimentResult
    | T2ExperimentResult
    | T3ExperimentResult
    | T4ExperimentResult
    | T5ExperimentResult
    | T7ExperimentResult
)


def summarize(result: AnyResult) -> list[str]:
    """CLI表示用の要約行。"""
    lines: list[str] = []
    if isinstance(result, T5ExperimentResult):
        lines.append(
            f"統合コスト比較（{result.n_schemas} 合成スキーマ、"
            f"手書き={result.mean_handwritten_lines:.1f}行）:"
        )
        for m in result.mean_field_accuracy:
            lines.append(
                f"  {m:<10} 精度={result.mean_field_accuracy[m]:.3f} "
                f"修正行={result.mean_hand_fix_lines[m]:.1f} "
                f"検証合格率={result.valid_rate[m]:.2f} "
                f"所要={result.mean_elapsed_s[m] * 1000:.0f}ms"
            )
    elif isinstance(result, T4ExperimentResult):
        lines.append(f"劣化掃引 ({result.knob}) — トリプルF1:")
        for c in result.conditions:
            curve = " ".join(f"{v:.3f}" for v in result.fidelity_curves[c])
            lines.append(f"  {c:<14} {curve}")
        lines.append(
            "乖離領域（CI95が0を跨がないノブ値）: "
            + (", ".join(str(v) for v in result.divergence_values) or "なし")
        )
    elif isinstance(result, T7ExperimentResult):
        lines.append("条件別検索正答率:")
        for c in result.conditions:
            lines.append(f"  {c:<14} {result.accuracies[c]:.3f}")
        if result.embedding_mode == "stub":
            lines.append("  ※ stub埋め込み: vector-rag はハーネス検証のみ")
    elif isinstance(result, T3ExperimentResult):
        lines.append("割当正答率:")
        for c, v in result.allocation_accuracy.items():
            lines.append(f"  {c:<14} {v:.3f}")
        lines.append(
            f"Brier: 序盤5ep平均 {result.brier_first5_mean:.3f} → "
            f"終盤5ep平均 {result.brier_last5_mean:.3f}"
        )
        lines.append(
            f"較正MAE: {result.calibration_mae_first5:.3f} → "
            f"{result.calibration_mae_last5:.3f} / 再計画 {result.replans} 回"
        )
    elif isinstance(result, T2ExperimentResult):
        lines.append("条件別正答率（/1kトークン効率）:")
        for c in result.conditions:
            lines.append(f"  {c:<14} {result.accuracies[c]:.3f}  (tokens={result.total_tokens[c]})")
        if result.llm_mode == "stub":
            lines.append("  ※ stubモード: エージェント条件はハーネス検証のみ")
    else:
        lines.append("条件別タスク成功率:")
        for c, rate in result.success_rates.items():
            lines.append(f"  {c:<18} {rate:.3f}")
    for cmp in getattr(result, "comparisons", []):
        lines.append(f"McNemar ({cmp.condition_a} vs {cmp.condition_b}): p = {cmp.mcnemar_p:.2e}")
    return lines


def write_experiment_report(exp_dir: Path, out_dir: Path) -> Path:
    import json

    data = json.loads((exp_dir / RESULTS).read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    if data.get("task") == "t5":
        t5_result = T5ExperimentResult.model_validate(data)
        out_path = out_dir / f"{t5_result.exp_id}.md"
        modes = list(t5_result.mean_field_accuracy)
        lines = [
            f"# ORX Experiment Report — `{t5_result.exp_id}`",
            "",
            f"- タスク: t5（オンボーディング統合コスト, H1） / 合成スキーマ数: "
            f"{t5_result.n_schemas} / LLM: {t5_result.llm_mode} / "
            f"構成ハッシュ: `{t5_result.config_hash}`",
            "",
            "## 統合コスト比較",
            "",
            "| 方式 | フィールド精度 | 人手修正行数 | 検証合格率 | 所要 [ms] |",
            "|------|---------------|--------------|-----------|-----------|",
            f"| 手書き（ベースライン） | 1.000 | {t5_result.mean_handwritten_lines:.1f}"
            "（全行を書く） | 1.00 | - |",
            *[
                f"| {m} | {t5_result.mean_field_accuracy[m]:.3f} | "
                f"{t5_result.mean_hand_fix_lines[m]:.1f} | "
                f"{t5_result.valid_rate[m]:.2f} | "
                f"{t5_result.mean_elapsed_s[m] * 1000:.0f} |"
                for m in modes
            ],
            "",
            "ハブ&スポーク統合のコスト = 共通オントロジーへのマッピング記述行数。"
            "支援生成は人手記述を「修正行数」まで圧縮する（H1）。",
            "",
        ]
        if t5_result.llm_mode == "openai":
            lines += [
                "> **射程注記**: heuristic（規則ベース）は決定的な**表現上限**。"
                "**llm 行は実LLMによる H1 エージェント検証の実測値**（live, temperature 0・"
                "全応答キャッシュで再現可能）。手書き行数との比較が統合コスト削減を示す。",
                "",
            ]
        else:
            lines += [
                "> **射程注記**: heuristic（規則ベース）は決定的な**表現上限**であり、ファズ空間が"
                "規則の射程に収まる設計のため飽和する。**H1 の llm 支援エージェント検証は未実行**"
                "（live）。手書きとの行数比較は H1 の必要条件を示すが本体検証ではない。",
                "",
            ]
        if t5_result.llm_mode == "stub" and "llm" in modes:
            from orx.exp import scope as _scope

            lines.append(_scope.stub_warning("llm 行の精度・行数"))
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path
    if data.get("task") == "t4":
        t4_result = T4ExperimentResult.model_validate(data)
        out_path = out_dir / f"{t4_result.exp_id}.md"
        baseline, other = t4_result.conditions[0], t4_result.conditions[1]
        lines = [
            f"# ORX Experiment Report — `{t4_result.exp_id}`",
            "",
            f"- タスク: t4（劣化頑健性, H5） / ノブ: **{t4_result.knob}** / "
            f"シード数: {len(t4_result.seeds)} / 構成ハッシュ: `{t4_result.config_hash}`",
            "",
            "## 頑健性曲線（平均トリプルF1）",
            "",
            f"| {t4_result.knob} | "
            + " | ".join(t4_result.conditions)
            + f" | 差({baseline}−{other}) CI95 |",
            "|------|" + "------|" * (len(t4_result.conditions) + 1),
        ]
        for i, v in enumerate(t4_result.values):
            cells = " | ".join(
                f"{t4_result.fidelity_curves[c][i]:.3f}" for c in t4_result.conditions
            )
            lo, hi = t4_result.diff_ci[i]
            lines.append(f"| {v} | {cells} | [{lo:.3f}, {hi:.3f}] |")
        lines += [
            "",
            "## 乖離領域",
            "",
            "OR-full と "
            + other
            + " の忠実度差のCI95が0を上回るノブ値: "
            + (", ".join(str(v) for v in t4_result.divergence_values) or "なし"),
            "",
            "## 同一性F1曲線",
            "",
            f"| {t4_result.knob} | " + " | ".join(t4_result.conditions) + " |",
            "|------|" + "------|" * len(t4_result.conditions),
            *[
                f"| {v} | "
                + " | ".join(f"{t4_result.identity_curves[c][i]:.3f}" for c in t4_result.conditions)
                + " |"
                for i, v in enumerate(t4_result.values)
            ],
            "",
        ]
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path
    if data.get("task") == "t3":
        t3_result = T3ExperimentResult.model_validate(data)
        out_path = out_dir / f"{t3_result.exp_id}.md"
        curve = t3_result.episode_curves["brier"]
        mae_curve = t3_result.episode_curves["calibration_mae"]
        lines = [
            f"# ORX Experiment Report — `{t3_result.exp_id}`",
            "",
            f"- タスク: t3/t6（能力考慮計画・較正, H3） / タスク数: "
            f"{t3_result.n_tasks_total} / 構成ハッシュ: `{t3_result.config_hash}`",
            "",
            "## 割当品質 (T3)",
            "",
            "| 条件 | 割当正答率 |",
            "|------|-----------|",
            *[f"| {c} | {v:.3f} |" for c, v in t3_result.allocation_accuracy.items()],
            "",
            *[
                f"- McNemar ({c.condition_a} vs {c.condition_b}): p = {c.mcnemar_p:.2e}"
                f"（差CI95 [{c.diff_ci_low:.3f}, {c.diff_ci_high:.3f}]）"
                for c in t3_result.comparisons
            ],
            f"- 再計画回数: {t3_result.replans}",
            "",
            "## 較正曲線 (T6)",
            "",
            f"- Brier: 序盤5ep {t3_result.brier_first5_mean:.4f} → "
            f"終盤5ep {t3_result.brier_last5_mean:.4f}",
            f"- 較正MAE: {t3_result.calibration_mae_first5:.4f} → "
            f"{t3_result.calibration_mae_last5:.4f}",
            "",
            "| ep | Brier | 較正MAE |",
            "|----|-------|---------|",
            *[f"| {i + 1} | {curve[i]:.4f} | {mae_curve[i]:.4f} |" for i in range(len(curve))],
            "",
        ]
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path
    if data.get("task") == "t7":
        from orx.exp import scope

        t7_result = T7ExperimentResult.model_validate(data)
        out_path = out_dir / f"{t7_result.exp_id}.md"
        is_stub = t7_result.embedding_mode == "stub"
        scopes = [scope.CEILING, scope.AGENT]  # onto-guided=ceiling, vector-rag=agent(embedding)
        lines = [
            f"# ORX Experiment Report — `{t7_result.exp_id}`",
            "",
            f"- タスク: t7（SOP検索, H4） / クエリ数: {t7_result.n_queries} / "
            f"埋め込み: {t7_result.embedding_mode}",
            "",
            scope.scope_section(scopes, scope.agent_status_for(t7_result.embedding_mode)),
            "| 条件 | 射程 | 正答率(P@1) |",
            "|------|------|-------------|",
            *[
                f"| {c} | {'ceiling' if c == 'onto-guided' else 'agent'} | "
                f"{t7_result.accuracies[c]:.3f} |"
                for c in t7_result.conditions
            ],
            "",
        ]
        if is_stub:
            # C2: stub 埋め込みに対する vector-rag の p 値は決定的走査 vs ランダムノイズで無意味。
            lines += [
                scope.stub_warning("P@1・McNemar p値"),
                "",
                "> vector-rag は stub 埋め込み（sha256由来の無意味ベクトル）のため、正答率と"
                "下記 p 値は**ランダム同等**。**H4 の根拠に引用しない**。"
                "onto-guided 1.000 は誘導検索の**表現上限**のみ（本計測は live 実埋め込み）。",
                "",
                "<!-- C2: stub の p 値は H4 主張に使わない。参考値として折りたたみ表記 -->",
                *[
                    f"- （参考・無意味）{c.condition_a} vs {c.condition_b}: p = {c.mcnemar_p:.2e}"
                    for c in t7_result.comparisons
                ],
            ]
        else:
            lines += [
                *[
                    f"- McNemar ({c.condition_a} vs {c.condition_b}): p = {c.mcnemar_p:.2e}"
                    for c in t7_result.comparisons
                ],
            ]
        lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path
    if data.get("task") == "t2":
        t2_result = T2ExperimentResult.model_validate(data)
        out_path = out_dir / f"{t2_result.exp_id}.md"
        out_path.write_text(render_t2_report(t2_result), encoding="utf-8")
        return out_path
    result = ExperimentResult.model_validate(data)
    out_path = out_dir / f"{result.exp_id}.md"
    out_path.write_text(render_experiment_report(result), encoding="utf-8")
    return out_path
