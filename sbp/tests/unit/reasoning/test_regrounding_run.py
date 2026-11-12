"""再接地の実行（M9-01、ADR-0031）。

重点:

1. **同じプローブで再生すると差分が出ない** — ライブと同じ区切りで再生できている証拠。
2. **dry-run は書かない。** ``apply`` のときだけ旧文を閉じ新文を足す。
3. **旧文は消さない** — ``as_of`` で旧信念が、新しい ``tx`` で新信念が引ける。
4. **再実行しても同じ** — 変わらない文は触らない。
5. **belief-worker 以外の来歴の文は閉じない。**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pytest
from services.belief_worker import default_ingest

from sbp.bridge.lifting import load_event_calculus
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import Settings
from sbp.core.errors import RegroundingError
from sbp.core.modality import Modality
from sbp.core.time import Interval
from sbp.knowledge import (
    AssertionStore,
    EmbeddedGraphStore,
    RegroundingTrigger,
    plan_regrounding,
)
from sbp.reasoning.regrounding_run import reground, replay_entity

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from sbp.knowledge.regrounding import RegroundingPlan

PUMP = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = str(vocab.ent.Running)
T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)
STEP = timedelta(seconds=10)
N = 60
WM = "https://sbp.example/wm/koopman/1.0.0"
PROBE_IDS = {"Running": "https://sbp.example/probe/Running/2.0.0"}
RULES = load_event_calculus("""
initiates(start_cmd, running).
terminates(stop_cmd, running).
initiates(fault_evt, faulted).
terminates(clear_evt, faulted).
""")
SETTINGS = Settings()


def _series() -> tuple[list[datetime], NDArray[np.float64]]:
    times = [T0 + STEP * i for i in range(N)]
    return times, np.arange(N, dtype=np.float64).reshape(N, 1)


def _probe(switch: int):  # type: ignore[no-untyped-def]
    """潜在の第 0 成分が ``switch`` 未満なら稼働、以上なら停止と言うプローブ。"""

    def run(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
        return {"Running": np.where(latents[:, 0] < switch, 0.95, 0.05)}

    return run


def _store() -> AssertionStore:
    return AssertionStore(EmbeddedGraphStore(), site_id="siteA")


def _plan(*, to_version: str = "probe/2.0.0") -> RegroundingPlan:
    return plan_regrounding(
        trigger=RegroundingTrigger.PROBE_VERSION,
        reason="Running プローブを v2 に昇格（ECE が改善）",
        entities=[PUMP],
        fluents=[RUNNING],
        interval=Interval(T0, T0 + STEP * (N + 1)),
        from_version="probe/1.0.0",
        to_version=to_version,
    )


def _live(store: AssertionStore, switch: int, *, tx: datetime) -> list[Assertion]:
    """ライブ（belief-worker）が書いたはずの信念を、旧版のプローブで作って書く。"""
    outcomes = replay_entity(
        entity=PUMP,
        series=_series(),
        probes=_probe(switch),
        probe_ids=PROBE_IDS,
        wm_version=WM,
        rules=RULES,
        settings=SETTINGS,
        claims=[],
        ingest=default_ingest(SETTINGS),
    )
    written: list[Assertion] = []
    for outcome in outcomes:
        assert outcome.assertion is not None
        old = outcome.assertion.model_copy(update={"tx_from": tx})
        store.assert_(old)
        written.append(old)
    return written


def _run(store: AssertionStore, switch: int, *, apply: bool, at: datetime | None = None):  # type: ignore[no-untyped-def]
    return reground(
        _plan(),
        store=store,
        series_by_entity={PUMP: _series()},
        probes=_probe(switch),
        probe_ids=PROBE_IDS,
        wm_version=WM,
        rules=RULES,
        settings=SETTINGS,
        claims=[],
        ingest_factory=lambda: default_ingest(SETTINGS),
        apply=apply,
        at=at,
    )


OLD_TX = T0 - timedelta(hours=1)
NOW = T0 + timedelta(hours=1)


class TestReplayFidelity:
    def test_the_same_probe_gives_no_diff(self) -> None:
        store = _store()
        live = _live(store, 30, tx=OLD_TX)
        assert live, "前提: ライブの信念がある"
        outcome = _run(store, 30, apply=False, at=NOW)
        assert not outcome.added
        assert not outcome.closed
        assert not outcome.report.entries


class TestDryRunAndApply:
    def test_a_dry_run_writes_nothing(self) -> None:
        store = _store()
        _live(store, 30, tx=OLD_TX)
        before = store.query(subject=PUMP, predicate=RUNNING, include_closed=True)
        outcome = _run(store, 45, apply=False, at=NOW)
        assert outcome.added
        assert not outcome.applied
        assert outcome.report.closed_assertions == 0
        assert store.query(subject=PUMP, predicate=RUNNING, include_closed=True) == before

    def test_apply_closes_old_statements_and_adds_new_ones_without_deleting(self) -> None:
        store = _store()
        live = _live(store, 30, tx=OLD_TX)
        outcome = _run(store, 45, apply=True, at=NOW)
        assert outcome.applied
        assert outcome.closed
        rows = store.query(subject=PUMP, predicate=RUNNING, include_closed=True)
        assert len(rows) >= len(live), "消さない（不変条件 2）"
        closed_ids = {row.assertion_id for row in outcome.closed}
        for row in rows:
            if row.assertion_id in closed_ids:
                assert row.tx_to == NOW

    def test_the_old_belief_is_reconstructible_with_as_of(self) -> None:
        """旧版の判断は ``as_of`` で引け、新版は新しい ``tx`` で載る（valid_from は過去のまま）。"""
        store = _store()
        live = _live(store, 30, tx=OLD_TX)
        outcome = _run(store, 45, apply=True, at=NOW)
        gone = outcome.closed[0]
        new = outcome.added[0]

        def at(valid_at: datetime, as_of: datetime) -> list:  # type: ignore[type-arg]
            return store.query(subject=PUMP, predicate=RUNNING, as_of=as_of, valid_at=valid_at)

        before, after = NOW - timedelta(seconds=1), NOW + timedelta(seconds=1)
        assert at(gone.valid_from, before), "再接地の前の信念が失われた"
        assert not at(gone.valid_from, after), "新版では消えた文は閉じられている"
        # 信念の文は valid_to を持たない（開いている）ので、新文の時刻でも旧版の文が引ける
        assert all(row.tx_from == OLD_TX for row in at(new.valid_from, before))
        assert [row.tx_from for row in at(new.valid_from, after)] == [NOW]
        assert new.valid_from < NOW, "valid_from は過去のまま"
        assert "regrounding" in new.provenance
        assert live

    def test_running_again_changes_nothing(self) -> None:
        store = _store()
        _live(store, 30, tx=OLD_TX)
        _run(store, 45, apply=True, at=NOW)
        again = _run(store, 45, apply=True, at=NOW + timedelta(minutes=1))
        assert not again.added
        assert not again.closed

    def test_a_crash_between_assert_and_retract_is_repaired_by_rerunning(self) -> None:
        store = _store()
        _live(store, 30, tx=OLD_TX)
        first = _run(store, 45, apply=False, at=NOW)
        for assertion in first.added:  # 足した直後に落ちた状態（旧文はまだ開いている）
            store.assert_(assertion)
        again = _run(store, 45, apply=True, at=NOW + timedelta(minutes=1))
        assert not again.added
        assert again.closed, "重複した旧文を閉じる"
        open_rows = store.query(subject=PUMP, predicate=RUNNING)
        assert len({row.valid_from for row in open_rows}) == len(open_rows)

    def test_statements_from_other_provenance_are_left_alone(self) -> None:
        store = _store()
        _live(store, 30, tx=OLD_TX)
        human = Assertion(
            subject=PUMP,
            predicate=RUNNING,
            object=True,
            valid_from=T0 + STEP * 5,
            tx_from=OLD_TX,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=0.5),
            provenance="urn:sbp:prov:human-correction/1",
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        store.assert_(human)
        _run(store, 45, apply=True, at=NOW)
        still_open = {row.assertion_id for row in store.query(subject=PUMP, predicate=RUNNING)}
        assert human.assertion_id in still_open


class TestGuards:
    def test_entities_without_latents_are_refused(self) -> None:
        with pytest.raises(RegroundingError, match="no latents"):
            reground(
                _plan(),
                store=_store(),
                series_by_entity={},
                probes=_probe(30),
                probe_ids=PROBE_IDS,
                wm_version=WM,
                rules=RULES,
                settings=SETTINGS,
                claims=[],
                ingest_factory=lambda: default_ingest(SETTINGS),
                apply=True,
            )

    def test_only_the_planned_interval_is_written(self) -> None:
        store = _store()
        _live(store, 30, tx=OLD_TX)
        narrow = plan_regrounding(
            trigger=RegroundingTrigger.PROBE_VERSION,
            reason="期間を絞る",
            entities=[PUMP],
            fluents=[RUNNING],
            interval=Interval(T0, T0 + STEP * 10),
            from_version="probe/1.0.0",
            to_version="probe/2.0.0",
        )
        outcome = reground(
            narrow,
            store=store,
            series_by_entity={PUMP: _series()},
            probes=_probe(45),
            probe_ids=PROBE_IDS,
            wm_version=WM,
            rules=RULES,
            settings=SETTINGS,
            claims=[],
            ingest_factory=lambda: default_ingest(SETTINGS),
            apply=True,
            at=NOW,
        )
        assert all(a.valid_from < T0 + STEP * 10 for a in outcome.added)
        assert all(row.valid_from < T0 + STEP * 10 for row in outcome.closed)


class TestReviewFindings:
    """M9-01 の独立レビューの指摘（複数フルーエント、結合の文、似た来歴）。"""

    def test_fluents_switching_at_the_same_instant_are_both_replaced(self) -> None:
        faulted = str(vocab.ent.Faulted)

        def both(switch: int):  # type: ignore[no-untyped-def]
            def run(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
                on = np.where(latents[:, 0] < switch, 0.95, 0.05)
                return {"Running": on, "Faulted": 1.0 - on}

            return run

        ids = {**PROBE_IDS, "Faulted": "https://sbp.example/probe/Faulted/2.0.0"}
        store = _store()
        for outcome in replay_entity(
            entity=PUMP,
            series=_series(),
            probes=both(30),
            probe_ids=ids,
            wm_version=WM,
            rules=RULES,
            settings=SETTINGS,
            claims=[],
            ingest=default_ingest(SETTINGS),
        ):
            assert outcome.assertion is not None
            store.assert_(outcome.assertion.model_copy(update={"tx_from": OLD_TX}))
        plan = plan_regrounding(
            trigger=RegroundingTrigger.PROBE_VERSION,
            reason="2 つのフルーエント",
            entities=[PUMP],
            fluents=[RUNNING, faulted],
            interval=Interval(T0, T0 + STEP * (N + 1)),
            from_version="probe/1.0.0",
            to_version="probe/2.0.0",
        )

        def go(switch: int):  # type: ignore[no-untyped-def]
            return reground(
                plan,
                store=store,
                series_by_entity={PUMP: _series()},
                probes=both(switch),
                probe_ids=ids,
                wm_version=WM,
                rules=RULES,
                settings=SETTINGS,
                claims=[],
                ingest_factory=lambda: default_ingest(SETTINGS),
                apply=True,
                at=NOW,
            )

        same = go(30)
        assert not same.added
        assert not same.closed
        changed = go(45)
        for predicate in (RUNNING, faulted):
            assert [a for a in changed.added if a.predicate == predicate], predicate
            assert store.query(subject=PUMP, predicate=predicate), "旧文だけ閉じて信念を欠かさない"

    @pytest.mark.parametrize(
        "provenance",
        [
            "urn:sbp:prov:belief-worker/coupling/2026-11-08T09:00:00+00:00",
            "urn:sbp:prov:belief-worker-2/x",
            "urn:sbp:prov:belief-worker",
        ],
    )
    def test_coupling_and_lookalike_provenance_are_not_closed(self, provenance: str) -> None:
        store = _store()
        _live(store, 30, tx=OLD_TX)
        other = Assertion(
            subject=PUMP,
            predicate=RUNNING,
            object=True,
            valid_from=T0 + STEP * 7,
            tx_from=OLD_TX,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=0.6),
            provenance=provenance,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        store.assert_(other)
        _run(store, 45, apply=True, at=NOW)
        still_open = {row.assertion_id for row in store.query(subject=PUMP, predicate=RUNNING)}
        assert other.assertion_id in still_open
