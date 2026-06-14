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
    conditions=CONDITIONS,
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
    falsification: dict[str, bool]
    calibration: dict
    onboarding: OnboardingCost


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
        falsification=falsification,
        calibration=calibration,
        onboarding=onb,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s3-demo", scenario="s3", world_config=WORLD, conditions=CONDITIONS,
        seeds=[301, 302, 303, 304], duration_s=0.0,
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


def render_report(result: dict) -> str:
    from orx.exp import scope

    stamp = f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
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

    lines += ["", "### 条件別サマリ", "",
              "| 条件 | 正答率 | 期待完遂 | 段取替後正答 | 故障後正答 | 最終正答 | 再計画 |",
              "|------|--------|----------|--------------|------------|----------|--------|"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['overall_accuracy']:.3f} | {a['overall_throughput']:.3f} | "
            f"{a['setup_accuracy']:.2f} | {a['fault_accuracy']:.2f} | "
            f"{a['final_accuracy']:.2f} | {a['total_replans']} |"
        )

    cal = result["calibration"]
    lines += ["", "## 2. 較正曲線（T6: 台帳が故障に追従, OR-full）", "",
              "| エピソード | フェーズ | Brier信頼性(↓) | 故障機体の推定 |",
              "|------------|----------|----------------|----------------|"]
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
    lines += ["", "## 3. オンボーディング・コスト（H1: ベンダーD参入）", "",
              "| 指標 | 値 |", "|------|-----|",
              f"| ベンダーDスキーマ・フィールド | {o['vendor_fields']} |",
              f"| OR-full 自動写像（ハブ＆スポーク） | {o['auto_mapped']} |",
              f"| OR-full 手修正行数 | {o['or_full_manual_lines']} |",
              f"| B1 手修正行数（ハブ無し・全行手書き） | {o['b1_manual_lines']} |"]
    lines += ["", "## 4. トークン効率", "",
              "決定的ソルバ（情報層）のため **0 トークン**。H1/H3 の agent 検証は live。", ""]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
