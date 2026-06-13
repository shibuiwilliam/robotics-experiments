"""S6 リサイクル選別（T13, H4）ランナー: 生成→双対表現3条件→コスト加重採点→反証＋σ掃引。

決定的（視覚埋め込み＋規制推論）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.suites.s6_recycling import generator
from orx.exp.suites.s6_recycling.grounding import class_prototypes
from orx.exp.suites.s6_recycling.model import S6World
from orx.exp.suites.s6_recycling.reference import CONDITIONS, assign_lane
from orx.exp.suites.s6_recycling.scorer import S6ConditionScore, score

WORLD = "configs/world/s6_recycling.yaml"
_DEFAULT_THETA = 0.15
_DEFAULT_PRIMARY_SIGMA = 0.2
_DEFAULT_HIGH_COST = 50.0

META = ScenarioMeta(
    id="s6",
    suite="T13",
    slug="recycling",
    title="リサイクル選別：法規制と知覚の橋渡し",
    tier="A",
    touchstones=["双対表現（記号×ベクトル）"],
    hypotheses=["H4"],
    conditions=CONDITIONS,
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
    falsification: dict[str, bool]
    robustness: dict


def _eval_seeds(
    world: S6World, seeds: list[int], sigma: float, theta: float, high_cost: float
) -> dict[str, S6ConditionAgg]:
    protos = class_prototypes(world.classes, world.embedding_dim)
    per: dict[str, list[S6ConditionScore]] = {c: [] for c in CONDITIONS}
    for seed in seeds:
        ep = generator.generate_episode(world, seed, sigma)
        for c in CONDITIONS:
            assignments = [
                (obj, *assign_lane(c, obj, world, protos, theta)) for obj in ep.objects
            ]
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
    return agg


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S6World)
    theta = float(config.params.get("confidence_threshold", _DEFAULT_THETA))
    high_cost = float(config.params.get("high_cost_threshold", _DEFAULT_HIGH_COST))
    primary_sigma = float(config.params.get("primary_sigma", _DEFAULT_PRIMARY_SIGMA))

    per_condition = _eval_seeds(world, config.seeds, primary_sigma, theta, high_cost)
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

    knob = config.knob or "visual_noise"
    values = config.knob_values or [0.1, 0.5, 1.0, 2.0]
    cost_curve = {c: [] for c in CONDITIONS}
    brier_curve = {c: [] for c in CONDITIONS}
    for v in values:
        agg = _eval_seeds(world, config.seeds, v, theta, high_cost)
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
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s6-demo", scenario="s6", world_config=WORLD, conditions=CONDITIONS,
        seeds=[601, 602, 603, 604], duration_s=0.0,
        knob="visual_noise", knob_values=[0.1, 0.5, 1.0, 2.0],
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


def render_report(result: dict) -> str:
    from orx.exp import scope

    stamp = f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
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
    lines += ["", f"### 条件別サマリ（σ={result['primary_sigma']}）", "",
              "| 条件 | コスト加重 | 高コスト誤り | 委譲率 | 自動処理率 | 委譲外正答 |",
              "|------|-----------|--------------|--------|------------|------------|"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['mean_cost']:.3f} | {a['high_cost_errors']} | "
            f"{a['escalation_rate']:.2f} | {a['throughput']:.2f} | {a['routed_accuracy']:.3f} |"
        )
    rob = result.get("robustness") or {}
    lines += ["", "## 2. 頑健性曲線（視覚ノイズσ × コスト加重）", ""]
    if rob:
        header = f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |"
        lines += [header, "|------|" + "------|" * len(result["conditions"])]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['mean_cost'][c][i]:.1f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
        lines += ["", "### 較正（接地確信度 Brier・低いほど良い）", "",
                  f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |",
                  "|------|" + "------|" * len(result["conditions"])]
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
