"""P0 scaffold smoke tests: config registry, deterministic clock, seeded RNG registry."""

from __future__ import annotations

import numpy as np

from config import load_registry
from core.clock import SimClock
from core.rng import RngRegistry


def test_registry_loads_model_ids() -> None:
    reg = load_registry()
    assert reg.model_id("er").startswith("gemini-robotics-er")
    assert reg.model_id("embedding") == "gemini-embedding-2"
    assert reg.require("models.embedding.dimensions") == 768


def test_registry_dotted_get_and_require() -> None:
    reg = load_registry()
    assert reg.get("does.not.exist", "fallback") == "fallback"
    assert reg.get("budget.daily_usd_cap") == 5.0
    try:
        reg.require("nope.nope")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("require() should raise on missing key")


def test_registry_vcr_mode_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    reg = load_registry()
    monkeypatch.setenv("MUSUBI_VCR_MODE", "record")
    assert reg.vcr_mode() == "record"
    monkeypatch.delenv("MUSUBI_VCR_MODE", raising=False)
    assert reg.vcr_mode() == "replay"


def test_clock_is_monotonic() -> None:
    clk = SimClock()
    assert clk.now() == 0.0
    assert clk.advance(1.5) == 1.5
    assert clk.now() == 1.5
    clk.set(3.0)
    assert clk.now() == 3.0
    for bad in (lambda: clk.advance(-1.0), lambda: clk.set(0.0)):
        try:
            bad()
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ValueError on non-monotonic time")


def test_rng_is_deterministic_and_named() -> None:
    a = RngRegistry(0)
    b = RngRegistry(0)
    # Same seed + same sub-stream name -> identical draws.
    xs_a = a.generator("sim_init").integers(0, 1_000_000, size=5)
    xs_b = b.generator("sim_init").integers(0, 1_000_000, size=5)
    assert np.array_equal(xs_a, xs_b)
    # Different sub-streams -> (almost surely) different draws.
    ys = a.generator("fault_injection").integers(0, 1_000_000, size=5)
    assert not np.array_equal(xs_a, ys)


def test_rng_generator_is_cached() -> None:
    reg = RngRegistry(42)
    assert reg.generator("x") is reg.generator("x")
