"""実体の現在の信念の読み出し（M8-21、M8-27 のレビューで直した）。

belief-worker は ``valid_to`` の無い ``inferred`` の文を**追記するだけで閉じない**。同じ
フルーエントに開いた文が時系列で積まれるので、「確信度が最大の文」ではなく**最新の文**が
現在の信念である。最大を採ると、0.95 だった設備が 0.05 に落ちても検証・前提検査は 0.95 を読む。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.modality import Modality
from sbp.knowledge import AssertionStore, EmbeddedGraphStore, current_beliefs

T0 = datetime(2026, 11, 1, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = str(vocab.ent.Running)


def _store(*points: tuple[timedelta, float]) -> AssertionStore:
    store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
    for offset, p in points:
        store.assert_(
            Assertion(
                subject=PUMP,
                predicate=RUNNING,
                object=True,
                valid_from=T0 + offset,
                tx_from=T0 + offset,
                modality=Modality.INFERRED,
                belief=BooleanBelief(p=p),
                provenance="urn:sbp:prov:test",
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )
    return store


class TestLatestWins:
    def test_a_later_lower_belief_replaces_an_earlier_higher_one(self) -> None:
        store = _store((timedelta(0), 0.95), (timedelta(minutes=5), 0.05))
        assert current_beliefs(store, entity=PUMP) == {RUNNING: 0.05}

    def test_a_later_higher_belief_replaces_an_earlier_lower_one(self) -> None:
        store = _store((timedelta(0), 0.05), (timedelta(minutes=5), 0.95))
        assert current_beliefs(store, entity=PUMP) == {RUNNING: 0.95}

    def test_valid_at_reads_the_belief_in_force_then(self) -> None:
        store = _store((timedelta(0), 0.95), (timedelta(minutes=5), 0.05))
        assert current_beliefs(store, entity=PUMP, at=T0 + timedelta(minutes=2)) == {RUNNING: 0.95}
        assert current_beliefs(store, entity=PUMP, at=T0 + timedelta(minutes=6)) == {RUNNING: 0.05}
