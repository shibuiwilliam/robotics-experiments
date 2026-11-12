"""同じ情報源の繰り返しの主張が信念を飽和させない（H-26、ADR-0027）。

履歴の再生・再起動・遅延到着では、観測 1 件に対して主張が数百件まとめて入る。主張を独立な証拠として
足し続けると、件数だけで信念が 1.0 に飽和し、末尾の状態と食い違う。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.reasoning.test_ingest import (  # pyright: ignore[reportPrivateUsage]
    MES,
    PUMP_A,
    _claim_event,  # pyright: ignore[reportPrivateUsage]
    _event,  # pyright: ignore[reportPrivateUsage]
    _ingest,  # pyright: ignore[reportPrivateUsage]
)

from sbp.reasoning.ingest import BeliefIngest, SkipReason

T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)


def _posterior(ingest: BeliefIngest, at: datetime) -> float:
    flt = ingest.filter_for(entity=PUMP_A, fluent="Running")
    assert flt.posterior is not None, at
    return flt.posterior


def test_many_repeated_claims_are_bounded_by_one_claim() -> None:
    once = _ingest()
    once.handle(_event(at=T0, p=0.5))
    once.handle(_claim_event(at=T0 + timedelta(minutes=1), p=0.79))
    many = _ingest()
    many.handle(_event(at=T0, p=0.5))
    for minute in range(1, 301):
        many.handle(_claim_event(at=T0 + timedelta(minutes=minute), p=0.79))
    assert _posterior(many, T0) == pytest.approx(_posterior(once, T0))


def test_the_latest_claim_replaces_the_earlier_ones_from_the_same_source() -> None:
    ingest = _ingest()
    ingest.handle(_event(at=T0, p=0.5))
    for minute in range(1, 201):
        ingest.handle(_claim_event(at=T0 + timedelta(minutes=minute), p=0.79))
    for minute in range(201, 204):
        ingest.handle(_claim_event(at=T0 + timedelta(minutes=minute), p=0.21))
    assert _posterior(ingest, T0) < 0.3, "末尾の主張が「停止」なら、件数で負けない"


def test_an_observation_between_claims_is_not_swamped() -> None:
    ingest = _ingest()
    ingest.handle(_event(at=T0, p=0.5))
    for minute in range(1, 101):
        ingest.handle(_claim_event(at=T0 + timedelta(minutes=minute), p=0.79))
    ingest.handle(_event(at=T0 + timedelta(minutes=101), p=0.02))
    assert _posterior(ingest, T0) < 0.5


def test_undo_restores_the_replaced_claim() -> None:
    ingest = _ingest()
    ingest.handle(_event(at=T0, p=0.5))
    ingest.handle(_claim_event(at=T0 + timedelta(minutes=1), p=0.79))
    before = _posterior(ingest, T0)
    outcome = ingest.handle(_claim_event(at=T0 + timedelta(minutes=2), p=0.21))
    assert outcome.undo is not None
    outcome.undo()
    assert _posterior(ingest, T0) == before
    again = ingest.handle(_claim_event(at=T0 + timedelta(minutes=2), p=0.21))
    assert again.update is not None
    assert again.update.posterior == pytest.approx(
        outcome.update.posterior if outcome.update else 0
    )


def test_distinct_sources_each_count_once() -> None:
    ingest = _ingest()
    ingest.handle(_event(at=T0, p=0.5))
    ingest.handle(_claim_event(at=T0 + timedelta(minutes=1), p=0.79))
    one = _posterior(ingest, T0)
    other = _claim_event(at=T0 + timedelta(minutes=2), p=0.79)
    evidence = other.evidence[0].model_copy(update={"source": MES + "-other"})
    ingest.handle(other.model_copy(update={"evidence": (evidence,)}))
    assert _posterior(ingest, T0) > one


def test_alternating_observations_and_claims_do_not_accumulate() -> None:
    ingest = _ingest()
    ingest.handle(_event(at=T0, p=0.5))
    for minute in range(1, 200):
        ingest.handle(_event(at=T0 + timedelta(minutes=2 * minute), p=0.5))
        ingest.handle(_claim_event(at=T0 + timedelta(minutes=2 * minute + 1), p=0.9))
    assert _posterior(ingest, T0) < 0.99


def test_a_stale_claim_does_not_overwrite_a_newer_one() -> None:
    ingest = _ingest()
    ingest.handle(_event(at=T0, p=0.5))
    ingest.handle(_claim_event(at=T0 + timedelta(minutes=10), p=0.79))
    ingest.handle(_claim_event(at=T0 + timedelta(minutes=20), p=0.21))
    before = _posterior(ingest, T0)
    stale = ingest.handle(_claim_event(at=T0 + timedelta(minutes=12), p=0.79))
    assert stale.skipped == SkipReason.STALE_CLAIM
    assert _posterior(ingest, T0) == before


def test_pruning_drops_the_claim_terms() -> None:
    ingest = _ingest()
    ingest.handle(_claim_event(at=T0))
    ingest.prune(before=T0 + timedelta(hours=1))
    assert not ingest._claim_terms  # pyright: ignore[reportPrivateUsage]
