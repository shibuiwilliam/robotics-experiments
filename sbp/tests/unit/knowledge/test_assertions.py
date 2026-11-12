"""二時制アサーション API の単体テスト（不変条件 1, 2, 3）。

組み込み pyoxigraph を使うのでサーバ不要（CLAUDE.md インフラ節）。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from tests.constants import PROV_BUNDLE, PUMP, SITE, T0

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief, CategoricalBelief, NumericBelief
from sbp.core.errors import AppendOnlyViolation, AssertionNotFoundError, ModalityError
from sbp.core.modality import Modality
from sbp.knowledge import AssertionStore, EmbeddedGraphStore, belief_graph, scenario_graph

HOUR = timedelta(hours=1)
VERSION = vocab.ONTOLOGY_VERSION


@pytest.fixture
def store() -> AssertionStore:
    """空の組み込みストアに載せたアサーション API。"""
    return AssertionStore(EmbeddedGraphStore(), site_id=SITE)


def _assertion(**overrides: object) -> Assertion:
    defaults: dict[str, object] = {
        "subject": PUMP,
        "predicate": vocab.ent.Running,
        "object": True,
        "valid_from": T0,
        "tx_from": T0,
        "modality": Modality.INFERRED,
        "belief": BooleanBelief(p=0.91),
        "provenance": PROV_BUNDLE,
        "ontology_version": VERSION,
    }
    return Assertion(**(defaults | overrides))  # type: ignore[arg-type]


class TestAssertAndQuery:
    def test_assert_then_query_round_trip(self, store: AssertionStore) -> None:
        a = _assertion()
        store.assert_(a)

        rows = store.query(subject=PUMP)
        assert len(rows) == 1
        row = rows[0]
        assert row.assertion_id == a.assertion_id
        assert row.subject == PUMP
        assert row.predicate == vocab.ent.Running
        assert row.modality is Modality.INFERRED
        assert row.confidence == pytest.approx(0.91)
        assert row.valid_from == T0
        assert row.valid_to is None
        assert row.is_current

    def test_query_always_returns_ontology_version(self, store: AssertionStore) -> None:
        """knowledge ルール: 呼び出し側が版を無視して比較しないようにする。"""
        store.assert_(_assertion())
        assert store.query()[0].ontology_version == VERSION

    def test_query_filters_by_predicate(self, store: AssertionStore) -> None:
        store.assert_(_assertion(predicate=vocab.ent.Running))
        store.assert_(_assertion(predicate=vocab.ent.UnderMaintenance))
        assert len(store.query(predicate=vocab.ent.Running)) == 1
        assert len(store.query()) == 2

    def test_query_filters_by_modality(self, store: AssertionStore) -> None:
        store.assert_(_assertion(modality=Modality.INFERRED))
        store.assert_(_assertion(modality=Modality.OBSERVED, belief=BooleanBelief(p=1.0)))
        assert len(store.query(modality=Modality.OBSERVED)) == 1
        assert len(store.query(modality=Modality.INFERRED)) == 1

    def test_query_filters_by_min_confidence(self, store: AssertionStore) -> None:
        store.assert_(_assertion(belief=BooleanBelief(p=0.2)))
        store.assert_(_assertion(belief=BooleanBelief(p=0.9)))
        assert len(store.query(min_confidence=0.5)) == 1
        assert len(store.query(min_confidence=0.95)) == 0

    def test_duplicate_assertion_id_is_rejected(self, store: AssertionStore) -> None:
        """不変条件 2: 既存文の上書き経路を作らない。"""
        a = _assertion()
        store.assert_(a)
        with pytest.raises(AppendOnlyViolation, match="already exists"):
            store.assert_(a)

    def test_numeric_and_categorical_beliefs_are_stored(self, store: AssertionStore) -> None:
        store.assert_(
            _assertion(
                predicate=vocab.ent.VibrationRms,
                object=2.5,
                belief=NumericBelief(mean=2.5, var=0.1),
            )
        )
        store.assert_(
            _assertion(
                predicate=vocab.ent.Faulted,
                belief=CategoricalBelief(pmf={"yes": 0.7, "no": 0.3}),
            )
        )
        rows = {r.predicate: r.confidence for r in store.query()}
        assert rows[vocab.ent.VibrationRms] == pytest.approx(1.0)
        assert rows[vocab.ent.Faulted] == pytest.approx(0.7), "カテゴリは最大確率を代表値にする"


class TestBitemporalCorrection:
    def test_retract_closes_tx_without_deleting(self, store: AssertionStore) -> None:
        """不変条件 2: 撤回は tx_to を書くだけ。文は残る。"""
        a = _assertion()
        store.assert_(a)
        store.retract(a.assertion_id, T0 + HOUR)

        assert store.query() == [], "既定クエリは現在の信念だけを返す"
        closed = store.query(include_closed=True)
        assert len(closed) == 1, "閉じた文も消えずに残っている"
        assert closed[0].tx_to == T0 + HOUR
        assert not closed[0].is_current

    def test_as_of_before_retraction_still_sees_the_old_belief(self, store: AssertionStore) -> None:
        """as-of で「当時の信念」を再構成できる（設計書 §6.4）。"""
        a = _assertion(belief=BooleanBelief(p=0.95))
        store.assert_(a)
        store.retract(a.assertion_id, T0 + HOUR)

        before = store.query(as_of=T0 + timedelta(minutes=30))
        assert len(before) == 1
        assert before[0].confidence == pytest.approx(0.95)

        after = store.query(as_of=T0 + timedelta(hours=2))
        assert after == [], "撤回後の時点では見えない"

    def test_correction_is_close_plus_new_statement(self, store: AssertionStore) -> None:
        """訂正の正しい形: 旧文を閉じて新文を追加する（設計書 §14 の MES 訂正に相当）。"""
        old = _assertion(belief=BooleanBelief(p=0.95))
        store.assert_(old)

        # 観測が入って事後が下がった
        store.retract(old.assertion_id, T0 + HOUR)
        new = old.supersede(belief=BooleanBelief(p=0.14), at=T0 + HOUR, provenance=PROV_BUNDLE)
        store.assert_(new)

        current = store.query()
        assert len(current) == 1
        assert current[0].confidence == pytest.approx(0.14)

        # 当時の信念は保存されている
        then = store.query(as_of=T0 + timedelta(minutes=1))
        assert len(then) == 1
        assert then[0].confidence == pytest.approx(0.95)

        # 全履歴を見ると 2 文ある
        assert len(store.query(include_closed=True)) == 2

    def test_retract_unknown_assertion_raises(self, store: AssertionStore) -> None:
        with pytest.raises(AssertionNotFoundError):
            store.retract(uuid4(), T0 + HOUR)

    def test_double_retract_is_rejected(self, store: AssertionStore) -> None:
        a = _assertion()
        store.assert_(a)
        store.retract(a.assertion_id, T0 + HOUR)
        with pytest.raises(AppendOnlyViolation, match="already closed"):
            store.retract(a.assertion_id, T0 + 2 * HOUR)

    def test_retract_before_tx_from_is_rejected(self, store: AssertionStore) -> None:
        a = _assertion()
        store.assert_(a)
        with pytest.raises(AppendOnlyViolation, match="strictly after"):
            store.retract(a.assertion_id, T0 - HOUR)


class TestValidAt:
    def test_valid_at_inside_and_outside(self, store: AssertionStore) -> None:
        store.assert_(_assertion(valid_from=T0, valid_to=T0 + HOUR))
        assert len(store.query(valid_at=T0)) == 1
        assert len(store.query(valid_at=T0 + timedelta(minutes=59))) == 1
        assert store.query(valid_at=T0 + HOUR) == [], "右開区間なので終端は含まない"

    def test_open_ended_validity_matches_far_future(self, store: AssertionStore) -> None:
        store.assert_(_assertion(valid_from=T0))
        assert len(store.query(valid_at=T0 + timedelta(days=365))) == 1

    def test_valid_at_and_as_of_are_independent(self, store: AssertionStore) -> None:
        """有効時間と記録時間は直交する軸である（設計書 §3.6）。"""
        a = _assertion(valid_from=T0, valid_to=T0 + HOUR)
        store.assert_(a)
        store.retract(a.assertion_id, T0 + 2 * HOUR)

        # 当時（tx 内）に、有効だった時刻を問う
        assert len(store.query(as_of=T0 + HOUR, valid_at=T0)) == 1
        # 当時に、有効でなかった時刻を問う
        assert store.query(as_of=T0 + HOUR, valid_at=T0 + HOUR) == []


class TestModalityIsolation:
    def test_hypothetical_goes_to_scenario_graph(self, store: AssertionStore) -> None:
        """不変条件 3: hypothetical は主信念に混ざらない。"""
        store.assert_(
            _assertion(modality=Modality.HYPOTHETICAL, scenario="urn:sbp:scenario:defer-maint")
        )
        assert store.query() == [], "主信念クエリにシナリオの文は現れない"

        in_scenario = store.query(scenario="urn:sbp:scenario:defer-maint")
        assert len(in_scenario) == 1
        assert in_scenario[0].modality is Modality.HYPOTHETICAL

    def test_main_belief_and_scenario_coexist(self, store: AssertionStore) -> None:
        store.assert_(_assertion())
        store.assert_(
            _assertion(modality=Modality.HYPOTHETICAL, scenario="urn:sbp:scenario:what-if")
        )
        assert len(store.query()) == 1
        assert len(store.query(scenario="urn:sbp:scenario:what-if")) == 1

    def test_each_modality_lands_in_its_own_graph(self, store: AssertionStore) -> None:
        store.assert_(_assertion(modality=Modality.OBSERVED, belief=BooleanBelief(p=1.0)))
        store.assert_(_assertion(modality=Modality.PLANNED))
        graphs = {r.modality: r.graph for r in store.query()}
        assert graphs[Modality.OBSERVED] == belief_graph(
            modality=Modality.OBSERVED, site_id=SITE, ontology_version=VERSION
        )
        assert graphs[Modality.PLANNED] == belief_graph(
            modality=Modality.PLANNED, site_id=SITE, ontology_version=VERSION
        )

    def test_predicted_assertion_keeps_horizon(self, store: AssertionStore) -> None:
        store.assert_(
            _assertion(
                predicate=vocab.ent.Faulted,
                modality=Modality.PREDICTED,
                horizon_origin=T0,
                lead_time=timedelta(days=7),
                belief=BooleanBelief(p=0.83),
            )
        )
        rows = store.query(modality=Modality.PREDICTED)
        assert len(rows) == 1
        assert rows[0].confidence == pytest.approx(0.83)


class TestGraphNaming:
    def test_belief_graph_format(self) -> None:
        assert (
            belief_graph(modality=Modality.INFERRED, site_id="siteA", ontology_version="0.2.0")
            == "urn:sbp:g:inferred:siteA:0.2.0"
        )

    def test_hypothetical_cannot_get_a_belief_graph(self) -> None:
        with pytest.raises(ModalityError, match="promote_scenario"):
            belief_graph(modality=Modality.HYPOTHETICAL, site_id="siteA", ontology_version="0.2.0")

    def test_scenario_graph_is_idempotent(self) -> None:
        assert scenario_graph("x") == "urn:sbp:scenario:x"
        assert scenario_graph("urn:sbp:scenario:x") == "urn:sbp:scenario:x"
