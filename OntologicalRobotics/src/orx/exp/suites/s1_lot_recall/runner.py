"""S1 ロット回収（T8）ランナー: 記録→条件別ソルバ→採点→対比較＋頑健性掃引。

評価方法論は既存基盤を再利用（記録1回／シード→3条件ソルバ→対応ありMcNemar）。
"""

from __future__ import annotations

from pathlib import Path

from orx.business.db import generate_lot_tables, generate_wms
from orx.business.lifting import lot_claims, wms_claims
from orx.common.config import RunConfig, WorldConfig, config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.episode import _Pipeline, record_episode
from orx.exp.scenario import (
    ScenarioExperimentConfig,
    ScenarioMeta,
    ScenarioSpec,
    register,
)
from orx.exp.stats import PairedComparison, compare_conditions
from orx.exp.suites.s1_lot_recall import generator
from orx.exp.suites.s1_lot_recall.reference import solve_b0, solve_b1, solve_or
from orx.exp.suites.s1_lot_recall.scorer import RecallScore, score_recall
from orx.oracle.scenarios.s1 import recall_truth
from orx.replay.io import RunReader

CONDITIONS = ["OR-full", "B0", "B1"]
WORLD = "configs/world/s1_lot_recall.yaml"

META = ScenarioMeta(
    id="s1",
    suite="T8",
    slug="lot_recall",
    title="ロット回収下の倉庫オペレーション",
    tier="A",
    touchstones=["①越境同一性", "③監査"],
    hypotheses=["H2", "H6", "H7"],
    conditions=CONDITIONS,
    status="implemented",
)


class ConditionAgg(StrictModel):
    completion: float
    membership_recall: float
    membership_f1: float
    location_accuracy: float
    false_quarantine_total: int
    success_rate: float


class S1Result(StrictModel):
    scenario: str = "s1"
    exp_id: str
    name: str
    config_hash: str
    seeds: list[int]
    conditions: list[str]
    recall_lots: dict[str, str]  # seed(str) -> lot
    per_condition: dict[str, ConditionAgg]
    comparisons: list[PairedComparison]
    falsification: dict[str, bool]
    robustness: dict  # {knob, values, completion: {cond: [..]}}


# --------------------------------------------------------------- evaluation


class PreparedEpisode(StrictModel):
    """1シードの記録から組み立てた条件別ソルバの入力一式（共有ビルド経路）。"""

    model_config = {"arbitrary_types_allowed": True}
    world: WorldConfig
    recall_lot: str
    recall_time: float
    lot_members: dict[str, str]
    graph: object  # WorldGraph（OR-full: anchoring融合＋lot/wms主張）
    latest_raw: dict  # robot_id -> RawObservation
    events: list  # PerceptionEvent（<= recall_time）
    truth_states: list  # TruthState


def prepare_episode(
    base_world: WorldConfig,
    seed: int,
    runs_root: Path,
    duration_s: float,
    claim_ttl_s: float,
    knob: str | None = None,
    value: float = 0.0,
) -> PreparedEpisode:
    """エピソードを記録し、世界グラフ・真値・条件別入力を組み立てる（再利用される）。"""
    world = base_world
    if knob is not None:
        degr = base_world.degradation.model_copy(update={knob: value})
        world = base_world.model_copy(update={"degradation": degr})

    run_cfg = RunConfig(
        world=world, duration_s=duration_s, root_seed=seed, claim_ttl_s=claim_ttl_s
    )
    tag = f"seed{seed}" if knob is None else f"{knob}-{value}-seed{seed}"
    run_id, _ = record_episode(run_cfg, runs_root, run_id=tag)
    run_dir = runs_root / run_id

    record = generate_wms(world, SeedTree(seed), run_dir / "wms.sqlite")
    recall_lot = generator.choose_recall_lot(world, seed)
    rt = generator.recall_time(world)
    lot_data = generate_lot_tables(world, run_dir / "wms.sqlite", recall_lot, rt)

    reader = RunReader(run_dir)
    truth_states = list(reader.truth_states())
    latest_raw = {}
    for obs in reader.observations():
        if obs.sim_time <= rt + 1e-9:
            latest_raw[obs.robot_id] = obs
    events = [e for e in reader.events() if e.sim_time <= rt + 1e-9]

    stage = _Pipeline(reader.config(), SeedTree(seed))
    for ev in events:
        stage.feed(ev, writer=None)
    stage.drain_all(writer=None)
    for claim in wms_claims(record, SeedTree(seed)):
        stage.graph.assert_claim(claim)
    for claim in lot_claims(record, lot_data, SeedTree(seed)):
        stage.graph.assert_claim(claim)

    return PreparedEpisode(
        world=world,
        recall_lot=recall_lot,
        recall_time=rt,
        lot_members=lot_data.members,
        graph=stage.graph,
        latest_raw=latest_raw,
        events=events,
        truth_states=truth_states,
    )


def _eval_seed(
    base_world: WorldConfig,
    seed: int,
    runs_root: Path,
    knob: str | None,
    value: float,
    duration_s: float,
    claim_ttl_s: float,
) -> tuple[dict[str, RecallScore], str]:
    """1シードを記録し、3条件ソルバを走らせて採点する。返り値 (scores, recall_lot)。"""
    ep = prepare_episode(base_world, seed, runs_root, duration_s, claim_ttl_s, knob, value)
    truth = recall_truth(ep.truth_states, ep.lot_members, ep.recall_lot, ep.recall_time)
    answers = {
        "B0": solve_b0(ep.latest_raw, ep.world.zones, ep.lot_members, ep.recall_lot),
        "B1": solve_b1(ep.events, ep.world.zones, ep.lot_members, ep.recall_lot),
        "OR-full": solve_or(ep.graph, ep.recall_lot, ep.recall_time),
    }
    scores = {c: score_recall(answers[c], truth) for c in CONDITIONS}
    return scores, ep.recall_lot


def _aggregate(per_seed: dict[int, dict[str, RecallScore]]) -> dict[str, ConditionAgg]:
    seeds = list(per_seed)
    out: dict[str, ConditionAgg] = {}
    for c in CONDITIONS:
        rows = [per_seed[s][c] for s in seeds]
        n = len(rows)
        out[c] = ConditionAgg(
            completion=round(sum(r.completion for r in rows) / n, 6),
            membership_recall=round(sum(r.membership_recall for r in rows) / n, 6),
            membership_f1=round(sum(r.membership_f1 for r in rows) / n, 6),
            location_accuracy=round(sum(r.location_accuracy for r in rows) / n, 6),
            false_quarantine_total=sum(r.false_quarantine for r in rows),
            success_rate=round(sum(1 for r in rows if r.success) / n, 6),
        )
    return out


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    notify_fn = notify if callable(notify) else (lambda *_: None)
    base_world = load_config(repo_root() / config.world_config, WorldConfig)
    episodes = exp_dir / "episodes"

    # 主評価（ノイズ0）
    per_seed: dict[int, dict[str, RecallScore]] = {}
    recall_lots: dict[str, str] = {}
    for seed in config.seeds:
        scores, lot = _eval_seed(
            base_world, seed, episodes, None, 0.0, config.duration_s, config.claim_ttl_s
        )
        per_seed[seed] = scores
        recall_lots[str(seed)] = lot
        notify_fn(
            f"  seed={seed} lot={lot}: "
            + " ".join(f"{c}={scores[c].completion:.2f}" for c in CONDITIONS)
        )
    per_condition = _aggregate(per_seed)

    # 対比較（OR-full vs B0 / B1）
    rng = SeedTree(config.seeds[0]).child("bootstrap").rng()
    or_success = [per_seed[s]["OR-full"].success for s in config.seeds]
    comparisons = []
    for other in ("B0", "B1"):
        other_success = [per_seed[s][other].success for s in config.seeds]
        comparisons.append(
            compare_conditions(
                "OR-full", other, or_success, other_success, rng,
                metric_a=[per_seed[s]["OR-full"].completion for s in config.seeds],
                metric_b=[per_seed[s][other].completion for s in config.seeds],
                metric_name="completion",
            )
        )

    # 失敗予言の検証（情報層での構造的失敗を数値でアサート）
    falsification = {
        "B0_misses_in_transit": per_condition["B0"].membership_recall < 1.0 - 1e-9,
        "B1_stale_location": per_condition["B1"].location_accuracy < 1.0 - 1e-9,
        "OR_full_succeeds": per_condition["OR-full"].completion >= 1.0 - 1e-9,
    }

    # 頑健性掃引（任意）
    robustness: dict = {}
    if config.knob:
        values = config.knob_values or [0.0, 0.1, 0.2, 0.3]
        completion_curve = {c: [] for c in CONDITIONS}
        for value in values:
            sweep_seed_scores: dict[int, dict[str, RecallScore]] = {}
            for seed in config.seeds:
                scores, _ = _eval_seed(
                    base_world, seed, episodes, config.knob, value,
                    config.duration_s, config.claim_ttl_s,
                )
                sweep_seed_scores[seed] = scores
            agg = _aggregate(sweep_seed_scores)
            for c in CONDITIONS:
                completion_curve[c].append(round(agg[c].completion, 6))
            notify_fn(
                f"  {config.knob}={value}: "
                + " ".join(f"{c}={completion_curve[c][-1]:.2f}" for c in CONDITIONS)
            )
        robustness = {"knob": config.knob, "values": values, "completion": completion_curve}

    result = S1Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        recall_lots=recall_lots,
        per_condition=per_condition,
        comparisons=comparisons,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


# ------------------------------------------------------------------ demo/report


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s1-demo",
        scenario="s1",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[101, 102, 103],
        duration_s=18.0,
        knob="id_read_failure_rate",
        knob_values=[0.0, 0.3, 0.6],
    )
    exp_dir = runs_root / "scenario-s1-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s1-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = ["S1 ロット回収（T8）— 回収完遂率:"]
    for c in result["conditions"]:
        agg = result["per_condition"][c]
        lines.append(
            f"  {c:<9} completion={agg['completion']:.3f} "
            f"membership_F1={agg['membership_f1']:.3f} "
            f"location_acc={agg['location_accuracy']:.3f}"
        )
    for cmp in result["comparisons"]:
        lines.append(
            f"McNemar (OR-full vs {cmp['condition_b']}): p = {cmp['mcnemar_p']:.2e}"
        )
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def render_report(result: dict) -> str:
    lines = [
        f"# ORX Scenario Report — S1 ロット回収（T8） `{result['exp_id']}`",
        "",
        f"- シナリオ: s1 / 仮説: H2, H6, H7 / シード数: {len(result['seeds'])} / "
        f"構成ハッシュ: `{result['config_hash']}`",
        f"- 回収ロット（シード別）: {result['recall_lots']}",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "B0_misses_in_transit": "B0 は搬送中・ID不可読個体のロット帰属を解けない（列挙recall<1）",
        "B1_stale_location": "B1 は横断同一性が無く搬送済み個体の現在地が陳腐化する",
        "OR_full_succeeds": "OR-full は越境同一性＋識別子スレッドで完遂率100%",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")
    lines += ["", "### 条件別サマリ（ノイズ0）", "",
              "| 条件 | 完遂率 | 列挙F1 | 現在地精度 | 誤隔離 | 成功率 |",
              "|------|--------|--------|-----------|--------|--------|"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['completion']:.3f} | {a['membership_f1']:.3f} | "
            f"{a['location_accuracy']:.3f} | {a['false_quarantine_total']} | "
            f"{a['success_rate']:.3f} |"
        )
    lines += ["", "### 対比較（対応のあるMcNemar）", ""]
    for cmp in result["comparisons"]:
        lines.append(
            f"- OR-full vs {cmp['condition_b']}: 成功率 {cmp['success_rate_a']:.3f} / "
            f"{cmp['success_rate_b']:.3f}, McNemar p = {cmp['mcnemar_p']:.2e}"
        )

    lines += ["", "## 2. 頑健性曲線", ""]
    rob = result.get("robustness") or {}
    if rob:
        knob = rob["knob"]
        header = f"| {knob} | " + " | ".join(result["conditions"]) + " |"
        lines += [header, "|------|" + "------|" * len(result["conditions"])]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['completion'][c][i]:.3f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
    else:
        lines.append("（掃引なし）")

    lines += [
        "",
        "## 3. トークン効率",
        "",
        "本シナリオの反証は決定的リファレンスソルバ（情報層）で証明するため **0 トークン**"
        "（LLM不使用・stubで常時CI）。LLMエージェント版の正答率/トークンは live 計測"
        "（provider.mode=openai）で別途記録する。",
        "",
    ]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
