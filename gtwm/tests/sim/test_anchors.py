import numpy as np
import pytest

from gtwm.sim.env import RawTrajectory
from gtwm.sim.sensors.anchors import (
    AnchorConfig,
    detect_gate_events,
    detect_scale_events,
    detect_scan_events,
)

pytestmark = pytest.mark.sim


def test_gate_event_detected_on_crossing() -> None:
    times = np.arange(0, 2.0, 0.1)
    n = len(times)
    pos = np.zeros((n, 3))
    pos[:, 0] = np.linspace(-5.0, -3.0, n)  # gate:1 は x=-4
    traj = RawTrajectory(times=times, positions={"pallet:1": pos})

    events = detect_gate_events(traj, ["pallet:1"])

    assert any(e["detail_gate"] == "gate:1" for e in events)


def test_gate_event_not_detected_without_crossing() -> None:
    times = np.arange(0, 2.0, 0.1)
    n = len(times)
    pos = np.zeros((n, 3))
    pos[:, 0] = -5.0
    traj = RawTrajectory(times=times, positions={"pallet:1": pos})

    events = detect_gate_events(traj, ["pallet:1"])

    assert events == []


def test_scan_event_requires_min_duration() -> None:
    times = np.arange(0, 3.0, 0.1)
    n = len(times)
    worker_pos = np.zeros((n, 3))
    target_pos = np.zeros((n, 3))
    target_pos[:, 0] = 0.1  # scan_radius_m=1.0 以内に終始滞在
    traj = RawTrajectory(times=times, positions={"worker:1": worker_pos, "case:1": target_pos})
    cfg = AnchorConfig(scan_prob=1.0)
    rng = np.random.default_rng(0)

    events = detect_scan_events(traj, ["worker:1"], ["case:1"], cfg, rng)

    assert len(events) >= 1
    assert events[0]["entity"] == "case:1"


def test_scan_event_absent_when_too_short() -> None:
    times = np.arange(0, 0.5, 0.1)  # 0.5s しかない（min_duration=1.0s）
    n = len(times)
    worker_pos = np.zeros((n, 3))
    target_pos = np.zeros((n, 3))
    target_pos[:, 0] = 0.1
    traj = RawTrajectory(times=times, positions={"worker:1": worker_pos, "case:1": target_pos})
    cfg = AnchorConfig(scan_prob=1.0)
    rng = np.random.default_rng(0)

    events = detect_scan_events(traj, ["worker:1"], ["case:1"], cfg, rng)

    assert events == []


def test_scale_event_requires_stationary() -> None:
    times = np.arange(0, 3.0, 0.1)
    n = len(times)
    pos = np.zeros((n, 3))
    pos[:, 0] = -3.0
    pos[:, 1] = -3.0
    traj = RawTrajectory(times=times, positions={"pallet:1": pos})
    cfg = AnchorConfig()

    events = detect_scale_events(traj, ["pallet:1"], cfg)

    assert len(events) >= 1
    assert events[0]["anchor_type"] == "scale"
