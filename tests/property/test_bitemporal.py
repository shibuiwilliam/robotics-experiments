"""二時制の性質テスト（hypothesis）。CLAUDE.md テスト方針で必須の 1 つ。

検証する性質:

- **as-of の単調性**: 文が見えるのは ``[tx_from, tx_to)`` の内側だけ。
- **訂正の非破壊性**: 撤回しても文は消えない（``include_closed`` で必ず見つかる）。
- **tx 区間の無重複**: 同じ主張の訂正系列で、記録時間の区間が重ならない。
- **valid-at と as-of の独立性**: 2 つの時間軸は互いに干渉しない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.constants import PROV_BUNDLE, PUMP, SITE

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.modality import Modality
from sbp.knowledge import AssertionStore, EmbeddedGraphStore

_EPOCH = datetime(2026, 9, 12, tzinfo=UTC)
VERSION = vocab.ONTOLOGY_VERSION

# 秒単位のオフセット。範囲を狭く取って hypothesis の探索を実用的に保つ。
offsets = st.integers(min_value=0, max_value=86_400)
durations = st.integers(min_value=1, max_value=86_400)
probabilities = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

_SLOW = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _at(seconds: int) -> datetime:
    return _EPOCH + timedelta(seconds=seconds)


def _fresh_store() -> AssertionStore:
    return AssertionStore(EmbeddedGraphStore(), site_id=SITE)


def _assertion(
    *,
    tx_from: datetime,
    valid_from: datetime,
    valid_to: datetime | None = None,
    p: float = 0.9,
) -> Assertion:
    return Assertion(
        subject=PUMP,
        predicate=vocab.ent.Running,
        object=True,
        valid_from=valid_from,
        valid_to=valid_to,
        tx_from=tx_from,
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=p),
        provenance=PROV_BUNDLE,
        ontology_version=VERSION,
    )


@given(tx_offset=offsets, tx_len=durations, probe_offset=offsets)
@_SLOW
def test_as_of_visibility_is_exactly_the_tx_interval(
    tx_offset: int, tx_len: int, probe_offset: int
) -> None:
    """文が見えるのは記録時間の区間の内側だけ（右開）。"""
    store = _fresh_store()
    tx_from = _at(tx_offset)
    tx_to = _at(tx_offset + tx_len)
    a = _assertion(tx_from=tx_from, valid_from=tx_from)
    store.assert_(a)
    store.retract(a.assertion_id, tx_to)

    probe = _at(probe_offset)
    visible = bool(store.query(as_of=probe))
    expected = tx_from <= probe < tx_to
    assert visible == expected, (
        f"as_of={probe.isoformat()} tx=[{tx_from.isoformat()}, {tx_to.isoformat()}) "
        f"visible={visible} expected={expected}"
    )


@given(tx_offset=offsets, tx_len=durations)
@_SLOW
def test_retraction_never_destroys_the_statement(tx_offset: int, tx_len: int) -> None:
    """不変条件 2: 撤回しても文は残る。"""
    store = _fresh_store()
    a = _assertion(tx_from=_at(tx_offset), valid_from=_at(tx_offset))
    store.assert_(a)
    store.retract(a.assertion_id, _at(tx_offset + tx_len))

    all_rows = store.query(include_closed=True)
    assert len(all_rows) == 1
    assert all_rows[0].assertion_id == a.assertion_id
    assert all_rows[0].tx_to == _at(tx_offset + tx_len)


@given(start=offsets, step=durations, p1=probabilities, p2=probabilities)
@_SLOW
def test_correction_chain_has_non_overlapping_tx_intervals(
    start: int, step: int, p1: float, p2: float
) -> None:
    """訂正系列の記録時間区間は重ならない（どの時点にも現在の信念は 1 つ）。"""
    store = _fresh_store()
    t0, t1 = _at(start), _at(start + step)
    first = _assertion(tx_from=t0, valid_from=t0, p=p1)
    store.assert_(first)
    store.retract(first.assertion_id, t1)
    second = first.supersede(belief=BooleanBelief(p=p2), at=t1, provenance=PROV_BUNDLE)
    store.assert_(second)

    rows = store.query(include_closed=True)
    assert len(rows) == 2
    # どの時点を問うても現在の信念は最大 1 つ
    for probe in (t0, _at(start + step // 2), t1, _at(start + step * 2)):
        current = store.query(as_of=probe)
        assert len(current) <= 1, f"as_of={probe.isoformat()} に複数の現在信念がある"


@given(valid_offset=offsets, valid_len=durations, probe=offsets)
@_SLOW
def test_valid_at_visibility_is_exactly_the_valid_interval(
    valid_offset: int, valid_len: int, probe: int
) -> None:
    """有効時間の判定も右開区間（不変条件 8）。"""
    store = _fresh_store()
    valid_from = _at(valid_offset)
    valid_to = _at(valid_offset + valid_len)
    store.assert_(_assertion(tx_from=_EPOCH, valid_from=valid_from, valid_to=valid_to))

    visible = bool(store.query(valid_at=_at(probe)))
    expected = valid_from <= _at(probe) < valid_to
    assert visible == expected


@given(p=probabilities)
@_SLOW
def test_confidence_survives_round_trip(p: float) -> None:
    """確信度がグラフを経由しても保たれる（較正済みの値がそのまま返る。不変条件 7）。"""
    store = _fresh_store()
    store.assert_(_assertion(tx_from=_EPOCH, valid_from=_EPOCH, p=p))
    rows = store.query()
    assert len(rows) == 1
    assert abs(rows[0].confidence - p) < 1e-9


@given(count=st.integers(min_value=1, max_value=6))
@_SLOW
def test_repeated_assert_of_distinct_statements_accumulates(count: int) -> None:
    """別の文は独立に積まれる（追記専用）。"""
    store = _fresh_store()
    for i in range(count):
        store.assert_(_assertion(tx_from=_at(i * 60), valid_from=_at(i * 60)))
    assert len(store.query(include_closed=True)) == count
