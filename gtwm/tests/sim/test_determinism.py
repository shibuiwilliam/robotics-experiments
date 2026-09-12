from pathlib import Path

import pandas as pd
import pytest

from gtwm.sim.generate import generate_episode

pytestmark = pytest.mark.sim


def test_same_seed_gives_identical_poses(tmp_path: Path) -> None:
    out1 = generate_episode("det", 0, 2.0, 123, output_root=tmp_path / "a")
    out2 = generate_episode("det", 0, 2.0, 123, output_root=tmp_path / "b")
    p1 = pd.read_parquet(out1 / "poses.parquet")
    p2 = pd.read_parquet(out2 / "poses.parquet")
    pd.testing.assert_frame_equal(p1, p2)
