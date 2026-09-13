"""EXP-07（H6）用に追加した `WarehouseEnv` の介入パラメータの回帰テスト。

`active_workers`/`worker_speed_multiplier`/`worker_loop_overrides` は既定値では
従来と同じ挙動になり、指定すると作業者の軌跡が実際に変わることを検査する。
"""

from pathlib import Path

import pandas as pd
import pytest

from gtwm.sim.generate import generate_episode

pytestmark = pytest.mark.sim


def _worker1_positions(poses: pd.DataFrame) -> pd.DataFrame:
    return poses[poses["entity"] == "worker:1"].sort_values("t")


def test_default_params_match_baseline_exactly(tmp_path: Path) -> None:
    """新しいキーワード引数を渡さない呼び出しは、明示的にデフォルト値を渡した
    呼び出しと同一の軌跡になる（後方互換の回帰）。"""
    out1 = generate_episode("iv_default_a", 0, 3.0, 7, output_root=tmp_path / "a")
    out2 = generate_episode(
        "iv_default_b",
        0,
        3.0,
        7,
        output_root=tmp_path / "b",
        active_workers=None,
        worker_speed_multiplier=1.0,
        worker_loop_overrides=None,
    )
    p1 = pd.read_parquet(out1 / "poses.parquet")
    p2 = pd.read_parquet(out2 / "poses.parquet")
    pd.testing.assert_frame_equal(p1, p2)


def test_inactive_worker_stays_parked(tmp_path: Path) -> None:
    out = generate_episode(
        "iv_parked",
        0,
        3.0,
        7,
        output_root=tmp_path,
        active_workers=frozenset({"worker:2", "worker:3"}),
    )
    poses = pd.read_parquet(out / "poses.parquet")
    w1 = _worker1_positions(poses)
    # 静止している場合、全フレームで x/y が完全に一致する。
    assert w1["x"].nunique() == 1
    assert w1["y"].nunique() == 1


def test_active_worker_moves(tmp_path: Path) -> None:
    out = generate_episode(
        "iv_active",
        0,
        3.0,
        7,
        output_root=tmp_path,
        active_workers=frozenset({"worker:1", "worker:2", "worker:3"}),
    )
    poses = pd.read_parquet(out / "poses.parquet")
    w1 = _worker1_positions(poses)
    assert w1["x"].nunique() > 1


def test_worker_loop_override_changes_trajectory(tmp_path: Path) -> None:
    out_default = generate_episode("iv_loop_default", 0, 3.0, 7, output_root=tmp_path / "a")
    out_override = generate_episode(
        "iv_loop_override",
        0,
        3.0,
        7,
        output_root=tmp_path / "b",
        worker_loop_overrides={"worker:3": [(5.0, 5.0), (5.0, -5.0), (-5.0, -5.0), (-5.0, 5.0)]},
    )
    default_poses = pd.read_parquet(out_default / "poses.parquet")
    override_poses = pd.read_parquet(out_override / "poses.parquet")
    w3_default = default_poses[default_poses["entity"] == "worker:3"].sort_values("t")
    w3_override = override_poses[override_poses["entity"] == "worker:3"].sort_values("t")
    assert (
        not w3_default["x"].reset_index(drop=True).equals(w3_override["x"].reset_index(drop=True))
    )
