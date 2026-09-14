"""`wm.dataset.open_video_reader` の再試行ロジックのユニットテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtwm.wm import dataset

pytestmark = pytest.mark.unit


def test_open_video_reader_retries_on_oserror_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    sentinel = object()

    def _fake_get_reader(path: Path) -> object:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("Could not load meta information")
        return sentinel

    monkeypatch.setattr(dataset.imageio, "get_reader", _fake_get_reader)
    result = dataset.open_video_reader(Path("dummy.mp4"), retries=3, delay_s=0.0)
    assert result is sentinel
    assert calls["n"] == 3


def test_open_video_reader_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _always_fail(path: Path) -> object:
        raise OSError("Could not load meta information")

    monkeypatch.setattr(dataset.imageio, "get_reader", _always_fail)
    with pytest.raises(OSError, match="Could not load meta information"):
        dataset.open_video_reader(Path("dummy.mp4"), retries=2, delay_s=0.0)
