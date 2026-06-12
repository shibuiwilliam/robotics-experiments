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


class ExperimentConfig(StrictModel):
    name: str
    task: Literal["t1", "t2", "t7"]
    world_config: str  # リポジトリルート相対
    conditions: list[str]
    seeds: list[int]
    duration_s: float
    claim_ttl_s: float = 5.0
    t1: t1_suite.T1Params | None = None
    t2: T2RunParams | None = None
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
    if config.task == "t1" and config.t1 is None:
        raise ValueError("task=t1 には t1: セクションが必要です")
    if config.task == "t2" and config.t2 is None:
        raise ValueError("task=t2 には t2: セクションが必要です")
    if config.task == "t7" and config.t7 is None:
        raise ValueError("task=t7 には t7: セクションが必要です")
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
    if config.task == "t7":
        return _run_t7(config, exp_dir, base_world, notify)
    return _run_t1(config, exp_dir, base_world, notify)


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
                condition, queries, graph, db, docs,
                embedder if condition == "vector-rag" else None,
            )
            correctness[condition].extend(a.correct for a in answers)
        notify(f"  seed={seed}: {len(queries)} クエリ評価完了")

    stats_rng = SeedTree(config.seeds[0]).child("bootstrap").rng()
    baseline = config.conditions[0]
    comparisons = [
        compare_conditions(
            baseline, other, correctness[baseline], correctness[other], stats_rng
        )
        for other in config.conditions[1:]
    ]
    result = T7ExperimentResult(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        seeds=list(config.seeds),
        conditions=list(config.conditions),
        n_queries=len(correctness[baseline]),
        accuracies={
            c: sum(correctness[c]) / len(correctness[c]) for c in config.conditions
        },
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
            graph = t2_suite.prepare_graph(
                run_dir, condition, wms, at_time=final_truth.sim_time
            )
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
        compare_conditions(
            baseline, other, correctness[baseline], correctness[other], stats_rng
        )
        for other in config.conditions[1:]
    ]
    tokens = {
        c: sum(
            a.prompt_tokens + a.completion_tokens
            for a in all_answers
            if a.condition == c
        )
        for c in config.conditions
    }
    accuracies = {
        c: sum(correctness[c]) / len(correctness[c]) for c in config.conditions
    }
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


def render_t2_report(result: T2ExperimentResult) -> str:
    lines = [
        f"# ORX Experiment Report — `{result.exp_id}`",
        "",
        f"- タスク: t2（業務‐物理クエリ） / 質問数: {result.n_questions} / "
        f"LLMモード: {result.llm_mode} / 構成ハッシュ: `{result.config_hash}`",
        "",
        "## 条件別サマリ（H6 正答率 / H7 知識効率）",
        "",
        "| 条件 | 正答率 | 総トークン | 正答率/1kトークン |",
        "|------|--------|-----------|---------------------|",
    ]
    for c in result.conditions:
        eff = result.accuracy_per_1k_tokens[c]
        lines.append(
            f"| {c} | {result.accuracies[c]:.3f} | {result.total_tokens[c]} | "
            f"{eff:.4f} |"
        )
    if result.llm_mode == "stub":
        lines += [
            "",
            "> **注**: LLMモードが stub のため、エージェント条件（OR-full/B1/B0）の"
            "正答率は無意味（ハーネス検証のみ）。OR-reference が表現の上限を示す。"
            "本計測は mode=openai（要コスト承認）で実行する。",
        ]
    lines += ["", "## 対比較（質問単位のMcNemar）", ""]
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


def summarize(result: ExperimentResult | T2ExperimentResult | T7ExperimentResult) -> list[str]:
    """CLI表示用の要約行。"""
    lines: list[str] = []
    if isinstance(result, T7ExperimentResult):
        lines.append("条件別検索正答率:")
        for c in result.conditions:
            lines.append(f"  {c:<14} {result.accuracies[c]:.3f}")
        if result.embedding_mode == "stub":
            lines.append("  ※ stub埋め込み: vector-rag はハーネス検証のみ")
    elif isinstance(result, T2ExperimentResult):
        lines.append("条件別正答率（/1kトークン効率）:")
        for c in result.conditions:
            lines.append(
                f"  {c:<14} {result.accuracies[c]:.3f}"
                f"  (tokens={result.total_tokens[c]})"
            )
        if result.llm_mode == "stub":
            lines.append("  ※ stubモード: エージェント条件はハーネス検証のみ")
    else:
        lines.append("条件別タスク成功率:")
        for c, rate in result.success_rates.items():
            lines.append(f"  {c:<18} {rate:.3f}")
    for cmp in result.comparisons:
        lines.append(
            f"McNemar ({cmp.condition_a} vs {cmp.condition_b}): p = {cmp.mcnemar_p:.2e}"
        )
    return lines


def write_experiment_report(exp_dir: Path, out_dir: Path) -> Path:
    import json

    data = json.loads((exp_dir / RESULTS).read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    if data.get("task") == "t7":
        t7_result = T7ExperimentResult.model_validate(data)
        out_path = out_dir / f"{t7_result.exp_id}.md"
        lines = [
            f"# ORX Experiment Report — `{t7_result.exp_id}`",
            "",
            f"- タスク: t7（SOP検索, H4） / クエリ数: {t7_result.n_queries} / "
            f"埋め込み: {t7_result.embedding_mode}",
            "",
            "| 条件 | 正答率(P@1) |",
            "|------|-------------|",
            *[
                f"| {c} | {t7_result.accuracies[c]:.3f} |"
                for c in t7_result.conditions
            ],
            "",
            *[
                f"- McNemar ({c.condition_a} vs {c.condition_b}): p = {c.mcnemar_p:.2e}"
                for c in t7_result.comparisons
            ],
            "",
        ]
        if t7_result.embedding_mode == "stub":
            lines.insert(
                -1,
                "> **注**: stub埋め込みのため vector-rag はハーネス検証のみ。"
                "本計測は OpenAI 埋め込み（live・キャッシュ記録）で行う。",
            )
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
