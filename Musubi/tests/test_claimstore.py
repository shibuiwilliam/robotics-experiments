"""P2 claimstore tests — append-only, bitemporal replay, confidence decay."""

from __future__ import annotations

import pytest

from core.claimstore import ClaimStore
from core.claimstore.store import AppendOnlyViolation
from core.clock import SimClock
from ontology.generated.musubi_types import Claim


def _claim(iri: str, subject: str, conf: float, kind: str = "position", **kw: object) -> Claim:
    return Claim(
        iri=iri,
        claimKind=kind,
        subject=subject,
        predicate="position",
        confidence=conf,
        realm="real",
        method="direct_measurement",
        source="msb:sensor/cam-0",
        **kw,
    )


def test_add_stamps_times_from_clock() -> None:
    clk = SimClock()
    clk.advance(10.0)
    store = ClaimStore(clk)
    c = store.add(_claim("msb:claim/1", "msb:e/1", 0.9))
    assert c.validTime == 10.0
    assert c.transactionTime == 10.0
    assert store.count() == 1


def test_append_only_rejects_duplicate_iri() -> None:
    store = ClaimStore(SimClock())
    store.add(_claim("msb:claim/1", "msb:e/1", 0.9))
    with pytest.raises(AppendOnlyViolation):
        store.add(_claim("msb:claim/1", "msb:e/1", 0.5))


def test_supersede_appends_and_deactivates_old() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    store.add(_claim("msb:claim/1", "msb:e/1", 0.9))
    clk.advance(5.0)
    store.supersede("msb:claim/1", _claim("msb:claim/2", "msb:e/1", 0.95))
    active = store.active(subject="msb:e/1")
    assert [c.iri for c in active] == ["msb:claim/2"]
    # old claim is still retrievable (never deleted)
    assert store.get("msb:claim/1") is not None
    assert store.count() == 2


def test_bitemporal_as_of_replay() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    store.add(_claim("msb:claim/1", "msb:e/1", 0.9))  # txn=0
    clk.advance(5.0)
    store.supersede("msb:claim/1", _claim("msb:claim/2", "msb:e/1", 0.95))  # txn=5
    # As of txn-time 0, only the first claim was known.
    early = store.as_of(0.0)
    assert [c.iri for c in early] == ["msb:claim/1"]
    later = store.as_of(5.0)
    assert {c.iri for c in later} == {"msb:claim/1", "msb:claim/2"}


def test_confidence_decays_by_half_life() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    # position half-life is 180s in registry; after 180s a 0.8 confidence -> ~0.4.
    c = store.add(_claim("msb:claim/1", "msb:e/1", 0.8, kind="position"))
    assert store.decayed_confidence(c, at_time=0.0) == pytest.approx(0.8)
    assert store.decayed_confidence(c, at_time=180.0) == pytest.approx(0.4, abs=1e-6)
    assert store.decayed_confidence(c, at_time=360.0) == pytest.approx(0.2, abs=1e-6)


def test_ownership_decays_slower_than_position() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    pos = store.add(_claim("msb:c/p", "msb:e/1", 1.0, kind="position"))
    own = store.add(_claim("msb:c/o", "msb:e/1", 1.0, kind="ownership"))
    at = 3600.0
    assert store.decayed_confidence(own, at) > store.decayed_confidence(pos, at)
