"""S4 プラント点検（T11, H2/H5）ランナー: ID無しアンカリング＋信念調停の4条件→位置ノイズ掃引。

決定的（位置×署名アンカリング＋確信度調停）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.exp.record import persist_episode
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.stats import PairedComparison, paired_comparisons
from orx.exp.suites.s4_inspection import generator
from orx.exp.suites.s4_inspection.agent import AGENT_CONDITIONS
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
    conditions=CONDITIONS + AGENT_CONDITIONS,
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
    comparisons: list[PairedComparison] = []
    falsification: dict[str, bool]
    robustness: dict
    scope: str = "deterministic"  # "deterministic" | "agent" (R-B)
    total_tokens: dict[str, int] = {}


def _eval_seeds(
    world: S4World,
    seeds: list[int],
    pos_noise: float,
    record_dir: Path | None = None,
    knob: str | None = None,
) -> tuple[dict[str, S4ConditionAgg], dict[str, list[S4ConditionScore]]]:
    per: dict[str, list[S4ConditionScore]] = {c: [] for c in CONDITIONS}
    for seed in seeds:
        ep = generator.generate_episode(world, seed, pos_noise)
        persist_episode(record_dir, ep, seed, knob, pos_noise)  # D-3
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
    return agg, per


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    if any(c.endswith("-llm") for c in config.conditions):
        return run_agent(config, exp_dir, notify)
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S4World)
    pos_noise = float(config.params.get("position_noise", _DEFAULT_POS_NOISE))

    per_condition, per_seed = _eval_seeds(
        world, config.seeds, pos_noise, record_dir=exp_dir, knob="position_noise"
    )
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
        "or_sym_appearance_fail": (per_condition["OR-sym"].anchor_accuracy < full.anchor_accuracy),
        # OR-full は対応・異常正答最高かつ見逃し0
        "or_full_best": (
            full.anchor_accuracy >= per_condition["OR-sym"].anchor_accuracy
            and full.missed_anomalies == 0
            and full.anomaly_accuracy >= per_condition["OR-no-belief"].anomaly_accuracy
            and full.anomaly_accuracy >= per_condition["OR-no-identity"].anomaly_accuracy
        ),
    }

    # 対比較（OR-full vs 各アブレーション, R-3d）: seed 毎の成否＝見逃し0かつ対応完全。
    def _ok(s: S4ConditionScore) -> bool:
        return s.missed_anomalies == 0 and s.anchor_accuracy >= 1.0 - 1e-9

    success_by = {c: [_ok(s) for s in per_seed[c]] for c in CONDITIONS}
    metric_by = {c: [s.anchor_accuracy for s in per_seed[c]] for c in CONDITIONS}
    comparisons = paired_comparisons(
        "OR-full",
        [c for c in CONDITIONS if c != "OR-full"],
        success_by,
        metric_by,
        "anchor_accuracy",
        config.seeds[0],
    )

    knob = config.knob or "position_noise"
    values = config.knob_values or [0.2, 0.4, 0.6, 0.8]
    anchor_curve = {c: [] for c in CONDITIONS}
    anomaly_curve = {c: [] for c in CONDITIONS}
    for v in values:
        agg, _ = _eval_seeds(world, config.seeds, v, record_dir=exp_dir, knob=knob)
        for c in CONDITIONS:
            anchor_curve[c].append(round(agg[c].anchor_accuracy, 6))
            anomaly_curve[c].append(round(agg[c].anomaly_accuracy, 6))
        notify_fn(
            f"  {knob}={v}: " + " ".join(f"{c}={agg[c].anchor_accuracy:.2f}" for c in CONDITIONS)
        )
    robustness = {
        "knob": knob,
        "values": values,
        "anchor_accuracy": anchor_curve,
        "anomaly_accuracy": anomaly_curve,
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
        comparisons=comparisons,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def _score_anchors(world: S4World, episode, anchors: dict[int, str | None]) -> S4ConditionScore:
    """エージェントの対応付け（obs→asset）を採点。信念調停は確信度加重で決定的に適用する。

    対応付けの質（H2/H4: 署名の有無）が異常見逃しに波及することを観測する。真値照合は
    観測 true_asset・資産 true_anomaly と oracle ヘルパで行う（漏洩なし）。
    """
    from orx.exp.suites.s4_inspection.reference import reconcile
    from orx.oracle.scenarios.s4 import is_missed_anomaly, required_action

    anchored_correct = 0
    grouped: dict[str, list[tuple[bool, float]]] = {a.asset_id: [] for a in world.assets}
    for i, obs in enumerate(episode.observations):
        a_id = anchors.get(i)
        if a_id == obs.true_asset:
            anchored_correct += 1
        if a_id is not None:
            grouped.setdefault(a_id, []).append((obs.anomaly_reading, obs.confidence))
    anchor_acc = anchored_correct / len(episode.observations)

    n = len(world.assets)
    correct_status = missed = wo_correct = 0
    for asset in world.assets:
        concluded = reconcile("OR-full", grouped.get(asset.asset_id, []))  # 確信度加重
        if concluded == asset.true_anomaly:
            correct_status += 1
        if is_missed_anomaly(asset.true_anomaly, concluded):
            missed += 1
        issued = world.sop.get(asset.system) if concluded else None
        if issued == required_action(asset.true_anomaly, asset.system, world.sop):
            wo_correct += 1
    return S4ConditionScore(
        anchor_accuracy=round(anchor_acc, 6),
        anomaly_accuracy=round(correct_status / n, 6),
        missed_anomalies=missed,
        workorder_correct=round(wo_correct / n, 6),
    )


def run_agent(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    """S4 を**実 LLM エージェント**が対応付け（署名の有無で対比, agent 射程・R-B）。"""
    from orx.common.providers import make_llm_client
    from orx.exp.suites.s4_inspection.agent import anchor_llm

    notify_fn = notify if callable(notify) else (lambda *_: None)
    if config.provider is None:
        raise ValueError("agent 条件には provider 設定が必要です")
    world = load_config(repo_root() / config.world_config, S4World)
    pos_noise = float(config.params.get("position_noise", _DEFAULT_POS_NOISE))
    conds = [c for c in config.conditions if c in AGENT_CONDITIONS]
    llm = make_llm_client(config.provider)

    per: dict[str, list[S4ConditionScore]] = {c: [] for c in conds}
    tokens: dict[str, int] = {c: 0 for c in conds}
    for seed in config.seeds:
        ep = generator.generate_episode(world, seed, pos_noise)
        for c in conds:
            anchors, rr = anchor_llm(c, ep, world, llm)
            per[c].append(_score_anchors(world, ep, anchors))
            tokens[c] += rr.prompt_tokens + rr.completion_tokens
        notify_fn(
            f"  seed={seed}: "
            + " ".join(f"{c}=対応{per[c][-1].anchor_accuracy:.2f}" for c in conds)
        )

    per_condition: dict[str, S4ConditionAgg] = {}
    for c in conds:
        rows = per[c]
        m = len(rows)
        per_condition[c] = S4ConditionAgg(
            anchor_accuracy=round(sum(r.anchor_accuracy for r in rows) / m, 6),
            anomaly_accuracy=round(sum(r.anomaly_accuracy for r in rows) / m, 6),
            missed_anomalies=sum(r.missed_anomalies for r in rows),
            workorder_correct=round(sum(r.workorder_correct for r in rows) / m, 6),
        )
    primary = "OR-full-llm"

    def _ok(s: S4ConditionScore) -> bool:
        return s.missed_anomalies == 0 and s.anchor_accuracy >= 1.0 - 1e-9

    success_by = {c: [_ok(s) for s in per[c]] for c in conds}
    metric_by = {c: [s.anchor_accuracy for s in per[c]] for c in conds}
    comparisons = paired_comparisons(
        primary,
        [c for c in conds if c != primary],
        success_by,
        metric_by,
        "anchor_accuracy",
        config.seeds[0],
    )
    falsification = {
        # 署名を使う OR-full-llm は対応付けが最高
        "OR_full_llm_best_anchor": all(
            per_condition[primary].anchor_accuracy >= per_condition[b].anchor_accuracy
            for b in conds
            if b != primary
        ),
        # 署名なし(位置のみ)の baseline は誤対応で異常を見逃すか対応精度が落ちる
        "sym_llm_appearance_fail": any(
            per_condition[b].anchor_accuracy < per_condition[primary].anchor_accuracy
            or per_condition[b].missed_anomalies > per_condition[primary].missed_anomalies
            for b in conds
            if b != primary
        ),
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S4Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot=f"live: {config.provider.llm_model} (mode={config.provider.mode})",
        seeds=list(config.seeds),
        conditions=conds,
        primary_position_noise=pos_noise,
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
        name="s4-demo",
        scenario="s4",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[401, 402, 403, 404],
        duration_s=0.0,
        knob="position_noise",
        knob_values=[0.2, 0.4, 0.6, 0.8],
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


def _render_agent_report(result: dict) -> str:
    from orx.exp import scope
    from orx.exp.scenario import comparison_section

    mode = (
        "openai"
        if "mode=openai" in result.get("model_snapshot", "")
        else ("cache" if "mode=cache" in result.get("model_snapshot", "") else "stub")
    )
    lines = [
        f"# ORX Scenario Report — S4 プラント点検（T11・agent/live） `{result['exp_id']}`",
        "",
        f"- agent 検証（視覚署名による ID 無し同定が LLM の対応付けを助けるか, H2/H4）/ "
        f"model: {result.get('model_snapshot', '?')} / git: `{result.get('git_commit', '?')}`",
        "",
        scope.scope_section([scope.AGENT], scope.agent_status_for(mode)),
        "> **射程注記**: 同一性/信念機構のオン・オフは決定的アブレーションが担う。本 agent 版は"
        "**位置＋視覚署名 vs 位置のみ**で LLM の対応付け品質を測り、対応の質が異常見逃しに波及"
        "することを観測する（信念調停は両条件とも確信度加重で決定的に適用）。",
        "",
        "## 1. 対応付け・異常結論（agent）",
        "",
        "| 条件 | 対応付け精度 | 異常正答率 | 見逃し | 作業指示正答 |",
        "|------|--------------|------------|--------|--------------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['anchor_accuracy']:.3f} | {a['anomaly_accuracy']:.3f} | "
            f"{a['missed_anomalies']} | {a['workorder_correct']:.3f} |"
        )
    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full-llm vs OR-sym-llm・成否=対応完全＆見逃し0/指標=対応精度）",
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

    lines += [
        "",
        f"### 条件別サマリ（位置ノイズ={result['primary_position_noise']}）",
        "",
        "| 条件 | 対応付け精度 | 異常正答率 | 見逃し | 作業指示正答 |",
        "|------|--------------|------------|--------|--------------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['anchor_accuracy']:.3f} | {a['anomaly_accuracy']:.3f} | "
            f"{a['missed_anomalies']} | {a['workorder_correct']:.3f} |"
        )

    from orx.exp.scenario import comparison_section

    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full vs アブレーション・対応のある検定）",
        ),
    ]

    rob = result.get("robustness") or {}
    lines += ["", "## 2. 頑健性曲線（位置ノイズ × 対応付け精度）", ""]
    if rob:
        header = f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |"
        lines += [header, "|------|" + "------|" * len(result["conditions"])]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['anchor_accuracy'][c][i]:.2f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
        lines.append("\n（位置ノイズ増で OR-sym の対応付けが崩れ、OR-full は署名で同定を維持）")
    lines += [
        "",
        "## 3. トークン効率",
        "",
        "決定的ソルバ（情報層）のため **0 トークン**。H2/H5 の agent 検証は live。",
        "",
    ]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
