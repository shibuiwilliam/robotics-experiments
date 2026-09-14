"""`wm.dataset.read_frames_with_retry` の再試行ロジックのユニットテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtwm.wm import dataset

pytestmark = pytest.mark.unit


class _FakeReader:
    def __init__(self, data: dict[int, object]) -> None:
        self._data = data
        self.closed = False

    def get_data(self, idx: int) -> object:
        return self._data[idx]

    def close(self) -> None:
        self.closed = True


def test_read_frames_with_retry_retries_whole_open_and_read_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffmpeg の遅延初期化は `get_reader()` ではなく最初の `get_data()` で起きるため、
    `get_reader()` 成功後の `get_data()` 失敗も再試行できなければならない（実際に
    EXP-04 本実行で `get_data()` 側の失敗を観測した、2026-09-14）。"""
    calls = {"n": 0}

    def _fake_get_reader(path: Path) -> _FakeReader:
        calls["n"] += 1
        if calls["n"] < 2:
            reader = _FakeReader({})

            def _fail_get_data(idx: int) -> object:
                raise OSError("Could not load meta information")

            reader.get_data = _fail_get_data  # type: ignore[method-assign]
            return reader
        return _FakeReader({0: "frame0", 1: "frame1"})

    monkeypatch.setattr(dataset.imageio, "get_reader", _fake_get_reader)
    frames = dataset.read_frames_with_retry(Path("dummy.mp4"), [0, 1], retries=3, delay_s=0.0)
    assert frames == ["frame0", "frame1"]
    assert calls["n"] == 2


def test_read_frames_with_retry_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _always_fail(path: Path) -> _FakeReader:
        reader = _FakeReader({})

        def _fail_get_data(idx: int) -> object:
            raise OSError("Could not load meta information")

        reader.get_data = _fail_get_data  # type: ignore[method-assign]
        return reader

    monkeypatch.setattr(dataset.imageio, "get_reader", _always_fail)
    with pytest.raises(OSError, match="Could not load meta information"):
        dataset.read_frames_with_retry(Path("dummy.mp4"), [0], retries=2, delay_s=0.0)


def test_read_frames_with_retry_closes_reader_each_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readers: list[_FakeReader] = []

    def _fake_get_reader(path: Path) -> _FakeReader:
        reader = _FakeReader({0: "ok"})
        readers.append(reader)
        return reader

    monkeypatch.setattr(dataset.imageio, "get_reader", _fake_get_reader)
    dataset.read_frames_with_retry(Path("dummy.mp4"), [0], retries=3, delay_s=0.0)
    assert len(readers) == 1
    assert readers[0].closed is True
