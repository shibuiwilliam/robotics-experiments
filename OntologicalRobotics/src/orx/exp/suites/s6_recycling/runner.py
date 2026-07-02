"""S6 リサイクル選別（T13, H4）ランナー: 生成→双対表現3条件→コスト加重採点→反証＋σ掃引。

決定的（視覚埋め込み＋規制推論）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.exp.record import persist_episode
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.stats import PairedComparison, paired_comparisons
from orx.exp.suites.s6_recycling import generator
from orx.exp.suites.s6_recycling.grounding import class_prototypes
from orx.exp.suites.s6_recycling.model import S6World
from orx.exp.suites.s6_recycling.reference import CONDITIONS, assign_lane
from orx.exp.suites.s6_recycling.scorer import S6ConditionScore, score

WORLD = "configs/world/s6_recycling.yaml"
_DEFAULT_THETA = 0.15
_DEFAULT_PRIMARY_SIGMA = 0.2
_DEFAULT_HIGH_COST = 50.0

from orx.exp.suites.s6_recycling.agent import AGENT_CONDITIONS  # noqa: E402

META = ScenarioMeta(
    id="s6",
    suite="T13",
    slug="recycling",
    title="リサイクル選別：法規制と知覚の橋渡し",
    tier="A",
    touchstones=["双対表現（記号×ベクトル）"],
    hypotheses=["H4"],
    conditions=CONDITIONS + AGENT_CONDITIONS,
    status="implemented",
)


class S6ConditionAgg(StrictModel):
    mean_cost: float
    high_cost_errors: int
    escalation_rate: float
    throughput: float
    routed_accuracy: float
    brier: float


class S6Result(StrictModel):
    scenario: str = "s6"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    primary_sigma: float
    per_condition: dict[str, S6ConditionAgg]
    comparisons: list[PairedComparison] = []
    falsification: dict[str, bool]
    robustness: dict
    scope: str = "deterministic"  # "deterministic" | "agent" (R-3a)
    total_tokens: dict[str, int] = {}


def _eval_seeds(
    world: S6World,
    seeds: list[int],
    sigma: float,
    theta: float,
    high_cost: float,
    record_dir: Path | None = None,
    knob: str | None = None,
) -> tuple[dict[str, S6ConditionAgg], dict[str, list[S6ConditionScore]]]:
    protos = class_prototypes(world.classes, world.embedding_dim)
    per: dict[str, list[S6ConditionScore]] = {c: [] for c in CONDITIONS}
    for seed in seeds:
        ep = generator.generate_episode(world, seed, sigma)
        persist_episode(record_dir, ep, seed, knob, sigma)  # D-3
        for c in CONDITIONS:
            assignments = [(obj, *assign_lane(c, obj, world, protos, theta)) for obj in ep.objects]
            per[c].append(score(assignments, world, high_cost))
    agg: dict[str, S6ConditionAgg] = {}
    for c in CONDITIONS:
        rows = per[c]
        n = len(rows)
        agg[c] = S6ConditionAgg(
            mean_cost=round(sum(r.mean_cost for r in rows) / n, 6),
            high_cost_errors=sum(r.high_cost_errors for r in rows),
            escalation_rate=round(sum(r.escalation_rate for r in rows) / n, 6),
            throughput=round(sum(r.throughput for r in rows) / n, 6),
            routed_accuracy=round(sum(r.routed_accuracy for r in rows) / n, 6),
            brier=round(sum(r.brier for r in rows) / n, 6),
        )
    return agg, per


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    if any(c.endswith("-llm") for c in config.conditions):
        return run_agent(config, exp_dir, notify)
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S6World)
    theta = float(config.params.get("confidence_threshold", _DEFAULT_THETA))
    high_cost = float(config.params.get("high_cost_threshold", _DEFAULT_HIGH_COST))
    primary_sigma = float(config.params.get("primary_sigma", _DEFAULT_PRIMARY_SIGMA))

    per_condition, per_seed = _eval_seeds(
        world,
        config.seeds,
        primary_sigma,
        theta,
        high_cost,
        record_dir=exp_dir,
        knob="visual_noise",
    )
    for c in CONDITIONS:
        a = per_condition[c]
        notify_fn(
            f"  {c:<8} mean_cost={a.mean_cost:.3f} high_cost_err={a.high_cost_errors} "
            f"escalation={a.escalation_rate:.2f} throughput={a.throughput:.2f}"
        )

    falsification = {
        "OR_sym_throughput_collapse": per_condition["OR-sym"].throughput <= 1e-9,
        "OR_vec_high_cost_misroute": per_condition["OR-vec"].high_cost_errors > 0,
        "OR_full_lowest_cost": (
            per_condition["OR-full"].mean_cost < per_condition["OR-vec"].mean_cost
            and per_condition["OR-full"].mean_cost < per_condition["OR-sym"].mean_cost
            and per_condition["OR-full"].high_cost_errors == 0
        ),
    }

    # 対比較（OR-full vs OR-vec/OR-sym, R-3d）: seed 毎の成否＝高コスト誤り0、指標=平均コスト。
    success_by = {c: [s.high_cost_errors == 0 for s in per_seed[c]] for c in CONDITIONS}
    metric_by = {c: [s.mean_cost for s in per_seed[c]] for c in CONDITIONS}
    comparisons = paired_comparisons(
        "OR-full",
        [c for c in CONDITIONS if c != "OR-full"],
        success_by,
        metric_by,
        "mean_cost",
        config.seeds[0],
    )

    knob = config.knob or "visual_noise"
    values = config.knob_values or [0.1, 0.5, 1.0, 2.0]
    cost_curve = {c: [] for c in CONDITIONS}
    brier_curve = {c: [] for c in CONDITIONS}
    for v in values:
        agg, _ = _eval_seeds(
            world, config.seeds, v, theta, high_cost, record_dir=exp_dir, knob=knob
        )
        for c in CONDITIONS:
            cost_curve[c].append(round(agg[c].mean_cost, 6))
            brier_curve[c].append(round(agg[c].brier, 6))
        notify_fn(f"  {knob}={v}: " + " ".join(f"{c}={agg[c].mean_cost:.1f}" for c in CONDITIONS))
    robustness = {"knob": knob, "values": values, "mean_cost": cost_curve, "brier": brier_curve}

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S6Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (stub visual embedding, no LLM)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        primary_sigma=primary_sigma,
        per_condition=per_condition,
        comparisons=comparisons,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def run_agent(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    """S6 を**実 LLM エージェント**が経路化（規制写像の有無で対比, agent 射程・R-3a）。"""
    from orx.common.providers import make_llm_client
    from orx.exp.stats import paired_comparisons
    from orx.exp.suites.s6_recycling.agent import AGENT_CONDITIONS, route_episode_llm
    from orx.exp.suites.s6_recycling.scorer import score

    notify_fn = notify if callable(notify) else (lambda *_: None)
    if config.provider is None:
        raise ValueError("agent 条件には provider 設定が必要です")
    world = load_config(repo_root() / config.world_config, S6World)
    high_cost = float(config.params.get("high_cost_threshold", _DEFAULT_HIGH_COST))
    sigma = float(config.params.get("primary_sigma", _DEFAULT_PRIMARY_SIGMA))
    protos = class_prototypes(world.classes, world.embedding_dim)
    conds = [c for c in config.conditions if c in AGENT_CONDITIONS]
    llm = make_llm_client(config.provider)

    per: dict[str, list] = {c: [] for c in conds}
    tokens: dict[str, int] = {c: 0 for c in conds}
    for seed in config.seeds:
        ep = generator.generate_episode(world, seed, sigma)
        for c in conds:
            assignments, rr = route_episode_llm(c, ep.objects, world, protos, llm)
            per[c].append(score(assignments, world, high_cost))
            tokens[c] += rr.prompt_tokens + rr.completion_tokens
        notify_fn(
            f"  seed={seed}: " + " ".join(f"{c}=cost{per[c][-1].mean_cost:.1f}" for c in conds)
        )

    per_condition: dict[str, S6ConditionAgg] = {}
    for c in conds:
        rows = per[c]
        n = len(rows)
        per_condition[c] = S6ConditionAgg(
            mean_cost=round(sum(r.mean_cost for r in rows) / n, 6),
            high_cost_errors=sum(r.high_cost_errors for r in rows),
            escalation_rate=round(sum(r.escalation_rate for r in rows) / n, 6),
            throughput=round(sum(r.throughput for r in rows) / n, 6),
            routed_accuracy=round(sum(r.routed_accuracy for r in rows) / n, 6),
            brier=round(sum(r.brier for r in rows) / n, 6),
        )
    primary = "OR-full-llm"
    success_by = {c: [r.high_cost_errors == 0 for r in per[c]] for c in conds}
    metric_by = {c: [r.mean_cost for r in per[c]] for c in conds}
    comparisons = paired_comparisons(
        primary,
        [c for c in conds if c != primary],
        success_by,
        metric_by,
        "mean_cost",
        config.seeds[0],
    )
    falsification = {
        "OR_full_llm_lowest_cost": all(
            per_condition[primary].mean_cost <= per_condition[b].mean_cost
            for b in conds
            if b != primary
        ),
        "no_regulation_misroutes": any(
            per_condition[b].high_cost_errors > 0 for b in conds if b != primary
        ),
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S6Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot=f"live: {config.provider.llm_model} (mode={config.provider.mode})",
        seeds=list(config.seeds),
        conditions=conds,
        primary_sigma=sigma,
        per_condition=per_condition,
        comparisons=comparisons,
        falsification=falsification,
        robustness={},
        scope="agent",
        total_tokens=tokens,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s6-demo",
        scenario="s6",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[601, 602, 603, 604],
        duration_s=0.0,
        knob="visual_noise",
        knob_values=[0.1, 0.5, 1.0, 2.0],
    )
    exp_dir = runs_root / "scenario-s6-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s6-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = [f"S6 リサイクル選別（T13）— コスト加重（σ={result['primary_sigma']}）:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<8} mean_cost={a['mean_cost']:.3f} 高コスト誤り={a['high_cost_errors']} "
            f"委譲={a['escalation_rate']:.2f} 自動処理={a['throughput']:.2f}"
        )
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def _primary_signal_section(result: dict) -> list[str]:
    """S6 agent の**主シグナル**（平均コスト差＋Wilcoxon/CI）を前面化する（R-A: 多シード）。

    高コスト誤りが稀/同値だと per-seed McNemar は不一致 seed が少なく低検出力（例 p=0.5）になる。
    そのため S6 の結論は**規制写像が平均コストを下げるか**（連続指標 = 平均コスト差の Wilcoxon
    符号順位＋成功率差 bootstrap CI）で述べる。McNemar 表は参考として併記する。
    """
    primary = "OR-full-llm"
    pc = result.get("per_condition", {})
    baselines = [c for c in result.get("conditions", []) if c != primary]
    cmp_by = {(c["condition_a"], c["condition_b"]): c for c in result.get("comparisons", [])}
    lines = [
        "## 主シグナル（平均コスト差・連続指標）",
        "",
        "> **結論はここで述べる**: 規制オントロジー（disposal_route 写像）の有無による"
        "**平均処理コスト差**。高コスト誤りは稀なため per-seed McNemar は低検出力になりやすく、"
        "主指標には連続指標（平均コストの Wilcoxon・成功率差 bootstrap CI）を用いる。",
        "",
        "| 対 | 平均コスト(A/B) | コスト差(B−A) | Wilcoxon p | 成功率差95%CI | 高コスト誤り(A/B) |",
        "|----|-----------------|---------------|------------|----------------|--------------------|",
    ]
    a = pc.get(primary, {})
    for b in baselines:
        bb = pc.get(b, {})
        cmp = cmp_by.get((primary, b), {})
        cost_a = a.get("mean_cost", 0.0)
        cost_b = bb.get("mean_cost", 0.0)
        wp = cmp.get("wilcoxon_p")
        wp_s = f"{wp:.2e}" if isinstance(wp, (int, float)) else "—"
        ci = (
            f"[{cmp['diff_ci_low']:+.3f}, {cmp['diff_ci_high']:+.3f}]"
            if "diff_ci_low" in cmp
            else "—"
        )
        lines.append(
            f"| {primary} vs {b} | {cost_a:.3f}/{cost_b:.3f} | {cost_b - cost_a:+.3f} | "
            f"{wp_s} | {ci} | {a.get('high_cost_errors', 0)}/{bb.get('high_cost_errors', 0)} |"
        )
    lines.append("")
    return lines


def render_report(result: dict) -> str:
    from orx.exp import scope
    from orx.exp.scenario import comparison_section

    if result.get("scope") == "agent":
        mode = "openai" if "mode=openai" in result.get("model_snapshot", "") else "cache"
        lines = [
            f"# ORX Scenario Report — S6 リサイクル（T13・agent/live） `{result['exp_id']}`",
            "",
            f"- agent 検証（規制オントロジーが LLM の経路化を助けるか, H3/H4-隣接）/ "
            f"model: {result.get('model_snapshot', '?')} / git: `{result.get('git_commit', '?')}`",
            "",
            scope.scope_section([scope.AGENT], scope.agent_status_for(mode)),
            "> **射程注記**: H4（記号×ベクトル双対表現）の機構検証は決定的 OR-vec/OR-sym が担う。"
            "本 agent 版は接地済みクラスからの**規制写像（disposal_route）が LLM の正レーン選定を"
            "助けるか**を測る別射程の検証。",
            "",
            "## 1. 経路化コスト（agent）",
            "",
            "| 条件 | 平均コスト | 高コスト誤り | 委譲率 | 自動処理 |",
            "|------|-----------|-------------|--------|----------|",
        ]
        for c in result["conditions"]:
            a = result["per_condition"][c]
            lines.append(
                f"| {c} | {a['mean_cost']:.3f} | {a['high_cost_errors']} | "
                f"{a['escalation_rate']:.2f} | {a['throughput']:.2f} |"
            )
        lines += ["", *_primary_signal_section(result)]
        lines += [
            "",
            *comparison_section(
                result.get("comparisons", []), "対比較（OR-full-llm vs B0-llm・参考）"
            ),
        ]
        tok = result.get("total_tokens", {})
        if tok:
            lines += ["", "## 2. トークン", "", "| 条件 | 総トークン |", "|------|-----------|"]
            for c in result["conditions"]:
                lines.append(f"| {c} | {tok.get(c, 0)} |")
        return "\n".join(lines) + "\n"

    stamp = (
        f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
    )
    lines = [
        f"# ORX Scenario Report — S6 リサイクル選別（T13） `{result['exp_id']}`",
        "",
        f"- シナリオ: s6 / 仮説: H4（双対表現） / シード数: {len(result['seeds'])} / "
        f"σ={result['primary_sigma']} / 構成ハッシュ: `{result['config_hash']}`{stamp}",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 3条件は決定的ソルバ（OR-vec/OR-sym=機構アブレーション, OR-full=表現＋機構）。"
        "LLM/実埋め込みでの H4 検証は未実行（live）。",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "OR_sym_throughput_collapse": "OR-sym は記号ID無しで接地不能→全件委譲（スループット崩壊）",
        "OR_vec_high_cost_misroute": "OR-vec は規制推論が無く誤レーン（電池の高コスト誤り）",
        "OR_full_lowest_cost": "OR-full は接地＋規制＋委譲でコスト最小・高コスト誤り0",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")
    lines += [
        "",
        f"### 条件別サマリ（σ={result['primary_sigma']}）",
        "",
        "| 条件 | コスト加重 | 高コスト誤り | 委譲率 | 自動処理率 | 委譲外正答 |",
        "|------|-----------|--------------|--------|------------|------------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['mean_cost']:.3f} | {a['high_cost_errors']} | "
            f"{a['escalation_rate']:.2f} | {a['throughput']:.2f} | {a['routed_accuracy']:.3f} |"
        )
    from orx.exp.scenario import comparison_section

    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full vs OR-vec/OR-sym・対応のある検定）",
        ),
    ]

    rob = result.get("robustness") or {}
    lines += ["", "## 2. 頑健性曲線（視覚ノイズσ × コスト加重）", ""]
    if rob:
        header = f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |"
        lines += [header, "|------|" + "------|" * len(result["conditions"])]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['mean_cost'][c][i]:.1f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
        lines += [
            "",
            "### 較正（接地確信度 Brier・低いほど良い）",
            "",
            f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |",
            "|------|" + "------|" * len(result["conditions"]),
        ]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['brier'][c][i]:.3f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
    lines += [
        "",
        "## 3. トークン効率",
        "",
        "決定的ソルバ（情報層）のため **0 トークン**。実埋め込み/LLM での H4 検証は live で別途。",
        "",
    ]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
