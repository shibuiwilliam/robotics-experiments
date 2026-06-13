"""S2 アレルゲン交差汚染（T9）ランナー: 生成→X2蒸留→4条件ソルバ→採点→反証＋掃引。

決定的（接触スケジュール＋反証ソルバ）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.suites.s2_allergen import generator
from orx.exp.suites.s2_allergen.model import S2World
from orx.exp.suites.s2_allergen.reference import (
    CONDITIONS,
    contamination_pairs,
    grasp_allowed_under,
)
from orx.exp.suites.s2_allergen.scorer import _f1
from orx.oracle.scenarios.s2 import contamination_closure
from orx.perception.contacts import distill_contacts

WORLD = "configs/world/s2_allergen.yaml"

META = ScenarioMeta(
    id="s2",
    suite="T9",
    slug="allergen",
    title="食品工場のアレルゲン交差汚染管理",
    tier="A",
    touchstones=["②関係伝播", "時間付き状態推論"],
    hypotheses=["H5"],
    conditions=CONDITIONS,
    status="implemented",
)


class S2ConditionAgg(StrictModel):
    accuracy: float
    safety_violations: int  # 偽陰性（汚染把持を許可）
    over_conservative: int  # 偽陽性（許可把持を拒否）
    contamination_f1: float


class S2Result(StrictModel):
    scenario: str = "s2"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    per_condition: dict[str, S2ConditionAgg]
    falsification: dict[str, bool]
    robustness: dict


def _truth_pairs(ep) -> set[tuple[str, str]]:
    st = contamination_closure(ep.intrinsic, ep.contacts, ep.cleanings, ep.eval_time)
    return {(e, a) for e, alls in st.carried.items() for a in alls}


def _eval_seeds(
    world: S2World, seeds: list[int], miss_rate: float
) -> dict[str, S2ConditionAgg]:
    observers = world.robots
    answers: dict[str, list[tuple[bool, bool]]] = {c: [] for c in CONDITIONS}
    f1s: dict[str, list[float]] = {c: [] for c in CONDITIONS}
    for seed in seeds:
        ep = generator.generate_episode(world, seed)
        rng = SeedTree(seed).child("s2-distill").rng()
        distilled = distill_contacts(ep.contacts, miss_rate, rng)
        truth_pairs = _truth_pairs(ep)
        for c in CONDITIONS:
            answers[c].extend(
                (grasp_allowed_under(c, ep, distilled, observers, q), q.truth_allowed)
                for q in ep.queries
            )
            f1s[c].append(
                _f1(contamination_pairs(c, ep, distilled, observers, ep.eval_time), truth_pairs)
            )
    agg: dict[str, S2ConditionAgg] = {}
    for c in CONDITIONS:
        a = answers[c]
        n = len(a)
        agg[c] = S2ConditionAgg(
            accuracy=round(sum(1 for p, t in a if p == t) / n, 6),
            safety_violations=sum(1 for p, t in a if p and not t),
            over_conservative=sum(1 for p, t in a if (not p) and t),
            contamination_f1=round(sum(f1s[c]) / len(f1s[c]), 6),
        )
    return agg


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S2World)

    per_condition = _eval_seeds(world, config.seeds, miss_rate=0.0)
    for c in CONDITIONS:
        a = per_condition[c]
        notify_fn(
            f"  {c:<9} acc={a.accuracy:.3f} F1={a.contamination_f1:.3f} "
            f"violations={a.safety_violations} over={a.over_conservative}"
        )

    falsification = {
        "B0_no_history_false_negative": per_condition["B0"].safety_violations > 0,
        "B1_cross_robot_miss": per_condition["B1"].safety_violations > 0,
        "OR_belief_overconservative": per_condition["OR-belief"].over_conservative > 0,
        "OR_full_correct": (
            per_condition["OR-full"].safety_violations == 0
            and per_condition["OR-full"].over_conservative == 0
            and per_condition["OR-full"].contamination_f1 >= 1.0 - 1e-9
        ),
    }

    robustness: dict = {}
    knob = config.knob or "contact_miss_rate"
    values = config.knob_values or [0.0, 0.2, 0.4, 0.6]
    acc_curve = {c: [] for c in CONDITIONS}
    for v in values:
        agg = _eval_seeds(world, config.seeds, miss_rate=v)
        for c in CONDITIONS:
            acc_curve[c].append(round(agg[c].accuracy, 6))
        notify_fn(f"  {knob}={v}: " + " ".join(f"{c}={agg[c].accuracy:.2f}" for c in CONDITIONS))
    robustness = {"knob": knob, "values": values, "accuracy": acc_curve}

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S2Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (no LLM/embedding)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        per_condition=per_condition,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s2-demo", scenario="s2", world_config=WORLD, conditions=CONDITIONS,
        seeds=[201, 202, 203, 204], duration_s=0.0,
        knob="contact_miss_rate", knob_values=[0.0, 0.3, 0.6],
    )
    exp_dir = runs_root / "scenario-s2-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s2-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = ["S2 アレルゲン交差汚染（T9）— 把持可否:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<9} acc={a['accuracy']:.3f} 汚染F1={a['contamination_f1']:.3f} "
            f"違反={a['safety_violations']} 過保守={a['over_conservative']}"
        )
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def render_report(result: dict) -> str:
    from orx.exp import scope

    stamp = f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
    lines = [
        f"# ORX Scenario Report — S2 アレルゲン交差汚染（T9） `{result['exp_id']}`",
        "",
        f"- シナリオ: s2 / 仮説: H5（関係伝播・時間付き状態） / シード数: {len(result['seeds'])} / "
        f"構成ハッシュ: `{result['config_hash']}`{stamp}",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 4条件はすべて決定的リファレンスソルバ（ceiling/ablation）。"
        "LLMエージェント版は未実装・未実行（live）。",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "B0_no_history_false_negative": "B0 は履歴なしで推移閉包を解けず汚染把持を許可（安全違反）",
        "B1_cross_robot_miss": "B1 は横断融合が無く越境連鎖の汚染を取りこぼす（安全違反）",
        "OR_belief_overconservative": "OR−belief は洗浄リセット無視で許可把持を拒否（過保守）",
        "OR_full_correct": "OR-full は推移閉包＋洗浄で違反0・過保守0・汚染F1=1.0",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")
    lines += ["", "### 条件別サマリ（ノイズ0）", "",
              "| 条件 | 把持正答率 | 汚染集合F1 | 安全違反(偽陰) | 過保守(偽陽) |",
              "|------|-----------|-----------|----------------|--------------|"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['accuracy']:.3f} | {a['contamination_f1']:.3f} | "
            f"{a['safety_violations']} | {a['over_conservative']} |"
        )
    rob = result.get("robustness") or {}
    lines += ["", "## 2. 頑健性曲線（接触見落とし率 × 把持正答率）", ""]
    if rob:
        header = f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |"
        lines += [header, "|------|" + "------|" * len(result["conditions"])]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['accuracy'][c][i]:.3f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
        lines.append("")
        lines.append("> OR-full は洗浄リセットを正しく扱うため OR−belief を上回る（H5）。")
    lines += [
        "",
        "## 3. トークン効率",
        "",
        "決定的リファレンスソルバ（情報層）のため **0 トークン**（LLM不使用・stub常時CI）。"
        "エージェント版の正答率/トークンは live で別途。",
        "",
    ]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
