import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtwm.sim.generate import generate_episode

pytestmark = pytest.mark.sim

DURATION_S = 2.0
LOG_HZ = 10
N_LOG = int(DURATION_S * LOG_HZ)


def test_generate_episode_writes_all_files(tmp_path: Path) -> None:
    out = generate_episode("shapetest", 0, DURATION_S, 5, output_root=tmp_path)
    files = {p.name for p in out.iterdir()}

    for cam in ("cam_1.mp4", "cam_2.mp4", "cam_3.mp4", "cam_4.mp4"):
        assert cam in files
    for name in (
        "masks.npz",
        "depth.npz",
        "poses.parquet",
        "occlusion.npz",
        "events.parquet",
        "meta.json",
    ):
        assert name in files

    masks = np.load(out / "masks.npz")
    assert masks["1"].shape == (N_LOG, 128, 128)
    assert masks["1"].dtype == np.uint16

    depth = np.load(out / "depth.npz")
    assert depth["1"].shape == (N_LOG, 128, 128)

    occlusion = np.load(out / "occlusion.npz")
    assert occlusion["1"].shape[0] == N_LOG
    assert occlusion["1"].ndim == 2

    poses = pd.read_parquet(out / "poses.parquet")
    assert set(poses.columns) >= {"t", "entity", "x", "y", "z", "zone"}
    assert poses["t"].nunique() == N_LOG

    meta = json.loads((out / "meta.json").read_text())
    assert meta["seed"] == 5
    assert meta["duration_s"] == DURATION_S


def test_events_have_true_and_observed_time_columns_equal_without_realism(tmp_path: Path) -> None:
    out = generate_episode("shapetest2", 0, DURATION_S, 6, output_root=tmp_path)
    events = pd.read_parquet(out / "events.parquet")

    assert "t_true" in events.columns
    assert "t_obs" in events.columns
    if len(events) > 0:
        assert (events["t_true"] == events["t_obs"]).all()
