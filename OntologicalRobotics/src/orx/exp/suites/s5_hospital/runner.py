"""S5 病院内搬送（T12, 規範層・H5）ランナー: 規範適合経路＋custody監査の4条件。

決定的（deontic 経路計画＋来歴記録）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
新評価軸 = **監査可能性**（custody連鎖の完全回答率）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.stats import PairedComparison, paired_comparisons
from orx.exp.suites.s5_hospital.agent import AGENT_CONDITIONS
from orx.exp.suites.s5_hospital.model import S5World
from orx.exp.suites.s5_hospital.reference import CONDITIONS
from orx.exp.suites.s5_hospital.scorer import S5ConditionScore, score_condition

WORLD = "configs/world/s5_hospital.yaml"

META = ScenarioMeta(
    id="s5",
    suite="T12",
    slug="hospital",
    title="病院内搬送：規範が支配する物理空間",
    tier="C",
    touchstones=["規範と監査", "custody連鎖"],
    hypotheses=["H5", "H6"],
    conditions=CONDITIONS + AGENT_CONDITIONS,
    status="implemented",
)


class S5ConditionAgg(StrictModel):
    violations: int
    audit_completeness: float  # agent 射程では「経路妥当率（src→dst 到達）」を格納する
    normative_cost: float
    delivered: int


class S5Result(StrictModel):
    scenario: str = "s5"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    per_condition: dict[str, S5ConditionAgg]
    comparisons: list[PairedComparison] = []
    falsification: dict[str, bool]
    robustness: dict = {}
    scope: str = "deterministic"  # "deterministic" | "agent" (R-B)
    total_tokens: dict[str, int] = {}


def _eval(world: S5World) -> dict[str, S5ConditionAgg]:
    out: dict[str, S5ConditionAgg] = {}
    for c in CONDITIONS:
        s: S5ConditionScore = score_condition(world, c)
        out[c] = S5ConditionAgg(
            violations=s.violations,
            audit_completeness=s.audit_completeness,
            normative_cost=s.normative_cost,
            delivered=s.delivered,
        )
    return out


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    if any(c.endswith("-llm") for c in config.conditions):
        return run_agent(config, exp_dir, notify)
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S5World)
    per_condition = _eval(world)
    for c in CONDITIONS:
        a = per_condition[c]
        notify_fn(
            f"  {c:<16} 違反={a.violations} 監査完全={a.audit_completeness:.3f} "
            f"規範コスト={a.normative_cost:.2f} 完遂={a.delivered}"
        )

    # 頑健性掃引（custody 欠落率, R-3c）: gap=0 は決定的（上の per_condition と一致）。
    # gap>0 は seed 毎の確率欠落を平均し監査完全性の劣化曲線を描く（seeds が解像度に効く=R-3b）。
    knob = config.knob or "custody_gap_rate"
    values = config.knob_values or [0.0, 0.25, 0.5, 0.75]
    audit_curve: dict[str, list[float]] = {c: [] for c in CONDITIONS}
    for v in values:
        for c in CONDITIONS:
            vals = []
            for seed in config.seeds:
                rng = SeedTree(seed).child("s5-custody").child(c).rng()
                s = score_condition(world, c, custody_gap_rate=v, rng=rng)
                vals.append(s.audit_completeness)
            audit_curve[c].append(round(sum(vals) / len(vals), 6))
        notify_fn(f"  {knob}={v}: " + " ".join(f"{c}={audit_curve[c][-1]:.2f}" for c in CONDITIONS))
    robustness = {"knob": knob, "values": values, "audit_completeness": audit_curve}

    full = per_condition["OR-full"]
    falsification = {
        # 規範層無しは最短経路で禁止区画を通過する（違反）
        "no_normative_violates": per_condition["OR-no-normative"].violations > 0,
        # 来歴無しは custody 連鎖を再構成できず監査クエリに完全回答できない
        "no_prov_audit_incomplete": per_condition["OR-no-prov"].audit_completeness < 1.0,
        # B1 は規範も来歴も無く 違反かつ監査不能（最悪）
        "b1_violates_and_unauditable": (
            per_condition["B1"].violations > 0 and per_condition["B1"].audit_completeness < 1.0
        ),
        # OR-full は違反0かつ監査完全（規範コストを定量化して許容）
        "or_full_compliant_auditable": (
            full.violations == 0 and full.audit_completeness >= 1.0 and full.normative_cost > 0.0
        ),
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S5Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (deontic planner, no LLM)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        per_condition=per_condition,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def run_agent(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    """S5 を**実 LLM エージェント**が経路計画（規範写像の有無で対比, agent 射程・R-B）。

    対比較の単位は**搬送（transport）**: 各搬送の規範違反0を成否、違反数を連続指標とする
    （搬送が独立した規範ルーティング決定。世界は決定的なため seed は反復で違反単位を増やす）。
    """
    from orx.common.providers import make_llm_client
    from orx.exp.suites.s5_hospital.agent import (
        adjacency,
        is_valid_path,
        plan_routes_llm,
    )
    from orx.exp.suites.s5_hospital.reference import shortest_route
    from orx.oracle.scenarios.s5 import count_violations

    notify_fn = notify if callable(notify) else (lambda *_: None)
    if config.provider is None:
        raise ValueError("agent 条件には provider 設定が必要です")
    world = load_config(repo_root() / config.world_config, S5World)
    conds = [c for c in config.conditions if c in AGENT_CONDITIONS]
    llm = make_llm_client(config.provider)
    adj = adjacency(world.edges)
    by_id = {t.transport_id: t for t in world.transports}

    # 搬送単位の成否/違反数ベクトル（条件→[unit値]）
    unit_success: dict[str, list[bool]] = {c: [] for c in conds}
    unit_metric: dict[str, list[float]] = {c: [] for c in conds}
    totals: dict[str, dict[str, float]] = {
        c: {"violations": 0, "delivered": 0, "valid": 0, "extra_hops": 0.0, "n": 0} for c in conds
    }
    tokens: dict[str, int] = {c: 0 for c in conds}
    for seed in config.seeds:  # noqa: B007 — 反復で違反単位を増やす（決定的）
        for c in conds:
            routes, rr = plan_routes_llm(c, world, llm)
            tokens[c] += rr.prompt_tokens + rr.completion_tokens
            for tid, t in by_id.items():
                route = routes.get(tid, [t.src, t.dst])
                v = count_violations(route, t.item_class, world.norms, world.zone_class)
                valid = is_valid_path(route, t.src, t.dst, adj)
                short = shortest_route(t, world)
                extra = max(0, (len(route) - 1) - (len(short) - 1)) if valid else 0
                unit_success[c].append(v == 0)
                unit_metric[c].append(float(v))
                tot = totals[c]
                tot["violations"] += v
                tot["delivered"] += 1 if valid else 0
                tot["valid"] += 1 if valid else 0
                tot["extra_hops"] += extra
                tot["n"] += 1
        notify_fn(
            f"  seed={seed}: " + " ".join(f"{c}=違反{totals[c]['violations']}" for c in conds)
        )

    per_condition: dict[str, S5ConditionAgg] = {}
    for c in conds:
        tot = totals[c]
        n = max(1, int(tot["n"]))
        per_condition[c] = S5ConditionAgg(
            violations=int(tot["violations"]),
            audit_completeness=round(tot["valid"] / n, 6),  # 経路妥当率（src→dst 到達）
            normative_cost=round(tot["extra_hops"] / n, 6),
            delivered=int(tot["delivered"]),
        )
    primary = "OR-full-llm"
    comparisons = paired_comparisons(
        primary,
        [c for c in conds if c != primary],
        unit_success,
        unit_metric,
        "violations",
        config.seeds[0],
    )
    falsification = {
        # OR-full-llm は規範写像で違反0
        "OR_full_llm_compliant": per_condition[primary].violations == 0,
        # 規範写像を欠く baseline は禁止区画を通過（違反>0）
        "no_norm_map_violates": any(per_condition[b].violations > 0 for b in conds if b != primary),
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S5Result(
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
        name="s5-demo",
        scenario="s5",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[501],
        duration_s=0.0,
    )
    exp_dir = runs_root / "scenario-s5-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s5-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = ["S5 病院内搬送（T12）— 規範適合＋custody監査:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<16} 違反={a['violations']} 監査完全={a['audit_completeness']:.3f} "
            f"規範コスト={a['normative_cost']:.2f} 完遂={a['delivered']}"
        )
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def _render_agent_report(result: dict) -> str:
    from orx.exp import scope
    from orx.exp.scenario import comparison_section

    mode = (
        "openai"
        if "mode=openai" in result.get("model_snapshot", "")
        else ("cache" if "mode=cache" in result.get("model_snapshot", "") else "stub")
    )
    lines = [
        f"# ORX Scenario Report — S5 病院内搬送（T12・agent/live） `{result['exp_id']}`",
        "",
        f"- agent 検証（規範オントロジーが LLM の適合経路計画を助けるか, H5-隣接）/ "
        f"model: {result.get('model_snapshot', '?')} / git: `{result.get('git_commit', '?')}`",
        "",
        scope.scope_section([scope.AGENT], scope.agent_status_for(mode)),
        "> **射程注記**: 規範/来歴機構の有無の差は決定的アブレーションが担う。本 agent 版は"
        "**禁止規範写像（item_class→禁止 zone class）を与えると LLM が禁止区画を通らない経路を"
        "計画できるか**を測る別射程の検証（S6 の規制写像と同型）。対比較の単位は搬送。",
        "",
        "## 1. 規範ルーティング（agent）",
        "",
        "| 条件 | 規範違反(総数) | 経路妥当率(src→dst) | 規範コスト(追加ホップ) | 完遂(妥当経路) |",
        "|------|----------------|---------------------|------------------------|----------------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['violations']} | {a['audit_completeness']:.3f} | "
            f"{a['normative_cost']:.2f} | {a['delivered']} |"
        )
    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full-llm vs B1-llm・搬送単位・成否=違反0/指標=違反数）",
        ),
    ]
    tok = result.get("total_tokens", {})
    if tok:
        lines += ["", "## 2. トークン", "", "| 条件 | 総トークン |", "|------|-----------|"]
        for c in result["conditions"]:
            lines.append(f"| {c} | {tok.get(c, 0)} |")
    return "\n".join(lines) + "\n"


def render_report(result: dict) -> str:
    if result.get("scope") == "agent":
        return _render_agent_report(result)
    from orx.exp import scope

    stamp = (
        f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
    )
    lines = [
        f"# ORX Scenario Report — S5 病院内搬送（T12） `{result['exp_id']}`",
        "",
        f"- シナリオ: s5 / 仮説: 規範層, H5, H6 / 構成ハッシュ: `{result['config_hash']}`{stamp}",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 4条件は決定的ソルバ（no-normative/no-prov/B1=機構アブレーション, OR-full=規範＋来歴）。"
        "規範層は新設 `ontology/domains/normative.ttl`。agent 検証は未実行（live）。",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "no_normative_violates": "規範層なしは最短経路で禁止区画を通過（違反）",
        "no_prov_audit_incomplete": "来歴なしは custody を再構成できず監査不完全",
        "b1_violates_and_unauditable": "B1 は規範も来歴も無く違反かつ監査不能",
        "or_full_compliant_auditable": "OR-full は違反0かつ監査完全（規範コストは定量化し許容）",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")

    lines += [
        "",
        "## 2. 規範遵守・監査可能性・効率（新評価軸=監査可能性）",
        "",
        "| 条件 | 規範違反 | 監査完全回答率 | 規範コスト(追加ホップ) | 搬送完遂 |",
        "|------|----------|----------------|------------------------|----------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['violations']} | {a['audit_completeness']:.3f} | "
            f"{a['normative_cost']:.2f} | {a['delivered']} |"
        )
    lines += [
        "",
        "規範遵守は経路を延ばす（効率劣化）が、違反0と監査完全という規範要件を満たす。"
        "効率と規範のトレードオフを定量化できる点が OR の価値。",
        "",
    ]

    rob = result.get("robustness") or {}
    if rob:
        lines += [
            "## 3. 頑健性曲線（custody 欠落率 × 監査完全性, R-3c）",
            "",
            f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |",
            "|------|" + "------|" * len(result["conditions"]),
        ]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(
                f"{rob['audit_completeness'][c][i]:.2f}" for c in result["conditions"]
            )
            lines.append(f"| {v} | {cells} |")
        lines += [
            "",
            "custody 記録が確率的に失われても OR-full は来歴連鎖で監査完全性を相対的に保つ"
            "（来歴無しは早期に底打ち）。seed 平均で掃引点を安定化（R-3b）。",
            "",
        ]

    lines += [
        "## 4. トークン効率",
        "",
        "決定的ソルバ（情報層）のため **0 トークン**。規範層の agent 検証は live。",
        "",
    ]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
