"""EXP-02 遮蔽下の同一性維持（H1、poc_plan.md 6.2）の測定ロジック。

条件：WM予測位置を使う／使わない（単純トラッキング）の比較。出力：ID切替率、IDF1。

このセッション（06）の実装範囲での簡略化（docs/status.md に明記）：
「WM予測位置」条件は、`wm.dynamics.Dynamics` の本物のロールアウトではなく、
直近の既知速度による等速直線外挿を使う。本物のロールアウトは学習済みスロット
空間で動作し、遮蔽開始時点でその個体がどのスロットに対応していたかを毎フレーム
の視覚パイプライン（エンコーダ→スロット→融合）を通して特定する必要があり、
smoke規模を超える計算になるため本実行時に置き換える。ここで検証するのは
`grounding/identity.py`（ハンガリアン割当・遮蔽保持・強制再同定）が実際に
複数フレームにわたって正しく機能するかどうかで、これは
`update_occluded_entities` が今まで単体テスト以外で一度も通しで実行されて
いなかった（`ground_run.py` は毎フレーム真値からエンティティを作り直しており、
時間的な保持ロジックを経由していなかった）ため、smoke でも実施する価値がある。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from omegaconf import DictConfig

from gtwm.eval.metrics import count_id_switches, id_counts_from_matches, id_switch_rate, idf1
from gtwm.grounding.identity import (
    TrackedEntity,
    resolve_identities,
    update_occluded_entities,
)
from gtwm.sim.env import AGV_LOOPS, N_CASES, N_PALLETS
from gtwm.utils.paths import data_dir

OCCLUSION_THRESHOLD = 0.7  # この値を超える occlusion を「未検出（遮蔽中）」とみなす
DETECTION_NOISE_STD = 0.02  # 観測ノイズ（m）。センサ誤差の簡易モデル
DISTANCE_GATE = 1.5  # ハンガリアン割当の採用上限距離（m）


def occlusion_entity_names() -> list[str]:
    """`sim/env.py` の `Env.occlusion_names` と同じ順序（pallet→case→agv）を、
    モデルを構築せずに公開定数から再現する。"""
    return (
        [f"pallet:{i}" for i in range(1, N_PALLETS + 1)]
        + [f"case:{i}" for i in range(1, N_CASES + 1)]
        + list(AGV_LOOPS.keys())
    )


@dataclass
class _Frame:
    t: float
    xy_by_entity: dict[str, np.ndarray]
    occluded_by_entity: dict[str, bool]


def _load_frames(episode_dir: str) -> list[_Frame]:
    poses = pd.read_parquet(f"{episode_dir}/poses.parquet")
    occ = np.load(f"{episode_dir}/occlusion.npz")
    names = occlusion_entity_names()
    # カメラ間の最良視点（occlusion 最小）を採用：全カメラで隠れていて初めて未検出とする。
    best_cam_occlusion = np.min(np.stack([occ[k] for k in occ], axis=0), axis=0)  # [T,N]

    frames: list[_Frame] = []
    for t_idx, t in enumerate(sorted(poses["t"].unique())):
        rows = poses[poses["t"] == t]
        # 積み重ねられたパレット/ケースは床面座標(x,y)が同一（zのみ異なる）になる。外観
        # 埋め込みは現状どこも未実装（ゼロ埋め）で位置以外に判別材料が無いため、2D floor
        # 座標ではなく3D位置(x,y,z)を使う（identity.py のコストは次元非依存の
        # ユークリッド距離なのでそのまま使える）。位置以外の判別材料が無い状態で2Dのみ
        # 使うと、同一(x,y)の積み重ね物体間でハンガリアン割当がフレームごとにほぼ
        # ランダムに入れ替わり、遮蔽そのものとは無関係な「ID切替」が支配的になることを
        # 実測で確認した（EXP-02 実装時の発見、docs/status.md 参照）。
        xy_by_entity = {str(r.entity_gt_id): np.array([r.x, r.y, r.z]) for r in rows.itertuples()}
        entity_by_sim_name = {str(r.entity): str(r.entity_gt_id) for r in rows.itertuples()}
        occluded_by_entity = {}
        for i, name in enumerate(names):
            gt_id = entity_by_sim_name.get(name)
            if gt_id is None or t_idx >= best_cam_occlusion.shape[0]:
                continue
            occluded_by_entity[gt_id] = bool(best_cam_occlusion[t_idx, i] > OCCLUSION_THRESHOLD)
        frames.append(
            _Frame(t=float(t), xy_by_entity=xy_by_entity, occluded_by_entity=occluded_by_entity)
        )
    return frames


def _run_condition(frames: list[_Frame], use_forward_prediction: bool, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    all_ids = sorted(frames[0].xy_by_entity.keys())
    entities = [
        TrackedEntity(
            entity_gt_id=gt_id,
            predicted_xy=frames[0].xy_by_entity[gt_id].copy(),
            appearance=np.zeros(1),
            last_seen_step=0,
        )
        for gt_id in all_ids
    ]
    velocity: dict[str, np.ndarray] = {gt_id: np.zeros(3) for gt_id in all_ids}
    last_visible_xy: dict[str, np.ndarray] = {
        gt_id: frames[0].xy_by_entity[gt_id].copy() for gt_id in all_ids
    }

    track_sequences: dict[str, list[str | None]] = {gt_id: [] for gt_id in all_ids}
    matched_pairs_per_frame: list[list[tuple[str, str]]] = []
    n_unmatched_preds_per_frame: list[int] = []
    n_unmatched_gts_per_frame: list[int] = []

    for frame in frames:
        visible_ids = [gt_id for gt_id in all_ids if not frame.occluded_by_entity.get(gt_id, True)]
        if not visible_ids:
            slot_positions = np.zeros((0, 3))
        else:
            slot_positions = np.stack(
                [
                    frame.xy_by_entity[gt_id] + rng.normal(0, DETECTION_NOISE_STD, size=3)
                    for gt_id in visible_ids
                ]
            )
        slot_appearance = np.zeros((len(visible_ids), 1))

        assignment = resolve_identities(
            slot_positions, slot_appearance, entities, distance_gate=DISTANCE_GATE
        )
        # resolve_identities は「スロットindex(0..)→トラックのentity_gt_id」を返すが、
        # スロットindexは visible_ids のインデックスと一致する（同じ順序で構築したため）
        # ので、それを引けば「このスロットの実際の中身（真の個体）」が分かる。
        slot_idx_to_true_gt = dict(enumerate(visible_ids))

        # matched_pairs の1要素目はトラックの永続ID（TrackedEntity.entity_gt_id、
        # 生成時から変わらない）、2要素目はそのスロットの真の個体（食い違えば誤対応）。
        matched_pairs = [
            (track_id, slot_idx_to_true_gt[slot_idx])
            for slot_idx, track_id in assignment.slot_to_entity.items()
        ]
        matched_pairs_per_frame.append(matched_pairs)
        n_unmatched_preds_per_frame.append(len(assignment.unmatched_slots))
        n_unmatched_gts_per_frame.append(len(assignment.unmatched_entities))

        for track_id, true_gt_at_slot in matched_pairs:
            track_sequences[track_id].append(true_gt_at_slot)
            velocity[true_gt_at_slot] = (
                frame.xy_by_entity[true_gt_at_slot] - last_visible_xy[true_gt_at_slot]
            )
            last_visible_xy[true_gt_at_slot] = frame.xy_by_entity[true_gt_at_slot].copy()
        for entity_gt_id in assignment.unmatched_entities:
            track_sequences[entity_gt_id].append(None)

        predicted_next_xy: dict[str, np.ndarray] = {}
        if use_forward_prediction:
            for entity_gt_id in assignment.unmatched_entities:
                predicted_next_xy[entity_gt_id] = (
                    last_visible_xy[entity_gt_id] + velocity[entity_gt_id]
                )

        entities = update_occluded_entities(
            entities, assignment, predicted_next_xy, step=0, slot_positions=slot_positions
        )

    total_switches = sum(count_id_switches(seq) for seq in track_sequences.values())
    duration_hours = (frames[-1].t - frames[0].t) / 3600.0
    idtp, idfp, idfn = id_counts_from_matches(
        matched_pairs_per_frame, n_unmatched_preds_per_frame, n_unmatched_gts_per_frame
    )
    return {
        "n_switches": total_switches,
        "n_entities": len(all_ids),
        "duration_hours": duration_hours,
        "id_switch_rate": id_switch_rate(total_switches, len(all_ids), max(duration_hours, 1e-6)),
        "idf1": idf1(idtp, idfp, idfn),
        "idtp": idtp,
        "idfp": idfp,
        "idfn": idfn,
    }


def _measure_single_episode(episode_dir: str, seed: int) -> dict[str, Any]:
    frames = _load_frames(episode_dir)
    baseline = _run_condition(frames, use_forward_prediction=False, seed=seed)
    with_prediction = _run_condition(frames, use_forward_prediction=True, seed=seed)
    return {
        "baseline": baseline,
        "with_prediction": with_prediction,
        "n_entities": len(frames[0].xy_by_entity),
        "n_frames": len(frames),
    }


def _pool(per_episode: list[dict[str, Any]], key: str) -> dict[str, Any]:
    switches = sum(e[key]["n_switches"] for e in per_episode)
    object_hours = sum(e[key]["n_entities"] * e[key]["duration_hours"] for e in per_episode)
    idtp = sum(e[key]["idtp"] for e in per_episode)
    idfp = sum(e[key]["idfp"] for e in per_episode)
    idfn = sum(e[key]["idfn"] for e in per_episode)
    # id_switch_rate() は「1個体・1時間あたりの切替回数」なので、複数エピソードを
    # プールする際は総切替数を総「個体×時間」で割る（各エピソードの rate を単純平均
    # すると短いエピソードと長いエピソードが等しい重みになってしまい、
    # poc_plan.md 6.2「200回相当」規模で見たい実質的な切替率と乖離する）。
    rate = switches / max(object_hours, 1e-6)
    return {
        "n_switches": switches,
        "id_switch_rate": rate,
        "idf1": idf1(idtp, idfp, idfn),
    }


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    """`config.episode_ids`（複数）が指定されていればそれら全エピソードで集計し
    （poc_plan.md 6.2「200回相当」の PoC 縮小版、本実行向け）、`with_prediction`/
    `baseline` それぞれの総切替数・object-hours・IDF1 の混同行列カウントをプールした
    上で切替率・IDF1 を算出する。従来通り `config.episode_id`（単数）のみが
    指定されている場合は単一エピソードで測定する（smoke 互換）。"""
    episode_ids = list(config.get("episode_ids") or [])
    if not episode_ids:
        episode_ids = [config.episode_id]

    per_episode = [
        _measure_single_episode(str(data_dir() / "sim" / config.set_name / episode_id), seed=seed)
        for episode_id in episode_ids
    ]

    baseline = _pool(per_episode, "baseline")
    with_prediction = _pool(per_episode, "with_prediction")

    return {
        "id_switch_rate": with_prediction["id_switch_rate"],
        "id_switch_rate_baseline": baseline["id_switch_rate"],
        "idf1": with_prediction["idf1"],
        "idf1_baseline": baseline["idf1"],
        "n_switches_with_prediction": with_prediction["n_switches"],
        "n_switches_baseline": baseline["n_switches"],
        "n_entities": per_episode[0]["n_entities"],
        "n_frames": sum(e["n_frames"] for e in per_episode),
        "n_episodes": len(episode_ids),
    }


__all__ = ["measure", "occlusion_entity_names"]
