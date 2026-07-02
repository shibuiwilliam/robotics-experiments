"""S8 fulfillment（T15）ランナー: 決定的 ceiling（アクション層を通した条件別ロールアウト）。

評価方法論は S1–S7 と同型（各 seed が独立入力 → 対応あり McNemar）。ただし**閉ループ**
（アクションが物理を変える）ため反実仮想リプレイ（単一記録・多重リプレイ）は使わず、条件ごとに
独立ロールアウトする（loop="closed"・IMPROVEMENT.md §3.1）。agent（live）射程は K2。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import WorldConfig, config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.scenario import (
    ScenarioExperimentConfig,
    ScenarioMeta,
    ScenarioSpec,
    register,
)
from orx.exp.stats import PairedComparison, paired_comparisons
from orx.exp.suites.s8_fulfillment import reference
from orx.exp.suites.s8_fulfillment.agent import AGENT_CONDITIONS
from orx.exp.suites.s8_fulfillment.generator import (
    build_ontology_view,
    build_truth,
    build_vendor_view,
)
from orx.exp.suites.s8_fulfillment.scorer import S8Score, score_s8

CONDITIONS = ["OR-full", "B1", "B0"]
WORLD = "configs/world/s8_fulfillment.yaml"

META = ScenarioMeta(
    id="s8",
    suite="T15",
    slug="fulfillment",
    title="アクション型 fulfillment（キネティック層）",
    tier="A",
    touchstones=["②キネティック実行", "③監査", "④安全"],
    hypotheses=["H3", "H5", "H2", "H6", "H7"],
    conditions=CONDITIONS + AGENT_CONDITIONS,
    status="implemented",
)


class ConditionAgg(StrictModel):
    completion: float
    safety_violations: float
    misdeliveries: float
    failed_executions: float
    audit_completeness: float
    over_escalation: float
    recovery_rate: float
    success_rate: float


class S8Result(StrictModel):
    scenario: str = "s8"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    per_condition: dict[str, ConditionAgg]
    comparisons: list[PairedComparison]
    falsification: dict[str, bool]
    robustness: dict = {}
    scope: str = "deterministic"
    loop: str = "closed"  # キネティック閉ループ（反実仮想リプレイ非適用・IMPROVEMENT.md §3.1）
    total_tokens: dict[str, int] = {}


# ------------------------------------------------------------ evaluation


def _eval_seed(
    world: WorldConfig, ontology, vendor, truth, seed: int, conditions: list[str]
) -> dict[str, S8Score]:
    seeds = SeedTree(seed)
    out: dict[str, S8Score] = {}
    for c in conditions:
        final, receipts, wb, _graph, recovery = reference.run_condition(
            c, world, ontology, vendor, seeds.child(c)
        )
        out[c] = score_s8(final, truth, receipts, wb, recovery_rate=recovery)
    return out


def _aggregate(
    per_seed: dict[int, dict[str, S8Score]], conditions: list[str]
) -> dict[str, ConditionAgg]:
    seeds = list(per_seed)
    n = len(seeds)
    agg: dict[str, ConditionAgg] = {}
    for c in conditions:
        rows = [per_seed[s][c] for s in seeds]
        agg[c] = ConditionAgg(
            completion=round(sum(r.completion for r in rows) / n, 6),
            safety_violations=round(sum(r.safety_violations for r in rows) / n, 6),
            misdeliveries=round(sum(r.misdeliveries for r in rows) / n, 6),
            failed_executions=round(sum(r.failed_executions for r in rows) / n, 6),
            audit_completeness=round(sum(r.audit_completeness for r in rows) / n, 6),
            over_escalation=round(sum(r.over_escalation for r in rows) / n, 6),
            recovery_rate=round(sum(r.recovery_rate for r in rows) / n, 6),
            success_rate=round(sum(1 for r in rows if r.success) / n, 6),
        )
    return agg


def _falsification(pc: dict[str, ConditionAgg]) -> dict[str, bool]:
    orf = pc["OR-full"]
    baseline_completion = max(pc[b].completion for b in ("B1", "B0"))
    return {
        # B1: 能力契約を語彙横断できず不適合機体を選び実行が失敗する（H1/H3）
        "B1_capability_mismatch": pc["B1"].failed_executions > 1e-9,
        # B0: 送信基準の検証が無く規制違反＋誤配送を起こす（H5/H2/H6）
        "B0_unsafe": pc["B0"].safety_violations > 1e-9 and pc["B0"].misdeliveries > 1e-9,
        # OR-full: 送信基準で安全（違反0）かつ遂行率はベースライン以上（H3/H5）
        "OR_full_safe": (
            orf.safety_violations <= 1e-9 and orf.completion >= baseline_completion - 1e-9
        ),
        # OR-full: 全 move を custody で監査可能・ベースラインは来歴語彙が無く不完全（H8）
        "OR_full_auditable": (
            orf.audit_completeness >= 1.0 - 1e-9
            and all(pc[b].audit_completeness < 1.0 - 1e-9 for b in ("B1", "B0"))
        ),
        # OR-full: 注入された誤動作を ontology+custody で検出・補償（回復）。baseline は不可（H8）
        "OR_full_recovers": (
            orf.recovery_rate >= 1.0 - 1e-9
            and all(pc[b].recovery_rate < 1.0 - 1e-9 for b in ("B1", "B0"))
        ),
    }


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    if any(c.endswith("-llm") for c in config.conditions):
        return run_agent(config, exp_dir, notify)
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, WorldConfig)
    ontology = build_ontology_view(world)
    vendor = build_vendor_view(world)
    truth = build_truth(world)

    per_seed: dict[int, dict[str, S8Score]] = {}
    for seed in config.seeds:
        per_seed[seed] = _eval_seed(world, ontology, vendor, truth, seed, CONDITIONS)
        s = per_seed[seed]
        notify_fn(
            f"  seed={seed}: "
            + " ".join(
                f"{c}=cmp{s[c].completion:.2f}/viol{s[c].safety_violations}/"
                f"mis{s[c].misdeliveries}/fail{s[c].failed_executions}"
                for c in CONDITIONS
            )
        )
    per_condition = _aggregate(per_seed, CONDITIONS)
    falsification = _falsification(per_condition)

    success_by = {c: [per_seed[s][c].success for s in config.seeds] for c in CONDITIONS}
    metric_by = {c: [per_seed[s][c].completion for s in config.seeds] for c in CONDITIONS}
    comparisons = paired_comparisons(
        "OR-full", ["B1", "B0"], success_by, metric_by, "completion", config.seeds[0]
    )

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S8Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (action layer, no LLM/embedding)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        per_condition=per_condition,
        comparisons=comparisons,
        falsification=falsification,
    )
    return result.model_dump(mode="json")


# ------------------------------------------------------------ agent（live, K2）


def run_agent(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    """S8 を**実 LLM エージェント**で解く（agent 射程・K2・要コスト承認）。

    決定的版と同じ採点（score_s8）。stub では tool 呼出が無く遂行 0（配線 smoke）。本体は live。
    """
    from orx.common.providers import make_llm_client
    from orx.exp.suites.s8_fulfillment.agent import run_condition_llm

    notify_fn = notify if callable(notify) else (lambda *_: None)
    if config.provider is None:
        raise ValueError("agent 条件には provider 設定が必要（mode=stub/openai/cache）")
    world = load_config(repo_root() / config.world_config, WorldConfig)
    ontology = build_ontology_view(world)
    vendor = build_vendor_view(world)
    truth = build_truth(world)
    conds = [c for c in config.conditions if c in AGENT_CONDITIONS]
    llm = make_llm_client(config.provider)

    per_seed: dict[int, dict[str, S8Score]] = {}
    tokens = {c: 0 for c in conds}
    for seed in config.seeds:
        seeds = SeedTree(seed)
        scores: dict[str, S8Score] = {}
        for c in conds:
            score, toks = run_condition_llm(c, world, ontology, vendor, llm, seeds.child(c), truth)
            scores[c] = score
            tokens[c] += toks
        per_seed[seed] = scores
        notify_fn(
            f"  seed={seed}: " + " ".join(f"{c}=cmp{scores[c].completion:.2f}" for c in conds)
        )
    per_condition = _aggregate(per_seed, conds)

    primary = "OR-full-llm"
    baselines = [c for c in conds if c != primary]
    success_by = {c: [per_seed[s][c].success for s in config.seeds] for c in conds}
    metric_by = {c: [per_seed[s][c].completion for s in config.seeds] for c in conds}
    comparisons = paired_comparisons(
        primary, baselines, success_by, metric_by, "completion", config.seeds[0]
    )
    orf = per_condition[primary]
    falsification = {
        "OR_full_llm_safe": orf.safety_violations <= 1e-9,
        "OR_full_llm_auditable": orf.audit_completeness >= 1.0 - 1e-9,
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S8Result(
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
        scope="agent",
        total_tokens=tokens,
    )
    return result.model_dump(mode="json")


# ------------------------------------------------------------ demo/report


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s8-demo",
        scenario="s8",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[101, 102, 103, 104],
        duration_s=1.0,
    )
    exp_dir = runs_root / "scenario-s8-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s8-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = ["S8 fulfillment（T15・キネティック層）— アクション遂行・安全・監査:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<9} completion={a['completion']:.3f} "
            f"safety_viol={a['safety_violations']:.2f} misdeliv={a['misdeliveries']:.2f} "
            f"failed_exec={a['failed_executions']:.2f} audit={a['audit_completeness']:.3f}"
        )
    for cmp in result.get("comparisons", []):
        lines.append(f"McNemar (OR-full vs {cmp['condition_b']}): p = {cmp['mcnemar_p']:.2e}")
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def render_report(result: dict) -> str:
    from orx.exp import scope

    is_agent = result.get("scope") == "agent"
    scopes = [scope.AGENT] if is_agent else [scope.CEILING, scope.ABLATION]
    mode = "openai" if "mode=openai" in result.get("model_snapshot", "") else "cache"
    header_scope = (
        scope.scope_section(scopes, scope.agent_status_for(mode))
        if is_agent
        else scope.scope_section(scopes)
    )
    lines = [
        f"# ORX Scenario Report — S8 fulfillment（T15・キネティック層） `{result['exp_id']}`",
        "",
        f"- シナリオ: s8 / 仮説: {', '.join(META.hypotheses)} / "
        f"シード数: {len(result['seeds'])} / model: {result.get('model_snapshot', '?')} / "
        f"git: `{result.get('git_commit', '?')}`",
        "",
        header_scope,
        scope.loop_note(result.get("loop", "open")),
        "",
        "## 条件別サマリ（遂行・安全・監査）",
        "",
        "| 条件 | 遂行率 | 安全違反 | 誤配送 | 実行失敗 | 監査完全性 |",
        "|------|--------|----------|--------|----------|-----------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['completion']:.3f} | {a['safety_violations']:.2f} | "
            f"{a['misdeliveries']:.2f} | {a['failed_executions']:.2f} | "
            f"{a['audit_completeness']:.3f} |"
        )
    from orx.exp.scenario import comparison_section

    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []), "対比較（OR-full vs ベースライン・seed-paired）"
        ),
    ]
    lines += ["", "## 失敗予言の検証", ""]
    labels = {
        "B1_capability_mismatch": "B1 は能力契約を語彙横断できず不適合機体で実行失敗",
        "B0_unsafe": "B0 は送信基準が無く規制違反＋誤配送",
        "OR_full_safe": "OR-full は送信基準で安全違反0かつ遂行率最優位",
        "OR_full_auditable": "OR-full は全 move を custody で監査可能・ベースラインは不完全",
        "OR_full_llm_safe": "OR-full-llm は安全違反0",
        "OR_full_llm_auditable": "OR-full-llm は監査完全",
    }
    for k, v in result["falsification"].items():
        lines.append(f"- {labels.get(k, k)}: {'✓ PASS' if v else '✗ FAIL'}")
    if result.get("total_tokens"):
        lines += ["", f"- 総トークン: {result['total_tokens']}"]
    lines.append("")
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
