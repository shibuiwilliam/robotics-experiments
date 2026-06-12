"""T1 越境同一性スイート（H2）。

課題: アームAが棚で識別した対象（バーコード指定）が搬出域へ搬送される。
搬出域を見ているのはID不可読のモバイルBのみ。実行器は世界グラフだけを窓に
「対象の現在位置」を決定する（D4: 決定的実行器 — LLM揺らぎと交絡させない）。

採点は決定時刻の真値と照合（採点部のみが真値に触れる）。物理的なピック実行は
P3のスキル導入後に接続する（決定の正しさがH2の被験変数）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import RunConfig, ScriptedMove, WorldConfig
from orx.common.schemas import FidelityReport, StrictModel, TruthState, Vec3
from orx.common.seeding import SeedTree
from orx.exp.episode import _apply_condition, _Pipeline
from orx.kg.world_graph import CURRENT_GRAPH, WorldGraph
from orx.oracle.truth import truth_snapshot
from orx.replay.io import RunReader


class T1Params(StrictModel):
    """T1エピソード生成パラメータ（実験コンフィグから）。"""

    target_pool: list[str]  # バーコード付き棚箱（ターゲット候補）
    decoy_pool: list[str]  # ID無し棚箱（デコイ候補）
    handoff_zone: str = "handoff"
    slide_start_min: float = 4.0
    slide_start_max: float = 6.0
    slide_duration_s: float = 6.0
    decision_offset_s: float = 3.0  # 滑走完了から決定までの猶予
    land_x_half_range: float = 0.45
    success_threshold_m: float = 0.30


class T1Task(StrictModel):
    """1エピソードの課題仕様。target_object_id は採点のみが使う。"""

    target_barcode: str
    target_object_id: str
    decision_time: float


class T1EpisodeOutcome(StrictModel):
    seed: int
    condition: str
    success: bool
    chosen_position: Vec3 | None
    identity_f1: float
    triple_f1: float


def generate_episode(
    base_world: WorldConfig, params: T1Params, seed: int
) -> tuple[WorldConfig, T1Task]:
    """シードからT1エピソード（搬送スクリプト＋課題仕様）を決定的に生成する。"""
    rng = SeedTree(seed).child("t1-gen").rng()
    target = params.target_pool[int(rng.integers(0, len(params.target_pool)))]
    decoy = params.decoy_pool[int(rng.integers(0, len(params.decoy_pool)))]
    barcode = next(b.barcode for b in base_world.boxes if b.name == target)
    if barcode is None:
        raise ValueError(f"T1ターゲット {target!r} にバーコードがありません")

    t_target = float(rng.uniform(params.slide_start_min, params.slide_start_max))
    # デコイは1.5〜2.5s ずらす: 運動コーンが時間で判別できる範囲。
    # 同時搬送（±1s未満）はLiDAR単独では原理的に曖昧 — H4（埋め込み）の領域で、
    # 劣化掃引（T4）で別途定量化する。
    stagger = float(rng.uniform(1.5, 2.5)) * (1.0 if rng.random() < 0.5 else -1.0)
    t_decoy = t_target + stagger
    x_target = float(rng.uniform(-params.land_x_half_range, params.land_x_half_range))
    # デコイはターゲットから最低0.5m離して着地させる（着地の重なりは別の実験変数）
    x_decoy = x_target + (0.5 if x_target < 0 else -0.5)

    # 着地は搬出域の北側ストリップ（y+0.25）— 常駐ディストラクタ（中央）と
    # 物理的に重ならない位置に降ろす
    moves = [
        ScriptedMove(
            box=target, at_time=round(t_target, 3), to_zone=params.handoff_zone,
            mode="slide", duration_s=params.slide_duration_s, offset=(round(x_target, 3), 0.25),
        ),
        ScriptedMove(
            box=decoy, at_time=round(max(0.5, t_decoy), 3), to_zone=params.handoff_zone,
            mode="slide", duration_s=params.slide_duration_s, offset=(round(x_decoy, 3), 0.25),
        ),
    ]
    world = base_world.model_copy(
        update={"scripted_moves": list(base_world.scripted_moves) + moves}
    )
    decision_time = max(t_target, t_decoy) + params.slide_duration_s + params.decision_offset_s
    task = T1Task(
        target_barcode=barcode,
        target_object_id=target,
        decision_time=round(decision_time, 3),
    )
    return world, task


def execute_t1(graph: WorldGraph, target_barcode: str, decision_time: float) -> Vec3 | None:
    """決定的実行器: 世界グラフのみを窓に、対象の現在位置を返す（無ければ None）。"""
    graph.refresh_current_graph(decision_time)
    rows = graph.query(
        f"""
        PREFIX orx-upper: <https://orx.local/onto/upper#>
        PREFIX orx-st: <https://orx.local/onto/st#>
        SELECT ?x ?y ?z WHERE {{
          GRAPH <{CURRENT_GRAPH}> {{
            ?e orx-upper:hasIdentifier "{target_barcode}" ;
               orx-st:posX ?x ; orx-st:posY ?y ; orx-st:posZ ?z .
          }}
        }}
        """
    )
    if not rows:
        return None
    row = rows[0]
    return (float(row["x"]), float(row["y"]), float(row["z"]))


def score_t1(
    choice: Vec3 | None, truth: TruthState, target_object_id: str, threshold_m: float
) -> bool:
    """採点: 選択位置に最も近い真値物体がターゲットで、距離が閾値内なら成功。"""
    if choice is None:
        return False
    best_id, best_d = None, float("inf")
    for obj in truth.objects:
        d = sum((a - b) ** 2 for a, b in zip(choice, obj.position, strict=True)) ** 0.5
        if d < best_d:
            best_id, best_d = obj.object_id, d
    return best_id == target_object_id and best_d <= threshold_m


def evaluate_condition(
    run_dir: Path, condition: str, task: T1Task, params: T1Params, seed: int
) -> tuple[T1EpisodeOutcome, FidelityReport]:
    """記録済みエピソードを条件で再生し、T1成否と忠実度を返す。"""
    reader = RunReader(run_dir)
    config: RunConfig = reader.config()
    _apply_condition(config, condition)
    stage = _Pipeline(config, SeedTree(config.root_seed))

    truth_states = list(reader.truth_states())
    events = list(reader.events())
    choice: Vec3 | None = None
    decided = False
    event_idx = 0
    end_time = truth_states[-1].sim_time if truth_states else 0.0
    truth_snaps = [truth_snapshot(s) for s in truth_states]

    def maybe_decide(now: float) -> None:
        nonlocal choice, decided
        if not decided and now >= task.decision_time - 1e-9:
            choice = execute_t1(stage.graph, task.target_barcode, task.decision_time)
            decided = True

    for truth in truth_states:
        while event_idx < len(events) and events[event_idx].sim_time <= truth.sim_time + 1e-9:
            maybe_decide(events[event_idx].sim_time)
            stage.feed(events[event_idx], writer=None)
            event_idx += 1
        stage.drain(truth.sim_time, writer=None)
        maybe_decide(truth.sim_time + 1e-9)
        stage.snapshot(truth.sim_time, writer=None)
    while event_idx < len(events):
        end_time = max(end_time, events[event_idx].sim_time)
        stage.feed(events[event_idx], writer=None)
        event_idx += 1
    stage.drain_all(writer=None)
    maybe_decide(end_time + 1.0)

    decision_truth = min(
        truth_states, key=lambda s: abs(s.sim_time - task.decision_time)
    )
    success = score_t1(
        choice, decision_truth, task.target_object_id, params.success_threshold_m
    )
    fidelity = stage.fidelity(truth_snaps, end_time)
    outcome = T1EpisodeOutcome(
        seed=seed,
        condition=condition,
        success=success,
        chosen_position=choice,
        identity_f1=fidelity.identity_f1,
        triple_f1=fidelity.triple_f1,
    )
    return outcome, fidelity
