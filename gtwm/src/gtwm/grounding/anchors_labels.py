"""アンカー時刻の真値ラベルを抽出する（意味プローブ α のアンカー自己教師：world_model.md）。

`events.parquet`（session 02 の WMS モックが吐くアンカー由来「記録」イベント）の
(entity, t_true) から、`poses.parquet` の対応時刻の真値（ゾーン・床面座標・型）を引く。
学習には全時刻の真値ではなく、このアンカー時刻ぶんだけを使う（world_model.md「損失」節：
「シミュレーションでは真値の一部だけを使い、全真値で学習しない」）。F1/ECE の評価
（`eval_probes.py`）は逆に全時刻の真値を使ってよい（世界モデル契約のテストであって
学習ラベルではないため）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from gtwm.sim.env import ZONE_NAMES
from gtwm.wm.dataset import EpisodeMeta
from gtwm.wm.slots import SLOT_TYPES

_TYPE_PREFIX = {"pallet": "pallet", "case": "case", "agv": "agv", "worker": "worker"}


def entity_type_index(entity: str) -> int:
    prefix = entity.split(":", 1)[0]
    type_name = _TYPE_PREFIX.get(prefix)
    if type_name is None:
        raise ValueError(f"未知の個体名プレフィックス: {entity}")
    return SLOT_TYPES.index(type_name)


@dataclass
class AnchorSample:
    episode: EpisodeMeta
    frame_idx: int
    entity: str
    entity_gt_id: str
    zone_idx: int
    xy: tuple[float, float]
    type_idx: int


def build_anchor_samples(ep: EpisodeMeta) -> list[AnchorSample]:
    """1エピソードぶんのアンカー教師サンプルを構築する。

    `events.parquet` の `event_type == "anchor"` 行（アンカー検出そのもの。CBV の
    `record` 行は WMS 側の記録であり教師には使わない）から (entity, t_true, zone) を
    取り、床面座標だけ `poses.parquet` の同時刻から補う（events.parquet はゾーンは
    持つが x/y は持たないため）。ゾーンが無い行（PLC 等、対象個体を持たないアンカー）は除外する。
    """
    events_path = ep.episode_dir / "events.parquet"
    poses_path = ep.episode_dir / "poses.parquet"
    events = pd.read_parquet(events_path)
    poses = pd.read_parquet(poses_path)

    anchors = events[events["event_type"] == "anchor"].dropna(subset=["zone"])

    samples: list[AnchorSample] = []
    seen_frames: set[tuple[str, int]] = set()
    for _, ev in anchors.iterrows():
        entity = str(ev["entity"])
        if entity.split(":", 1)[0] not in _TYPE_PREFIX:
            continue  # equipment（コンベア等）は型4クラスの対象外
        zone_name = str(ev["zone"])
        if zone_name not in ZONE_NAMES:
            continue
        t_true = float(ev["t_true"])
        frame_idx = int(round(t_true * ep.log_hz))
        if frame_idx < 0 or frame_idx >= ep.n_frames:
            continue
        key = (entity, frame_idx)
        if key in seen_frames:
            continue
        seen_frames.add(key)

        sub = poses[poses["entity"] == entity]
        if sub.empty:
            continue
        t_frame = frame_idx / ep.log_hz
        row = sub.iloc[(sub["t"] - t_frame).abs().to_numpy().argmin()]
        samples.append(
            AnchorSample(
                episode=ep,
                frame_idx=frame_idx,
                entity=entity,
                entity_gt_id=str(ev["entity_gt_id"]),
                zone_idx=ZONE_NAMES.index(zone_name),
                xy=(float(row["x"]), float(row["y"])),
                type_idx=entity_type_index(entity),
            )
        )
    return samples


def chronological_sample_split(
    samples: list[AnchorSample], eval_fraction: float = 0.3
) -> tuple[list[AnchorSample], list[AnchorSample]]:
    """時系列順（フレーム番号順）に後半を評価に回す。同一 (episode,frame) は片方にのみ入る。"""
    ordered = sorted(samples, key=lambda s: (s.episode.episode_id, s.frame_idx))
    if len(ordered) < 4:
        return ordered, []
    n_eval = max(1, int(round(len(ordered) * eval_fraction)))
    return ordered[:-n_eval], ordered[-n_eval:]


def episode_meta_from_dir(episode_dir_name: str, set_name: str) -> EpisodeMeta:
    from gtwm.utils.paths import data_dir

    ep_dir = data_dir() / "sim" / set_name / episode_dir_name
    meta = json.loads((ep_dir / "meta.json").read_text())
    n_frames = int(round(meta["duration_s"] * meta["log_hz"]))
    return EpisodeMeta(
        episode_dir=ep_dir,
        episode_id=meta["episode_id"],
        cameras=meta["cameras"],
        n_frames=n_frames,
        log_hz=float(meta["log_hz"]),
    )


__all__ = [
    "AnchorSample",
    "build_anchor_samples",
    "chronological_sample_split",
    "entity_type_index",
    "episode_meta_from_dir",
]
