"""Tests for deterministic clock."""

from mws.core.clock import DeterministicClock


def test_clock_determinism() -> None:
    c1 = DeterministicClock(seed=0)
    c2 = DeterministicClock(seed=0)
    for _ in range(100):
        assert c1.tick() == c2.tick()


def test_clock_tick_advances() -> None:
    c = DeterministicClock(seed=0, start_epoch=0.0)
    c.set_dt(1.0)
    assert c.now == 0.0
    c.tick()
    assert c.now == 1.0
    c.tick(5)
    assert c.now == 6.0


def test_clock_reset() -> None:
    c = DeterministicClock(seed=0, start_epoch=100.0)
    c.tick(10)
    assert c.tick_count == 10
    c.reset()
    assert c.tick_count == 0
    assert c.now == 100.0
