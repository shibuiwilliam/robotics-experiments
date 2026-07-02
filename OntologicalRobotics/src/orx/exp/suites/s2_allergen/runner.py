"""S2 アレルゲン交差汚染（T9）ランナー: 生成→X2蒸留→4条件ソルバ→採点→反証＋掃引。

決定的（接触スケジュール＋反証ソルバ）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.record import persist_episode
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.stats import PairedComparison, paired_comparisons
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

from orx.exp.suites.s2_allergen.agent import AGENT_CONDITIONS  # noqa: E402

META = ScenarioMeta(
    id="s2",
    suite="T9",
    slug="allergen",
    title="食品工場のアレルゲン交差汚染管理",
    tier="A",
    touchstones=["②関係伝播", "時間付き状態推論"],
    hypotheses=["H5"],
    conditions=CONDITIONS + AGENT_CONDITIONS,
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
    comparisons: list[PairedComparison] = []
    falsification: dict[str, bool]
    robustness: dict
    scope: str = "deterministic"  # "deterministic" | "agent" (R-3a)
    total_tokens: dict[str, int] = {}


def _truth_pairs(ep) -> set[tuple[str, str]]:
    st = contamination_closure(ep.intrinsic, ep.contacts, ep.cleanings, ep.eval_time)
    return {(e, a) for e, alls in st.carried.items() for a in alls}


def _eval_seeds(
    world: S2World,
    seeds: list[int],
    miss_rate: float,
    record_dir: Path | None = None,
) -> tuple[dict[str, S2ConditionAgg], dict[str, list[bool]], dict[str, list[float]]]:
    observers = world.robots
    answers: dict[str, list[tuple[bool, bool]]] = {c: [] for c in CONDITIONS}
    f1s: dict[str, list[float]] = {c: [] for c in CONDITIONS}
    seed_success: dict[str, list[bool]] = {c: [] for c in CONDITIONS}  # seed毎: 安全かつ全問正
    seed_acc: dict[str, list[float]] = {c: [] for c in CONDITIONS}  # seed毎: 正答率
    for seed in seeds:
        ep = generator.generate_episode(world, seed)
        # S2 のエピソードは miss_rate 非依存（蒸留は生成後）— 主評価時のみ永続化（D-3）
        persist_episode(record_dir, ep, seed)
        rng = SeedTree(seed).child("s2-distill").rng()
        distilled = distill_contacts(ep.contacts, miss_rate, rng)
        truth_pairs = _truth_pairs(ep)
        for c in CONDITIONS:
            qa = [
                (grasp_allowed_under(c, ep, distilled, observers, q), q.truth_allowed)
                for q in ep.queries
            ]
            answers[c].extend(qa)
            f1s[c].append(
                _f1(contamination_pairs(c, ep, distilled, observers, ep.eval_time), truth_pairs)
            )
            nq = len(qa)
            correct = sum(1 for p, t in qa if p == t)
            violations = sum(1 for p, t in qa if p and not t)
            seed_acc[c].append(correct / nq if nq else 1.0)
            seed_success[c].append(violations == 0 and correct == nq)
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
    return agg, seed_success, seed_acc


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    if any(c.endswith("-llm") for c in config.conditions):
        return run_agent(config, exp_dir, notify)
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S2World)

    per_condition, seed_success, seed_acc = _eval_seeds(
        world, config.seeds, miss_rate=0.0, record_dir=exp_dir
    )
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

    # 対比較（OR-full vs OR-belief/B1/B0, R-3d）: 成否＝当該seedで安全かつ全問正、指標=正答率。
    comparisons = paired_comparisons(
        "OR-full",
        [c for c in CONDITIONS if c != "OR-full"],
        seed_success,
        seed_acc,
        "accuracy",
        config.seeds[0],
    )

    robustness: dict = {}
    knob = config.knob or "contact_miss_rate"
    values = config.knob_values or [0.0, 0.2, 0.4, 0.6]
    acc_curve = {c: [] for c in CONDITIONS}
    for v in values:
        agg, _, _ = _eval_seeds(world, config.seeds, miss_rate=v)
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
        comparisons=comparisons,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def run_agent(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    """S2 を**実 LLM エージェント**が把持可否で解く（agent 射程・R-3a）。"""
    from orx.common.providers import make_llm_client
    from orx.common.seeding import SeedTree
    from orx.exp.stats import paired_comparisons
    from orx.exp.suites.s2_allergen.agent import AGENT_CONDITIONS, solve_query_llm
    from orx.exp.suites.s2_allergen.graph import build_contamination_graph

    notify_fn = notify if callable(notify) else (lambda *_: None)
    if config.provider is None:
        raise ValueError("agent 条件には provider 設定が必要です")
    world = load_config(repo_root() / config.world_config, S2World)
    observers = world.robots
    conds = [c for c in config.conditions if c in AGENT_CONDITIONS]
    llm = make_llm_client(config.provider)

    seed_success: dict[str, list[bool]] = {c: [] for c in conds}
    seed_acc: dict[str, list[float]] = {c: [] for c in conds}
    answers: dict[str, list[tuple[bool, bool]]] = {c: [] for c in conds}
    tokens: dict[str, int] = {c: 0 for c in conds}
    for seed in config.seeds:
        ep = generator.generate_episode(world, seed)
        rng = SeedTree(seed).child("s2-distill").rng()
        distilled = distill_contacts(ep.contacts, 0.0, rng)
        for c in conds:
            qa: list[tuple[bool, bool]] = []
            for q in ep.queries:
                seen = [ct for ct in distilled if ct.sim_time <= q.sim_time + 1e-9]
                graph = None
                if c == "OR-full-llm":
                    ep_q = ep.model_copy(update={"eval_time": q.sim_time})
                    graph = build_contamination_graph(
                        ep_q, world.allergens, SeedTree(seed).child(f"g{q.qid}")
                    )
                pred, rr = solve_query_llm(c, graph, q, seen, observers, ep.intrinsic, llm)
                qa.append((pred, q.truth_allowed))
                tokens[c] += rr.prompt_tokens + rr.completion_tokens
            answers[c].extend(qa)
            nq = len(qa)
            correct = sum(1 for p, t in qa if p == t)
            violations = sum(1 for p, t in qa if p and not t)
            seed_acc[c].append(correct / nq if nq else 1.0)
            seed_success[c].append(violations == 0 and correct == nq)
        notify_fn(f"  seed={seed}: " + " ".join(f"{c}={seed_acc[c][-1]:.2f}" for c in conds))

    per_condition: dict[str, S2ConditionAgg] = {}
    for c in conds:
        a = answers[c]
        n = len(a)
        per_condition[c] = S2ConditionAgg(
            accuracy=round(sum(1 for p, t in a if p == t) / n, 6) if n else 0.0,
            safety_violations=sum(1 for p, t in a if p and not t),
            over_conservative=sum(1 for p, t in a if (not p) and t),
            contamination_f1=0.0,  # agent 射程では把持可否のみ計測（汚染集合F1は決定的版）
        )
    primary = "OR-full-llm"
    comparisons = paired_comparisons(
        primary,
        [c for c in conds if c != primary],
        seed_success,
        seed_acc,
        "accuracy",
        config.seeds[0],
    )
    falsification = {
        "OR_full_llm_best_accuracy": all(
            per_condition[primary].accuracy >= per_condition[b].accuracy
            for b in conds
            if b != primary
        ),
        "baselines_have_safety_violations": any(
            per_condition[b].safety_violations > 0 for b in conds if b != primary
        ),
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S2Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot=f"live: {config.provider.llm_model} (mode={config.provider.mode})",
        seeds=list(config.seeds),
        conditions=conds,
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
        name="s2-demo",
        scenario="s2",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[201, 202, 203, 204],
        duration_s=0.0,
        knob="contact_miss_rate",
        knob_values=[0.0, 0.3, 0.6],
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
    from orx.exp.scenario import comparison_section

    if result.get("scope") == "agent":
        snap = result.get("model_snapshot", "")
        mode = "openai" if "mode=openai" in snap else "cache"
        lines = [
            f"# ORX Scenario Report — S2 アレルゲン（T9・agent/live） `{result['exp_id']}`",
            "",
            f"- 仮説: H5（**エージェント検証**）/ model: {result.get('model_snapshot', '?')} / "
            f"git: `{result.get('git_commit', '?')}`",
            "",
            scope.scope_section([scope.AGENT], scope.agent_status_for(mode)),
            "## 1. 把持可否（agent）",
            "",
            "| 条件 | 正答率 | 安全違反 | 過保守 |",
            "|------|--------|----------|--------|",
        ]
        for c in result["conditions"]:
            a = result["per_condition"][c]
            lines.append(
                f"| {c} | {a['accuracy']:.3f} | {a['safety_violations']} | "
                f"{a['over_conservative']} |"
            )
        lines += [
            "",
            *comparison_section(
                result.get("comparisons", []), "対比較（OR-full-llm vs ベースライン）"
            ),
        ]
        tok = result.get("total_tokens", {})
        if tok:
            lines += [
                "",
                "## 2. トークン（H7）",
                "",
                "| 条件 | 総トークン |",
                "|------|-----------|",
            ]
            for c in result["conditions"]:
                lines.append(f"| {c} | {tok.get(c, 0)} |")
        return "\n".join(lines) + "\n"

    stamp = (
        f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
    )
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
    lines += [
        "",
        "### 条件別サマリ（ノイズ0）",
        "",
        "| 条件 | 把持正答率 | 汚染集合F1 | 安全違反(偽陰) | 過保守(偽陽) |",
        "|------|-----------|-----------|----------------|--------------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['accuracy']:.3f} | {a['contamination_f1']:.3f} | "
            f"{a['safety_violations']} | {a['over_conservative']} |"
        )
    from orx.exp.scenario import comparison_section

    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full vs OR-belief/B1/B0・対応のある検定）",
        ),
    ]

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
