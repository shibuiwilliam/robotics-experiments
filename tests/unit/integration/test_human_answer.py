"""人の確認回答の主張イベント（M8-29、ADR-0018）。

重点:

1. **取込の主張と同じ形** — belief-worker が変更なしで読める（``BusinessEvent/<fluent>``、observed、
   ``RecordEvidence``）。
2. **確率は人の確信度** — 成立なら ``c``、不成立なら ``1 − c``。1.0 は拒否する。
3. **``event_id`` は判断から決まる** — 再試行で同じ ID（不変条件 4）。
4. **信念更新に実際に入る** — 形だけでなく ``BeliefIngest`` が記録尤度として使う。
5. **監査隔離が回答者単位で効く**（不変条件 6）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sbp.core import vocab
from sbp.core.audit import AuditMask
from sbp.core.errors import IntegrationError
from sbp.core.events import EventEnvelope, RecordEvidence
from sbp.core.ids import event_id_timestamp_ms
from sbp.core.modality import Modality
from sbp.integration.human_answer import answer_event, answer_record_iri, answer_topic
from sbp.reasoning.ingest import BeliefIngest, SkipReason, default_transition, is_record_claim

T0 = datetime(2026, 11, 7, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
OPERATOR = "https://sbp.example/person/operator-1"
RUNNING = str(vocab.ent.Running)


def _event(
    *,
    state: bool = True,
    decision_id: str = "a-1",
    at: datetime = T0,
    fluent: str = RUNNING,
    confidence: float = 0.98,
) -> EventEnvelope:
    return answer_event(
        site_id="siteA",
        decision_id=decision_id,
        entity=PUMP,
        fluent=fluent,
        state=state,
        answered_by=OPERATOR,
        at=at,
        confidence=confidence,
        uncertainty_s=300.0,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


def _ingest() -> BeliefIngest:
    return BeliefIngest(
        provenance="urn:sbp:prov:test",
        ontology_version=vocab.ONTOLOGY_VERSION,
        predicate_of={"Running": RUNNING},
        transition=default_transition(step=timedelta(seconds=10)),
    )


class TestShape:
    def test_it_is_a_business_claim_that_is_observed(self) -> None:
        event = _event()
        assert is_record_claim(str(event.type))
        assert event.modality is Modality.OBSERVED
        assert event.subject == PUMP

    def test_the_evidence_names_the_answer_and_the_answerer(self) -> None:
        event = _event()
        assert event.evidence == (RecordEvidence(record=answer_record_iri("a-1"), source=OPERATOR),)

    def test_the_topic_is_the_business_topic(self) -> None:
        assert answer_topic("siteA") == "siteA.integration.business"

    def test_the_valid_time_carries_an_uncertainty(self) -> None:
        assert _event().valid_time.uncertainty_s == pytest.approx(300.0)


class TestProbability:
    def test_a_yes_is_the_confidence_and_a_no_is_its_complement(self) -> None:
        assert _event(state=True).belief.p == pytest.approx(0.98)  # type: ignore[union-attr]
        assert _event(state=False).belief.p == pytest.approx(0.02)  # type: ignore[union-attr]

    @pytest.mark.parametrize("confidence", [1.0, 0.5, 0.3, 1.2])
    def test_a_confidence_that_is_not_an_answer_is_refused(self, confidence: float) -> None:
        with pytest.raises(IntegrationError, match="confidence"):
            _event(confidence=confidence)

    def test_a_non_fluent_is_refused_rather_than_silently_ignored(self) -> None:
        with pytest.raises(IntegrationError, match="FluentType"):
            _event(fluent=str(vocab.ent.WorkOrder))


class TestIdentity:
    def test_the_same_decision_gives_the_same_event_id(self) -> None:
        assert _event().event_id == _event().event_id

    def test_a_different_decision_gives_a_different_id(self) -> None:
        assert _event(decision_id="a-1").event_id != _event(decision_id="a-2").event_id

    def test_the_id_is_a_uuid7_stamped_with_the_answer_time(self) -> None:
        event = _event()
        assert event.event_id.version == 7
        assert event_id_timestamp_ms(event.event_id) == int(T0.timestamp() * 1000)


class TestBeliefUpdate:
    def test_a_yes_moves_the_belief_up_and_a_no_moves_it_down(self) -> None:
        up = _ingest().handle(_event(state=True))
        down = _ingest().handle(_event(state=False))
        assert up.update is not None
        assert down.update is not None
        assert up.update.posterior > 0.9
        assert down.update.posterior < 0.1

    def test_it_counts_as_record_evidence(self) -> None:
        outcome = _ingest().handle(_event())
        assert outcome.update is not None
        assert outcome.update.contribution.dominant == "record"

    def test_the_answerer_can_be_excluded_by_an_audit(self) -> None:
        mask = AuditMask.auditing_source(OPERATOR, reason="回答者の回答を監査する")
        outcome = _ingest().handle(_event(), mask=mask)
        assert outcome.skipped == SkipReason.AUDIT_EXCLUDED
