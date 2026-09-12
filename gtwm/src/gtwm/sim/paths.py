"""作業者のスプライン経路と AGV のウェイポイント追従。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _catmull_rom_point(
    p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, u: float
) -> np.ndarray:
    u2 = u * u
    u3 = u2 * u
    return 0.5 * (
        (2 * p1)
        + (-p0 + p2) * u
        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2
        + (-p0 + 3 * p1 - 3 * p2 + p3) * u3
    )


@dataclass
class LoopSpline:
    """閉ループの Catmull-Rom スプライン。`position(t)` は t を [0, n) で周期化して返す。"""

    waypoints: np.ndarray  # [N,2]

    def position(self, t: float) -> np.ndarray:
        n = len(self.waypoints)
        t = t % n
        i = int(np.floor(t))
        u = t - i
        p0 = self.waypoints[(i - 1) % n]
        p1 = self.waypoints[i % n]
        p2 = self.waypoints[(i + 1) % n]
        p3 = self.waypoints[(i + 2) % n]
        return _catmull_rom_point(p0, p1, p2, p3, u)


@dataclass
class WorkerPath:
    """作業者1人分のスプライン経路。`speed` は1周にかける仮想時間の逆数（大きいほど速い）。"""

    spline: LoopSpline
    speed: float
    phase: float = 0.0

    def position_at(self, t_s: float) -> np.ndarray:
        return self.spline.position(t_s * self.speed + self.phase)


def make_worker_path(
    waypoints: list[tuple[float, float]], speed: float, phase: float, seed: int
) -> WorkerPath:
    rng = np.random.default_rng(seed)
    pts = np.array(waypoints, dtype=float)
    jitter = rng.normal(scale=0.05, size=pts.shape)
    return WorkerPath(spline=LoopSpline(waypoints=pts + jitter), speed=speed, phase=phase)


@dataclass
class AgvController:
    """ウェイポイント巡回のための単純な P 制御（速度・ヨー角）コントローラ。"""

    waypoints: np.ndarray  # [N,2]
    target_idx: int = 0
    arrive_radius: float = 0.35
    max_speed: float = 1.0
    max_yaw_rate: float = 2.0

    def command(self, pos_xy: np.ndarray, yaw: float) -> tuple[float, float, float]:
        target = self.waypoints[self.target_idx]
        delta = target - pos_xy
        dist = float(np.linalg.norm(delta))
        if dist < self.arrive_radius:
            self.target_idx = (self.target_idx + 1) % len(self.waypoints)
            target = self.waypoints[self.target_idx]
            delta = target - pos_xy
            dist = float(np.linalg.norm(delta))

        desired_yaw = float(np.arctan2(delta[1], delta[0]))
        yaw_err = math_wrap_to_pi(desired_yaw - yaw)
        forward = min(self.max_speed, dist)
        vx = forward * math_cos(desired_yaw)
        vy = forward * math_sin(desired_yaw)
        vyaw = float(np.clip(yaw_err * 2.0, -self.max_yaw_rate, self.max_yaw_rate))
        return (vx, vy, vyaw)


def math_wrap_to_pi(angle: float) -> float:
    return float((angle + np.pi) % (2 * np.pi) - np.pi)


def math_cos(angle: float) -> float:
    return float(np.cos(angle))


def math_sin(angle: float) -> float:
    return float(np.sin(angle))
