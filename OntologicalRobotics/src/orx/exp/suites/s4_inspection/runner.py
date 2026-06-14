"""S4 プラント点検（T11, H2/H5）ランナー: ID無しアンカリング＋信念調停の4条件→位置ノイズ掃引。

決定的（位置×署名アンカリング＋確信度調停）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.suites.s4_inspection import generator
from orx.exp.suites.s4_inspection.model import S4World
from orx.exp.suites.s4_inspection.reference import CONDITIONS
from orx.exp.suites.s4_inspection.scorer import S4ConditionScore, score_condition

WORLD = "configs/world/s4_inspection.yaml"
_DEFAULT_POS_NOISE = 0.4

META = ScenarioMeta(
    id="s4",
    suite="T11",
    slug="inspection",
    title="プラント点検：図面個体と知覚個体の同一化",
    tier="B",
    touchstones=["越境同一性（ID無し）", "信念調停"],
    hypotheses=["H2", "H5"],
    conditions=CONDITIONS,
    status="implemented",
)


class S4ConditionAgg(StrictModel):
    anchor_accuracy: float
    anomaly_accuracy: float
    missed_anomalies: int
    workorder_correct: float


class S4Result(StrictModel):
    scenario: str = "s4"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    primary_position_noise: float
    per_condition: dict[str, S4ConditionAgg]
    falsification: dict[str, bool]
    robustness: dict


def _eval_seeds(world: S4World, seeds: list[int], pos_noise: float) -> dict[str, S4ConditionAgg]:
    per: dict[str, list[S4ConditionScore]] = {c: [] for c in CONDITIONS}
    for seed in seeds:
        ep = generator.generate_episode(world, seed, pos_noise)
        for c in CONDITIONS:
            per[c].append(score_condition(world, ep, c))
    agg: dict[str, S4ConditionAgg] = {}
    for c in CONDITIONS:
        rows = per[c]
        m = len(rows)
        agg[c] = S4ConditionAgg(
            anchor_accuracy=round(sum(r.anchor_accuracy for r in rows) / m, 6),
            anomaly_accuracy=round(sum(r.anomaly_accuracy for r in rows) / m, 6),
            missed_anomalies=sum(r.missed_anomalies for r in rows),
            workorder_correct=round(sum(r.workorder_correct for r in rows) / m, 6),
        )
    return agg


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S4World)
    pos_noise = float(config.params.get("position_noise", _DEFAULT_POS_NOISE))

    per_condition = _eval_seeds(world, config.seeds, pos_noise)
    for c in CONDITIONS:
        a = per_condition[c]
        notify_fn(
            f"  {c:<15} 対応={a.anchor_accuracy:.3f} 異常正答={a.anomaly_accuracy:.3f} "
            f"見逃し={a.missed_anomalies} 起票={a.workorder_correct:.3f}"
        )

    full = per_condition["OR-full"]
    falsification = {
        # H2: 同一性解決なしでは台帳と観測を結べず状態クエリに答えられない
        "no_identity_cannot_anchor": per_condition["OR-no-identity"].anchor_accuracy <= 1e-9,
        # H5: 信念調停なしでは矛盾観測を解けず真の異常を見逃す
        "no_belief_misses_anomalies": per_condition["OR-no-belief"].missed_anomalies > 0,
        # H2/H4: 署名なし（位置のみ）は位置曖昧で対応付け精度が落ちる
        "or_sym_appearance_fail": (
            per_condition["OR-sym"].anchor_accuracy < full.anchor_accuracy
        ),
        # OR-full は対応・異常正答最高かつ見逃し0
        "or_full_best": (
            full.anchor_accuracy >= per_condition["OR-sym"].anchor_accuracy
            and full.missed_anomalies == 0
            and full.anomaly_accuracy >= per_condition["OR-no-belief"].anomaly_accuracy
            and full.anomaly_accuracy >= per_condition["OR-no-identity"].anomaly_accuracy
        ),
    }

    knob = config.knob or "position_noise"
    values = config.knob_values or [0.2, 0.4, 0.6, 0.8]
    anchor_curve = {c: [] for c in CONDITIONS}
    anomaly_curve = {c: [] for c in CONDITIONS}
    for v in values:
        agg = _eval_seeds(world, config.seeds, v)
        for c in CONDITIONS:
            anchor_curve[c].append(round(agg[c].anchor_accuracy, 6))
            anomaly_curve[c].append(round(agg[c].anomaly_accuracy, 6))
        notify_fn(
            f"  {knob}={v}: " + " ".join(f"{c}={agg[c].anchor_accuracy:.2f}" for c in CONDITIONS)
        )
    robustness = {
        "knob": knob, "values": values,
        "anchor_accuracy": anchor_curve, "anomaly_accuracy": anomaly_curve,
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S4Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (stub visual signature, no LLM)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        primary_position_noise=pos_noise,
        per_condition=per_condition,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s4-demo", scenario="s4", world_config=WORLD, conditions=CONDITIONS,
        seeds=[401, 402, 403, 404], duration_s=0.0,
        knob="position_noise", knob_values=[0.2, 0.4, 0.6, 0.8],
        params={"position_noise": 0.4},
    )
    exp_dir = runs_root / "scenario-s4-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s4-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    pn = result["primary_position_noise"]
    lines = [f"S4 プラント点検（T11）— 対応付け＋調停（位置ノイズ={pn}）:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<15} 対応={a['anchor_accuracy']:.3f} 異常正答={a['anomaly_accuracy']:.3f} "
            f"見逃し={a['missed_anomalies']} 起票={a['workorder_correct']:.3f}"
        )
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def render_report(result: dict) -> str:
    from orx.exp import scope

    stamp = f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
    lines = [
        f"# ORX Scenario Report — S4 プラント点検（T11） `{result['exp_id']}`",
        "",
        f"- シナリオ: s4 / 仮説: H2, H5 / シード数: {len(result['seeds'])} / "
        f"位置ノイズ={result['primary_position_noise']} / 構成: `{result['config_hash']}`{stamp}",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 4条件は決定的ソルバ（no-identity/no-belief/OR-sym=アブレ, OR-full=同一性＋信念）。"
        "H2/H5 の agent 検証は未実行（live）。",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "no_identity_cannot_anchor": "同一性解決なしは台帳↔観測を結べず状態クエリ不能（H2）",
        "no_belief_misses_anomalies": "信念調停なしは矛盾観測を解けず真の異常を見逃す（H5）",
        "or_sym_appearance_fail": "署名なし（位置のみ）は位置曖昧で対応付け精度低下（H2/H4）",
        "or_full_best": "OR-full は対応・異常正答が最高かつ見逃し0",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")

    lines += ["", f"### 条件別サマリ（位置ノイズ={result['primary_position_noise']}）", "",
              "| 条件 | 対応付け精度 | 異常正答率 | 見逃し | 作業指示正答 |",
              "|------|--------------|------------|--------|--------------|"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['anchor_accuracy']:.3f} | {a['anomaly_accuracy']:.3f} | "
            f"{a['missed_anomalies']} | {a['workorder_correct']:.3f} |"
        )

    rob = result.get("robustness") or {}
    lines += ["", "## 2. 頑健性曲線（位置ノイズ × 対応付け精度）", ""]
    if rob:
        header = f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |"
        lines += [header, "|------|" + "------|" * len(result["conditions"])]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['anchor_accuracy'][c][i]:.2f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
        lines.append("\n（位置ノイズ増で OR-sym の対応付けが崩れ、OR-full は署名で同定を維持）")
    lines += ["", "## 3. トークン効率", "",
              "決定的ソルバ（情報層）のため **0 トークン**。H2/H5 の agent 検証は live。", ""]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
