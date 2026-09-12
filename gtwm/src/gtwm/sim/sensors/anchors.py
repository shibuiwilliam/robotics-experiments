"""アンカーイベント検出（純粋 Python、物理コールバックの外で実行する）。

`gtwm.sim.env.RawTrajectory`（10Hz の軌跡）を受け取り、RFID ゲート通過・スキャン・
秤・扉・PLC の各アンカーを検出して DataFrame で返す。真値時刻 `t_true` のみを扱い、
観測時刻 `t_obs` へのジッタ付与は `configs/realism` を読む書出側（着手順8）の責務。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from gtwm.sim.env import RawTrajectory

GATE_DEFS = {"gate:1": -4.0, "gate:2": 4.0}
DOCK_DEFS = {"dock:1": (-6.0, (-1.2, 1.2)), "dock:2": (6.0, (-1.2, 1.2))}
SCALE_POS = np.array([-3.0, -3.0])


@dataclass
class AnchorConfig:
    scan_radius_m: float = 1.0
    scan_min_duration_s: float = 1.0
    scan_prob: float = 0.9
    scale_min_duration_s: float = 2.0
    scale_radius_m: float = 0.35
    velocity_threshold_mps: float = 0.05
    door_radius_m: float = 0.6


def _velocity(positions: np.ndarray, dt: float) -> np.ndarray:
    vel = np.zeros_like(positions)
    vel[1:] = (positions[1:] - positions[:-1]) / dt
    return vel


def detect_gate_events(traj: RawTrajectory, entity_names: list[str]) -> list[dict]:
    """RFID ゲート通過：ゲート平面（x = 定数）を跨いだ瞬間を検出する。"""
    events: list[dict] = []
    dt = float(traj.times[1] - traj.times[0]) if len(traj.times) > 1 else 0.1
    for gate_name, gate_x in GATE_DEFS.items():
        for entity in entity_names:
            xs = traj.positions[entity][:, 0]
            sign = np.sign(xs - gate_x)
            crossings = np.where(np.diff(sign) != 0)[0]
            for i in crossings:
                frac = (
                    abs(sign[i]) / (abs(sign[i]) + abs(sign[i + 1]))
                    if (sign[i] != sign[i + 1])
                    else 0.5
                )
                t_true = float(traj.times[i] + frac * dt)
                direction = "outbound" if xs[i + 1] > xs[i] else "inbound"
                events.append(
                    {
                        "anchor_type": "rfid_gate",
                        "entity": entity,
                        "detail_gate": gate_name,
                        "detail_direction": direction,
                        "t_true": t_true,
                    }
                )
    return events


def detect_door_events(traj: RawTrajectory, agv_names: list[str]) -> list[dict]:
    """扉：AGV がドックのバウンディング領域を通過した区間の入場時刻を検出する。"""
    events: list[dict] = []
    for dock_name, (dock_x, y_range) in DOCK_DEFS.items():
        for agv in agv_names:
            pos = traj.positions[agv]
            inside = (
                (np.abs(pos[:, 0] - dock_x) < 1.0)
                & (pos[:, 1] > y_range[0])
                & (pos[:, 1] < y_range[1])
            )
            entries = np.where(np.diff(inside.astype(int)) == 1)[0] + 1
            for i in entries:
                events.append(
                    {
                        "anchor_type": "door",
                        "entity": agv,
                        "detail_gate": dock_name,
                        "detail_direction": "",
                        "t_true": float(traj.times[i]),
                    }
                )
    return events


def detect_scan_events(
    traj: RawTrajectory,
    worker_names: list[str],
    target_names: list[str],
    cfg: AnchorConfig,
    rng: np.random.Generator,
) -> list[dict]:
    """スキャン：作業者が対象から scan_radius_m 以内に scan_min_duration_s 以上滞在。"""
    events: list[dict] = []
    dt = float(traj.times[1] - traj.times[0]) if len(traj.times) > 1 else 0.1
    min_samples = max(1, int(round(cfg.scan_min_duration_s / dt)))
    for worker in worker_names:
        wpos = traj.positions[worker][:, :2]
        for target in target_names:
            tpos = traj.positions[target][:, :2]
            dist = np.linalg.norm(wpos - tpos, axis=1)
            close = dist < cfg.scan_radius_m
            run_start = None
            for i, is_close in enumerate(np.append(close, False)):
                if is_close and run_start is None:
                    run_start = i
                elif not is_close and run_start is not None:
                    run_len = i - run_start
                    if run_len >= min_samples and rng.random() < cfg.scan_prob:
                        t_true = float(traj.times[run_start] + cfg.scan_min_duration_s)
                        events.append(
                            {
                                "anchor_type": "scan",
                                "entity": target,
                                "detail_gate": worker,
                                "detail_direction": "",
                                "t_true": t_true,
                            }
                        )
                    run_start = None
    return events


def detect_scale_events(
    traj: RawTrajectory, target_names: list[str], cfg: AnchorConfig
) -> list[dict]:
    """秤：対象が秤サイト上でほぼ静止した状態が scale_min_duration_s 以上続いた時刻。"""
    events: list[dict] = []
    dt = float(traj.times[1] - traj.times[0]) if len(traj.times) > 1 else 0.1
    min_samples = max(1, int(round(cfg.scale_min_duration_s / dt)))
    for target in target_names:
        pos = traj.positions[target][:, :2]
        vel = _velocity(traj.positions[target], dt)
        speed = np.linalg.norm(vel[:, :2], axis=1)
        on_scale = (np.linalg.norm(pos - SCALE_POS, axis=1) < cfg.scale_radius_m) & (
            speed < cfg.velocity_threshold_mps
        )
        run_start = None
        for i, flag in enumerate(np.append(on_scale, False)):
            if flag and run_start is None:
                run_start = i
            elif not flag and run_start is not None:
                run_len = i - run_start
                if run_len >= min_samples:
                    t_true = float(traj.times[run_start] + cfg.scale_min_duration_s)
                    events.append(
                        {
                            "anchor_type": "scale",
                            "entity": target,
                            "detail_gate": "scale:1",
                            "detail_direction": "",
                            "t_true": t_true,
                        }
                    )
                run_start = None
    return events


def detect_plc_events(conveyor_schedule: list[tuple[float, bool]]) -> list[dict]:
    """PLC：コンベア稼働状態の遷移時刻をそのままアンカーとする。"""
    return [
        {
            "anchor_type": "plc",
            "entity": "equipment:conveyor_1",
            "detail_gate": "equipment:conveyor_1",
            "detail_direction": "on" if state else "off",
            "t_true": float(t),
        }
        for t, state in conveyor_schedule
    ]


def detect_all_anchors(
    traj: RawTrajectory,
    pallet_names: list[str],
    case_names: list[str],
    agv_names: list[str],
    worker_names: list[str],
    conveyor_schedule: list[tuple[float, bool]],
    cfg: AnchorConfig | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    cfg = cfg or AnchorConfig()
    rng = np.random.default_rng(seed)
    targets = pallet_names + case_names

    events: list[dict] = []
    events += detect_gate_events(traj, targets)
    events += detect_door_events(traj, agv_names)
    events += detect_scan_events(traj, worker_names, targets, cfg, rng)
    events += detect_scale_events(traj, targets, cfg)
    events += detect_plc_events(conveyor_schedule)

    df = pd.DataFrame(
        events, columns=["anchor_type", "entity", "detail_gate", "detail_direction", "t_true"]
    )
    if df.empty:
        df = pd.DataFrame(
            columns=["anchor_type", "entity", "detail_gate", "detail_direction", "t_true", "t_obs"]
        )
        return df
    df = df.sort_values("t_true").reset_index(drop=True)
    df["t_obs"] = df["t_true"]  # smoke（realism 無効）では真値と観測値は等しい
    return df
