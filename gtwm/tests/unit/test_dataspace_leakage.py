import numpy as np
import pytest

from gtwm.dataspace.leakage import _downsample

pytestmark = pytest.mark.unit


def test_downsample_preserves_corner_pixels() -> None:
    """32x32 の単色フレームを 16x16 に縮小しても平均色が保たれることを確認する
    （最近傍法の縮小なので、単色入力なら出力も同じ色になるはず）。"""
    frame = np.full((3, 32, 32), 200, dtype=np.uint8)
    frame[0] = 10  # チャンネル0だけ別の値
    small = _downsample(frame, size=16)
    assert small.shape == (16, 16, 3)
    assert np.all(small[:, :, 0] == 10)
    assert np.all(small[:, :, 1] == 200)


def test_downsample_output_dtype_matches_input() -> None:
    frame = np.random.default_rng(0).integers(0, 255, size=(3, 8, 8)).astype(np.uint8)
    small = _downsample(frame, size=4)
    assert small.dtype == frame.dtype
    assert small.shape == (4, 4, 3)
