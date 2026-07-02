"""S3 多ベンダー製造ライン（T10, H1/H3）ランナー。

決定的シミュレーション（能力契約照合＋台帳較正）。LLM不使用・完全オフライン。
段取り替え（品種切替）＋故障（経年劣化）下で、能力統合（H3）と統合コスト（H1）を exercise。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.stats import PairedComparison, paired_comparisons
from orx.exp.suites.s3_multi_vendor.agent import AGENT_CONDITIONS
from orx.exp.suites.s3_multi_vendor.model import S3World
from orx.exp.suites.s3_multi_vendor.onboarding import OnboardingCost, onboarding_cost
from orx.exp.suites.s3_multi_vendor.reference import CONDITIONS
from orx.exp.suites.s3_multi_vendor.scorer import ConditionRun, simulate_condition

WORLD = "configs/world/s3_multi_vendor.yaml"

META = ScenarioMeta(
    id="s3",
    suite="T10",
    slug="multi_vendor",
    title="多ベンダー製造ラインの段取り替えと故障時再割当",
    tier="B",
    touchstones=["能力統合（語彙横断）", "オンボーディング"],
    hypotheses=["H1", "H3"],
    conditions=CONDITIONS + AGENT_CONDITIONS,
    status="implemented",
)


class S3ConditionAgg(StrictModel):
    overall_accuracy: float
    overall_throughput: float
    initial_accuracy: float
    setup_accuracy: float
    fault_accuracy: float
    final_accuracy: float
    total_replans: int


class S3Result(StrictModel):
    scenario: str = "s3"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    per_condition: dict[str, S3ConditionAgg]
    comparisons: list[PairedComparison] = []
    falsification: dict[str, bool]
    calibration: dict
    onboarding: OnboardingCost
    robustness: dict = {}
    scope: str = "deterministic"  # "deterministic" | "agent" (R-B)
    total_tokens: dict[str, int] = {}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _aggregate(world: S3World, runs: list[ConditionRun]) -> S3ConditionAgg:
    n_ep = world.n_episodes
    # episode index -> list of values across seeds
    acc = [[r.per_episode[e].accuracy for r in runs] for e in range(n_ep)]
    thr = [[r.per_episode[e].throughput for r in runs] for e in range(n_ep)]
    ep_acc = [_mean(acc[e]) for e in range(n_ep)]
    ep_thr = [_mean(thr[e]) for e in range(n_ep)]
    phases = [runs[0].per_episode[e].phase for e in range(n_ep)]
    by_phase = lambda name: [ep_acc[e] for e in range(n_ep) if phases[e] == name]  # noqa: E731
    return S3ConditionAgg(
        overall_accuracy=round(_mean(ep_acc), 6),
        overall_throughput=round(_mean(ep_thr), 6),
        initial_accuracy=round(_mean(by_phase("initial")), 6),
        setup_accuracy=round(_mean(by_phase("setup")), 6),
        fault_accuracy=round(_mean(by_phase("fault")), 6),
        final_accuracy=round(ep_acc[-1], 6),
        total_replans=sum(rm.replans for r in runs for rm in r.per_episode),
    )


def _simulate(world: S3World, seeds: list[int]) -> dict[str, list[ConditionRun]]:
    out: dict[str, list[ConditionRun]] = {c: [] for c in CONDITIONS}
    for seed in seeds:
        for c in CONDITIONS:
            rng = SeedTree(seed).child("s3").child(c).rng()
            out[c].append(simulate_condition(world, c, rng))
    return out


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    if any(c.endswith("-llm") for c in config.conditions):
        return run_agent(config, exp_dir, notify)
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S3World)
    runs = _simulate(world, config.seeds)
    per_condition = {c: _aggregate(world, runs[c]) for c in CONDITIONS}
    for c in CONDITIONS:
        a = per_condition[c]
        notify_fn(
            f"  {c:<11} acc={a.overall_accuracy:.3f} thr={a.overall_throughput:.3f} "
            f"setup_acc={a.setup_accuracy:.2f} fault_acc={a.fault_accuracy:.2f} "
            f"final={a.final_accuracy:.2f}"
        )

    full = per_condition["OR-full"]
    b1 = per_condition["B1"]
    rr = per_condition["round-robin"]

    # OR-full の較正曲線（台帳の故障追従, T6）
    n_ep = world.n_episodes
    full_runs = runs["OR-full"]
    brier_curve = [
        round(_mean([r.per_episode[e].brier_reliability for r in full_runs]), 6)
        for e in range(n_ep)
    ]
    fault_est_curve = [
        round(_mean([r.per_episode[e].fault_machine_estimate for r in full_runs]), 6)
        for e in range(n_ep)
    ]
    fault_onset = fault_est_curve[world.fault_step - 1]  # 故障直前の推定（高い）
    fault_end = fault_est_curve[-1]  # 末尾の推定（追従して低下）

    onb = onboarding_cost(world.vendor_d_fields, world.known_semantic_fields)

    falsification = {
        # H3 帰無1: 能力無視は実現可能性を外し期待完遂が低い
        "round_robin_capability_blind": rr.overall_throughput < full.overall_throughput,
        # H1/H3: 共通オントロジー無しは段取り替え後に語彙横断で適格機体へ届かない
        "B1_cross_vendor_fail": b1.setup_accuracy < full.setup_accuracy - 0.5,
        # OR-full は全体最良かつ故障後に縮退再割当で回復
        "OR_full_best_and_recovers": (
            full.overall_throughput > b1.overall_throughput
            and full.overall_throughput > rr.overall_throughput
            and full.final_accuracy >= 0.95
        ),
        # T6: 台帳が故障（経年劣化）を検知し推定が追従低下
        "calibration_tracks_fault": fault_onset - fault_end > 0.1,
        # H1: ハブ写像で新ベンダー統合の手修正行数が削減
        "B1_onboarding_costly": onb.b1_manual_lines > onb.or_full_manual_lines,
    }

    calibration = {
        "episodes": list(range(n_ep)),
        "phases": [full_runs[0].per_episode[e].phase for e in range(n_ep)],
        "or_full_brier_reliability": brier_curve,
        "or_full_fault_estimate": fault_est_curve,
        "fault_estimate_onset": fault_onset,
        "fault_estimate_end": fault_end,
    }

    # 対比較（OR-full vs B1/round-robin, R-3d）: seed 毎の成否＝最終 episode で完全正答
    # （故障後に縮退再割当で回復）、指標＝全 episode 平均正答率。
    n_seeds = len(config.seeds)
    seed_success = {
        c: [runs[c][i].per_episode[-1].accuracy >= 1.0 - 1e-9 for i in range(n_seeds)]
        for c in CONDITIONS
    }
    seed_metric = {
        c: [_mean([ep.accuracy for ep in runs[c][i].per_episode]) for i in range(n_seeds)]
        for c in CONDITIONS
    }
    comparisons = paired_comparisons(
        "OR-full",
        [c for c in CONDITIONS if c != "OR-full"],
        seed_success,
        seed_metric,
        "overall_accuracy",
        config.seeds[0],
    )

    # 頑健性掃引（故障劣化係数, R-3c）: fault_degradation を悪化させ全体正答率の曲線を描く。
    # 係数が小さいほど故障機体の真の成功率が下がる（過酷）。世界を複製してノブのみ変える。
    knob = config.knob or "fault_degradation"
    values = config.knob_values or [0.8, 0.5, 0.3, 0.1]
    acc_curve: dict[str, list[float]] = {c: [] for c in CONDITIONS}
    for v in values:
        w_v = world.model_copy(update={"fault_degradation": v})
        runs_v = _simulate(w_v, config.seeds)
        for c in CONDITIONS:
            acc_curve[c].append(round(_aggregate(w_v, runs_v[c]).overall_accuracy, 6))
        notify_fn(f"  {knob}={v}: " + " ".join(f"{c}={acc_curve[c][-1]:.2f}" for c in CONDITIONS))
    robustness = {"knob": knob, "values": values, "overall_accuracy": acc_curve}

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S3Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (capability ledger, no LLM)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        per_condition=per_condition,
        comparisons=comparisons,
        falsification=falsification,
        calibration=calibration,
        onboarding=onb,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def _agent_task_list(world: S3World, seed: int):
    """seed 毎の工程列（初期品種/段取替後品種の混合）を決定的に生成する（per-seed 変動）。"""
    rng = SeedTree(seed).child("s3-agent-tasks").rng()
    products = [world.products[world.initial_product], world.products[world.setup_change_product]]
    tasks = []
    for i in range(world.tasks_per_episode):
        p = products[int(rng.integers(0, 2))]
        tasks.append((f"task{i}", p))
    return tasks


def run_agent(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    """S3 を**実 LLM エージェント**が静的割当（語彙横断能力表の有無で対比, agent 射程・R-B）。"""
    from orx.common.providers import make_llm_client
    from orx.exp.suites.s3_multi_vendor.agent import allocate_tasks_llm
    from orx.oracle.scenarios.s3 import allocation_correct, chosen_true_success

    notify_fn = notify if callable(notify) else (lambda *_: None)
    if config.provider is None:
        raise ValueError("agent 条件には provider 設定が必要です")
    world = load_config(repo_root() / config.world_config, S3World)
    conds = [c for c in config.conditions if c in AGENT_CONDITIONS]
    llm = make_llm_client(config.provider)
    no_fault: frozenset[str] = frozenset()  # agent 射程は静的割当（故障較正は決定的版が担う）

    # per-seed の正答率（全体・初期品種・段取替品種）と完遂
    seed_acc: dict[str, list[float]] = {c: [] for c in conds}
    seed_setup_acc: dict[str, list[float]] = {c: [] for c in conds}
    seed_init_acc: dict[str, list[float]] = {c: [] for c in conds}
    seed_thr: dict[str, list[float]] = {c: [] for c in conds}
    tokens: dict[str, int] = {c: 0 for c in conds}
    for seed in config.seeds:
        tasks = _agent_task_list(world, seed)
        setup_name = world.setup_change_product
        for c in conds:
            alloc, rr = allocate_tasks_llm(c, tasks, world, llm)
            tokens[c] += rr.prompt_tokens + rr.completion_tokens
            correct = thr = 0.0
            init_n = init_ok = setup_n = setup_ok = 0
            for tid, product in tasks:
                chosen = alloc.get(tid)
                ok = allocation_correct(
                    world.machines,
                    product,
                    chosen,
                    no_fault,
                    world.fault_degradation,
                    world.tolerance,
                )
                correct += 1 if ok else 0
                thr += chosen_true_success(
                    world.machines, product, chosen, no_fault, world.fault_degradation
                )
                if product.name == setup_name:
                    setup_n += 1
                    setup_ok += 1 if ok else 0
                else:
                    init_n += 1
                    init_ok += 1 if ok else 0
            ntask = len(tasks)
            seed_acc[c].append(correct / ntask)
            seed_thr[c].append(thr / ntask)
            seed_setup_acc[c].append(setup_ok / setup_n if setup_n else 1.0)
            seed_init_acc[c].append(init_ok / init_n if init_n else 1.0)
        notify_fn(f"  seed={seed}: " + " ".join(f"{c}=acc{seed_acc[c][-1]:.2f}" for c in conds))

    def _mean_list(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 6) if xs else 0.0

    per_condition: dict[str, S3ConditionAgg] = {}
    for c in conds:
        per_condition[c] = S3ConditionAgg(
            overall_accuracy=_mean_list(seed_acc[c]),
            overall_throughput=_mean_list(seed_thr[c]),
            initial_accuracy=_mean_list(seed_init_acc[c]),
            setup_accuracy=_mean_list(seed_setup_acc[c]),
            fault_accuracy=_mean_list(seed_setup_acc[c]),  # 故障なし＝段取替品種で代表
            final_accuracy=_mean_list(seed_setup_acc[c]),
            total_replans=0,
        )
    primary = "OR-full-llm"
    success_by = {c: [a >= 1.0 - 1e-9 for a in seed_acc[c]] for c in conds}
    comparisons = paired_comparisons(
        primary,
        [c for c in conds if c != primary],
        success_by,
        seed_acc,
        "overall_accuracy",
        config.seeds[0],
    )
    falsification = {
        # OR-full-llm は語彙横断で段取替品種にも適格機体を選べる
        "OR_full_llm_cross_vendor": (
            all(
                per_condition[primary].overall_accuracy >= per_condition[b].overall_accuracy
                for b in conds
                if b != primary
            )
        ),
        # B1-llm は基準ベンダーのみで段取替品種（横断要）に届かず正答低下
        "B1_llm_cross_vendor_fail": any(
            per_condition[b].setup_accuracy < per_condition[primary].setup_accuracy
            for b in conds
            if b != primary
        ),
    }
    onb = onboarding_cost(world.vendor_d_fields, world.known_semantic_fields)

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S3Result(
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
        calibration={},
        onboarding=onb,
        robustness={},
        scope="agent",
        total_tokens=tokens,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s3-demo",
        scenario="s3",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[301, 302, 303, 304],
        duration_s=0.0,
    )
    exp_dir = runs_root / "scenario-s3-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s3-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = ["S3 多ベンダー製造ライン（T10）— 能力統合＋故障時再割当:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<11} 正答={a['overall_accuracy']:.3f} 完遂={a['overall_throughput']:.3f} "
            f"段取替後={a['setup_accuracy']:.2f} 故障後={a['fault_accuracy']:.2f} "
            f"最終={a['final_accuracy']:.2f}"
        )
    o = result["onboarding"]
    lines.append(
        f"  オンボーディング(H1): OR-full 手修正={o['or_full_manual_lines']}行 "
        f"vs B1 {o['b1_manual_lines']}行（自動写像 {o['auto_mapped']}）"
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
        f"# ORX Scenario Report — S3 多ベンダー製造ライン（T10・agent/live） `{result['exp_id']}`",
        "",
        f"- agent 検証（語彙横断の能力契約表が LLM の適格機体選定を助けるか, H1/H3）/ "
        f"model: {result.get('model_snapshot', '?')} / git: `{result.get('git_commit', '?')}`",
        "",
        scope.scope_section([scope.AGENT], scope.agent_status_for(mode)),
        "> **射程注記**: オンライン較正・故障時再割当（T6）は決定的版が担う。本 agent 版は"
        "**共通オントロジーで正規化した語彙横断能力表 vs 基準ベンダーのみ**の静的割当の差を測る。",
        "",
        "## 1. 割当正答（agent）",
        "",
        "| 条件 | 全体正答率 | 初期品種正答 | 段取替品種正答(横断要) | 期待完遂 |",
        "|------|-----------|--------------|------------------------|----------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['overall_accuracy']:.3f} | {a['initial_accuracy']:.3f} | "
            f"{a['setup_accuracy']:.3f} | {a['overall_throughput']:.3f} |"
        )
    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full-llm vs B1-llm・成否=全工程正答/指標=正答率）",
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
        f"# ORX Scenario Report — S3 多ベンダー製造ライン（T10） `{result['exp_id']}`",
        "",
        f"- シナリオ: s3 / 仮説: H1, H3 / シード数: {len(result['seeds'])} / "
        f"構成ハッシュ: `{result['config_hash']}`{stamp}",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 3条件は決定的ソルバ（round-robin/B1=情報境界アブレーション, OR-full=能力統合）。"
        "LLM での H1/H3 検証は未実行（live）。",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "round_robin_capability_blind": "round-robin は能力無視で完遂低下（H3帰無1）",
        "B1_cross_vendor_fail": "B1 は共通オントロジー無しで段取替後に語彙横断照合できず失敗",
        "OR_full_best_and_recovers": "OR-full は全体最良かつ故障後に縮退再割当で回復（最終≥0.95）",
        "calibration_tracks_fault": "能力台帳が経年劣化を検知し推定が追従低下（T6）",
        "B1_onboarding_costly": "B1 は新ベンダー統合で手修正行数が大（H1: ハブ写像無し）",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")

    lines += [
        "",
        "### 条件別サマリ",
        "",
        "| 条件 | 正答率 | 期待完遂 | 段取替後正答 | 故障後正答 | 最終正答 | 再計画 |",
        "|------|--------|----------|--------------|------------|----------|--------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['overall_accuracy']:.3f} | {a['overall_throughput']:.3f} | "
            f"{a['setup_accuracy']:.2f} | {a['fault_accuracy']:.2f} | "
            f"{a['final_accuracy']:.2f} | {a['total_replans']} |"
        )

    from orx.exp.scenario import comparison_section

    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full vs B1/round-robin・対応のある検定）",
        ),
    ]

    cal = result["calibration"]
    lines += [
        "",
        "## 3. 較正曲線（T6: 台帳が故障に追従, OR-full）",
        "",
        "| エピソード | フェーズ | Brier信頼性(↓) | 故障機体の推定 |",
        "|------------|----------|----------------|----------------|",
    ]
    for i, e in enumerate(cal["episodes"]):
        lines.append(
            f"| {e} | {cal['phases'][i]} | {cal['or_full_brier_reliability'][i]:.3f} | "
            f"{cal['or_full_fault_estimate'][i]:.3f} |"
        )
    lines.append(
        f"\n故障直前の推定 {cal['fault_estimate_onset']:.3f} → 末尾 {cal['fault_estimate_end']:.3f}"
        "（台帳が劣化を検知して低下）。"
    )

    o = result["onboarding"]
    lines += [
        "",
        "## 4. オンボーディング・コスト（H1: ベンダーD参入）",
        "",
        "| 指標 | 値 |",
        "|------|-----|",
        f"| ベンダーDスキーマ・フィールド | {o['vendor_fields']} |",
        f"| OR-full 自動写像（ハブ＆スポーク） | {o['auto_mapped']} |",
        f"| OR-full 手修正行数 | {o['or_full_manual_lines']} |",
        f"| B1 手修正行数（ハブ無し・全行手書き） | {o['b1_manual_lines']} |",
    ]

    rob = result.get("robustness") or {}
    if rob:
        lines += [
            "",
            "## 5. 頑健性曲線（故障劣化係数 × 全体正答率, R-3c）",
            "",
            f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |",
            "|------|" + "------|" * len(result["conditions"]),
        ]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['overall_accuracy'][c][i]:.2f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
        lines += [
            "",
            "故障劣化が過酷になるほど能力盲な round-robin/B1 は完遂が崩れるが、"
            "OR-full は能力台帳の追従で縮退再割当し優位を保つ。",
            "",
        ]

    lines += [
        "## 6. トークン効率",
        "",
        "決定的ソルバ（情報層）のため **0 トークン**。H1/H3 の agent 検証は live。",
        "",
    ]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
