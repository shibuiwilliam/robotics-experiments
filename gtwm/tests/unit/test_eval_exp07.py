from pathlib import Path

import pandas as pd
import pytest

from gtwm.eval.experiments.exp07 import _worker_zone_occupancy

pytestmark = pytest.mark.unit


def _write_poses(path: Path, rows: list[dict]) -> Path:
    df = pd.DataFrame(rows)
    df.to_parquet(path)
    return path


def test_worker_zone_occupancy_counts_only_workers(tmp_path: Path) -> None:
    poses_path = _write_poses(
        tmp_path / "poses.parquet",
        [
            {"t": 10.0, "entity": "worker:1", "zone": "Pick"},
            {"t": 10.0, "entity": "worker:2", "zone": "Storage_A"},
            {"t": 10.0, "entity": "pallet:1", "zone": "Pick"},  # 非作業者は数えない
        ],
    )
    result = _worker_zone_occupancy(
        str(poses_path), "Pick", t_center=10.0, half_window=2.0, duration_s=20.0
    )
    assert result == 1.0


def test_worker_zone_occupancy_averages_over_window(tmp_path: Path) -> None:
    poses_path = _write_poses(
        tmp_path / "poses.parquet",
        [
            {"t": 9.0, "entity": "worker:1", "zone": "Pick"},
            {"t": 10.0, "entity": "worker:1", "zone": "Storage_B"},
        ],
    )
    result = _worker_zone_occupancy(
        str(poses_path), "Pick", t_center=9.5, half_window=1.0, duration_s=20.0
    )
    assert result == pytest.approx(0.5)


def test_worker_zone_occupancy_empty_window_returns_zero(tmp_path: Path) -> None:
    poses_path = _write_poses(
        tmp_path / "poses.parquet",
        [{"t": 1.0, "entity": "worker:1", "zone": "Pick"}],
    )
    result = _worker_zone_occupancy(
        str(poses_path), "Pick", t_center=15.0, half_window=1.0, duration_s=20.0
    )
    assert result == 0.0
