"""`gtwm sim gen` の実体：エピソード生成のオーケストレーション。

env（物理ステップ）・render（描画・遮蔽率）・sensors.anchors（アンカー検出）・
wms_mock（EPCIS 記録の導出）・writer（書出）を束ねる。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np
import pandas as pd

from gtwm.sim.drift import DriftConfig, apply_drift
from gtwm.sim.env import PHYSICS_DT, RawTrajectory, WarehouseEnv
from gtwm.sim.realism import RealismConfig, apply_observation_realism
from gtwm.sim.registry import ontology_id
from gtwm.sim.render import (
    camera_rotation_matrix,
    occlusion_rate,
    projected_bbox_area_px,
    render_camera,
)
from gtwm.sim.sensors.anchors import AnchorConfig, detect_all_anchors
from gtwm.sim.wms_mock import (
    InjectionConfig,
    apply_injections,
    derive_epcis_events,
    derive_scan_biz_step,
    generate_conveyor_schedule,
)
from gtwm.utils.paths import repo_root
from gtwm.utils.seed import seed_everything

LOG_HZ = 10
RESOLUTION = 128

EVENT_COLUMNS = [
    "event_type",
    "entity",
    "entity_gt_id",
    "anchor_type",
    "biz_step",
    "disposition",
    "zone",
    "detail",
    "t_true",
    "t_obs",
]


@dataclass
class GenConfig:
    set_name: str
    episodes: int
    duration_s: float
    seed: int
    realism: RealismConfig | None = None  # None＝P0/smoke（realism 無効、既定）
    drift: DriftConfig | None = None  # None＝ドリフト無し（既定）


def _unify_anchor_events(anchor_df: pd.DataFrame, zones: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    for _, row in anchor_df.iterrows():
        t_idx = int(round(row["t_true"] * LOG_HZ))
        zone_series = zones.get(row["entity"])
        zone = zone_series[min(t_idx, len(zone_series) - 1)] if zone_series else None
        rows.append(
            {
                "event_type": "anchor",
                "entity": row["entity"],
                "entity_gt_id": ontology_id(row["entity"]),
                "anchor_type": row["anchor_type"],
                "biz_step": None,
                "disposition": None,
                "zone": zone,
                "detail": json.dumps(
                    {"gate": row["detail_gate"], "direction": row["detail_direction"]}
                ),
                "t_true": row["t_true"],
                "t_obs": row["t_obs"],
            }
        )
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def _unify_record_events(record_df: pd.DataFrame) -> pd.DataFrame:
    if record_df.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    rows = []
    for _, row in record_df.iterrows():
        rows.append(
            {
                "event_type": "record",
                "entity": row["subject"],
                "entity_gt_id": row["subject_gt_id"],
                "anchor_type": None,
                "biz_step": row["biz_step"],
                "disposition": row["disposition"],
                "zone": None,
                "detail": json.dumps({}),
                "t_true": row["t_true"],
                "t_obs": row["t_obs"],
            }
        )
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def _scan_records(anchor_df: pd.DataFrame, zones: dict[str, list[str]]) -> pd.DataFrame:
    from gtwm.sim.wms_mock import CBV_BIZSTEP, CBV_DISPOSITION_ACTIVE

    rows = []
    scans = anchor_df[anchor_df["anchor_type"] == "scan"] if not anchor_df.empty else anchor_df
    for _, row in scans.iterrows():
        entity = row["entity"]
        zone_series = zones.get(entity)
        t_idx = min(int(round(row["t_true"] * LOG_HZ)), len(zone_series) - 1) if zone_series else 0
        zone = zone_series[t_idx] if zone_series else None
        biz = derive_scan_biz_step(zone) if zone else None
        if biz is None:
            continue
        rows.append(
            {
                "event_type": "record",
                "entity": entity,
                "entity_gt_id": ontology_id(entity),
                "anchor_type": None,
                "biz_step": CBV_BIZSTEP[biz],
                "disposition": CBV_DISPOSITION_ACTIVE,
                "zone": zone,
                "detail": json.dumps({}),
                "t_true": row["t_true"],
                "t_obs": row["t_obs"],
            }
        )
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def generate_episode(
    set_name: str,
    episode_idx: int,
    duration_s: float,
    seed: int,
    output_root: Path | None = None,
    realism: RealismConfig | None = None,
    drift: DriftConfig | None = None,
) -> Path:
    """1エピソードを生成し、出力ディレクトリを返す。"""
    seed_everything(seed)
    env = WarehouseEnv(seed=seed)

    n_log = int(round(duration_s * LOG_HZ))
    substeps = int(round(1.0 / LOG_HZ / PHYSICS_DT))
    times = np.arange(n_log) / LOG_HZ

    positions = {name: np.zeros((n_log, 3)) for name in env.tracked_names}
    yaws = {name: np.zeros(n_log) for name in env.agv_names}
    zones: dict[str, list[str]] = {name: [] for name in env.tracked_names}

    renderer = mujoco.Renderer(env.model, height=RESOLUTION, width=RESOLUTION)
    cams = list(env.cam_ids.keys())

    episode_id = f"ep_{episode_idx:04d}_seed{seed}"
    root = output_root or (repo_root() / "data" / "sim")
    output_dir = root / set_name / episode_id

    from gtwm.sim.writer import EpisodeWriter

    writer = EpisodeWriter(
        output_dir, cams, n_log, len(env.occlusion_names), RESOLUTION, RESOLUTION
    )
    conveyor_schedule = generate_conveyor_schedule(duration_s, seed)

    for t_idx in range(n_log):
        t_s = float(times[t_idx])
        env.advance_log_tick(t_s, substeps)
        snap = env.snapshot()
        for name, (pos, yaw, zone) in snap.items():
            positions[name][t_idx] = pos
            zones[name].append(zone)
            if name in env.agv_names:
                yaws[name][t_idx] = yaw

        for cam in cams:
            frame = render_camera(renderer, env.data, cam)
            cam_id = env.cam_ids[cam]
            cam_pos = np.array(env.data.cam_xpos[cam_id])
            cam_rot = camera_rotation_matrix(env.data, cam_id)
            fovy = float(env.model.cam_fovy[cam_id])
            occ_row = np.zeros(len(env.occlusion_names), dtype=np.float32)
            for oi, oname in enumerate(env.occlusion_names):
                gid = env.geom_ids[oname]
                center = np.array(env.data.geom_xpos[gid])
                half = np.array(env.model.geom_size[gid])
                area = projected_bbox_area_px(
                    cam_pos, cam_rot, fovy, RESOLUTION, RESOLUTION, center, half
                )
                occ_row[oi] = occlusion_rate(frame.seg_id, frame.seg_type, gid, area)
            writer.add_frame(cam, t_idx, frame, occ_row)

    traj = RawTrajectory(times=times, positions=positions, yaws=yaws, zones=zones)
    anchor_cfg = AnchorConfig()
    anchor_df = detect_all_anchors(
        traj,
        env.pallet_names,
        env.case_names,
        env.agv_names,
        env.worker_names,
        conveyor_schedule,
        cfg=anchor_cfg,
        seed=seed,
    )

    anchor_events = _unify_anchor_events(anchor_df, zones)
    gate_scale_records = _unify_record_events(derive_epcis_events(anchor_df, ontology_id))
    scan_records = _scan_records(anchor_df, zones)
    events_df = pd.concat([anchor_events, gate_scale_records, scan_records], ignore_index=True)
    events_df = events_df.sort_values("t_true").reset_index(drop=True)

    if drift is not None:
        events_df = apply_drift(events_df, drift)

    # 一般ノイズ（ジッタ・欠落・遅延・誤登録）は realism=None（P0/smoke）では no-op。
    # 実験 seed をそのまま使う（センサ雑さは狙った異常ではなく無差別ノイズのため、
    # injection_seed のような盲検分離は不要）。
    if realism is not None:
        events_df = apply_observation_realism(events_df, realism, seed=seed)

    injection_cfg = realism.injections if realism is not None else InjectionConfig()
    events_df, injection_ledger = apply_injections(
        events_df, injection_cfg, injection_seed=seed + 1_000_000
    )
    if not injection_ledger.empty:
        injection_ledger = injection_ledger.assign(episode_id=episode_id)
        ledger_dir = repo_root() / "data" / "injections"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        injection_ledger.to_parquet(ledger_dir / f"{episode_id}.parquet", index=False)

    poses_rows = []
    for name in env.tracked_names:
        for t_idx in range(n_log):
            pos = positions[name][t_idx]
            poses_rows.append(
                {
                    "t": float(times[t_idx]),
                    "entity": name,
                    "entity_gt_id": ontology_id(name),
                    "x": float(pos[0]),
                    "y": float(pos[1]),
                    "z": float(pos[2]),
                    "yaw": float(yaws[name][t_idx]) if name in yaws else 0.0,
                    "zone": zones[name][t_idx],
                }
            )
    poses_df = pd.DataFrame(poses_rows)

    meta = {
        "episode_id": episode_id,
        "set_name": set_name,
        "seed": seed,
        "duration_s": duration_s,
        "log_hz": LOG_HZ,
        "physics_dt": PHYSICS_DT,
        "resolution": [RESOLUTION, RESOLUTION],
        "cameras": cams,
        "occlusion_entities": env.occlusion_names,
        "masks_convention": "geom_id + 1 (uint16), 0 = background",
        "registry_version": "v1",
        "n_pallets": len(env.pallet_names),
        "n_cases": len(env.case_names),
        "n_agvs": len(env.agv_names),
        "n_workers": len(env.worker_names),
    }
    writer.close_and_write(poses_df, events_df, meta)
    return output_dir


def generate_set(cfg: GenConfig, output_root: Path | None = None) -> list[Path]:
    dirs = []
    for i in range(cfg.episodes):
        dirs.append(
            generate_episode(
                cfg.set_name,
                i,
                cfg.duration_s,
                cfg.seed + i,
                output_root=output_root,
                realism=cfg.realism,
                drift=cfg.drift,
            )
        )
    return dirs
