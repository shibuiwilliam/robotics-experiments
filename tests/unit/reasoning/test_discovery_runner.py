"""概念発見の周期実行の部品（M8-09-2、ADR-0019）。

重点:

1. **運用の入口は反復する不一致** — 解決済みの不一致の ``(実体, 類型, 結果)`` が signal になる。
2. **指紋は時間で変わる値を含めない** — 件数が増えても同じ signal として抑えられる。
3. **再提案の抑制（H-10）** — 判断待ち・採択済みは常に、却下は冷却の間だけ。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sbp.core.time import Interval
from sbp.knowledge.discrepancies import DiscrepancyRecord
from sbp.reasoning.concept_store import to_record as concept_to_record
from sbp.reasoning.discovery import (
    ConceptProposal,
    DiscoverySignal,
    DiscoveryTrigger,
    ProposalStatus,
)
from sbp.reasoning.discovery_runner import (
    resolution_signals,
    signal_fingerprint,
    suppressed_fingerprints,
)
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Resolution,
    ResolutionOutcome,
    Severity,
    to_record,
)

T0 = datetime(2026, 11, 7, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
LINE = "https://sbp.example/site/siteA/asset/LINE-3"
COOLDOWN = timedelta(days=30)


def _resolved(
    *,
    entity: str = PUMP,
    kind: DiscrepancyKind = DiscrepancyKind.STATE,
    outcome: str = "both_wrong",
) -> DiscrepancyRecord:
    open_ = Discrepancy(
        kind=kind,
        subjects=(entity,),
        detected_at=T0,
        interval=Interval(T0, T0 + timedelta(minutes=10)),
        severity=Severity(score=0.5, value_at_stake=1.0, confidence=0.9, persistence_s=60.0),
        detector="kind2",
        ontology_version="0.6.0",
    )
    triaged = open_.transition(DiscrepancyStatus.TRIAGED, at=T0)
    assigned = triaged.transition(
        DiscrepancyStatus.ASSIGNED, at=T0, assignee_role="https://sbp.example/role/planner"
    )
    resolved = assigned.transition(
        DiscrepancyStatus.RESOLVED,
        at=T0,
        resolution=Resolution(
            outcome=ResolutionOutcome(outcome),
            note="人が確認した",
            resolved_by="https://sbp.example/person/p1",
            resolved_at=T0,
        ),
    )
    return to_record(resolved)


def _signal(*, count: int = 3, entity: str = PUMP, kind: int = 2) -> DiscoverySignal:
    return DiscoverySignal(
        trigger=DiscoveryTrigger.REPEATED_DISCREPANCY,
        entities=(entity,),
        strength=0.5,
        evidence=(f"kind{kind}:{count}回",),
        note="反復",
    )


def _proposal(status: ProposalStatus, *, signal: DiscoverySignal | None = None) -> ConceptProposal:
    proposal = ConceptProposal(
        name="PartiallyRunning",
        definition="一部の系統だけが稼働している",
        rejected_alternatives=("Running は全系統を要求する",),
        signals=(signal or _signal(),),
        proposed_at=T0,
    )
    if status is ProposalStatus.PROPOSED:
        return proposal
    return proposal.curate(status, note="理由", revised_definition="修正した定義")


class TestResolutionSignals:
    def test_repeated_resolutions_of_one_entity_and_kind_become_a_signal(self) -> None:
        records = [_resolved() for _ in range(3)]
        signals = resolution_signals(records, min_repeats=3)
        assert len(signals) == 1
        assert signals[0].trigger is DiscoveryTrigger.REPEATED_DISCREPANCY
        assert signals[0].entities == (PUMP,)

    def test_fewer_than_the_threshold_is_not_a_signal(self) -> None:
        assert resolution_signals([_resolved(), _resolved()], min_repeats=3) == ()

    def test_different_kinds_or_entities_are_counted_separately(self) -> None:
        records = [
            _resolved(),
            _resolved(),
            _resolved(kind=DiscrepancyKind.TIME),
            _resolved(entity=LINE),
        ]
        assert resolution_signals(records, min_repeats=3) == ()

    def test_both_wrong_outcomes_make_a_stronger_signal(self) -> None:
        wrong = resolution_signals([_resolved(outcome="both_wrong")] * 3, min_repeats=3)
        plain = resolution_signals([_resolved(outcome="physical_correct")] * 3, min_repeats=3)
        assert wrong[0].strength > plain[0].strength

    def test_a_row_without_a_resolution_is_not_counted(self) -> None:
        bare = replace(_resolved(), payload={})
        assert resolution_signals([bare] * 5, min_repeats=3) == ()


class TestFingerprint:
    def test_the_count_does_not_change_the_fingerprint(self) -> None:
        assert signal_fingerprint(_signal(count=3)) == signal_fingerprint(_signal(count=9))

    def test_another_entity_or_kind_changes_it(self) -> None:
        base = signal_fingerprint(_signal())
        assert base != signal_fingerprint(_signal(entity=LINE))
        assert base != signal_fingerprint(_signal(kind=3))

    def test_entity_order_does_not_matter(self) -> None:
        a = DiscoverySignal(
            trigger=DiscoveryTrigger.REPEATED_DISCREPANCY,
            entities=(PUMP, LINE),
            strength=0.5,
            evidence=("kind2:3回",),
            note="",
        )
        b = replace(a, entities=(LINE, PUMP))
        assert signal_fingerprint(a) == signal_fingerprint(b)


class TestSuppression:
    def _at(self, days: float) -> datetime:
        return T0 + timedelta(days=days)

    def test_a_pending_proposal_always_suppresses(self) -> None:
        record = concept_to_record(_proposal(ProposalStatus.PROPOSED))
        found = suppressed_fingerprints([record], at=self._at(365), cooldown=COOLDOWN)
        assert signal_fingerprint(_signal()) in found

    def test_an_accepted_or_revised_proposal_always_suppresses(self) -> None:
        for status in (ProposalStatus.ACCEPTED, ProposalStatus.REVISED):
            record = concept_to_record(_proposal(status))
            found = suppressed_fingerprints([record], at=self._at(365), cooldown=COOLDOWN)
            assert signal_fingerprint(_signal()) in found, status

    def test_a_rejected_proposal_suppresses_only_during_the_cooldown(self) -> None:
        rejected = concept_to_record(_proposal(ProposalStatus.REJECTED))
        rejected = replace(rejected, updated_at=T0)
        during = suppressed_fingerprints([rejected], at=self._at(29), cooldown=COOLDOWN)
        after = suppressed_fingerprints([rejected], at=self._at(31), cooldown=COOLDOWN)
        assert signal_fingerprint(_signal()) in during
        assert signal_fingerprint(_signal()) not in after, "冷却後は再び提案できる"

    def test_the_cooldown_starts_when_it_was_rejected_not_when_proposed(self) -> None:
        """提案から 100 日後に却下したなら、却下の 29 日後はまだ冷却中。"""
        rejected = replace(
            concept_to_record(_proposal(ProposalStatus.REJECTED)), updated_at=self._at(100)
        )
        found = suppressed_fingerprints([rejected], at=self._at(129), cooldown=COOLDOWN)
        assert signal_fingerprint(_signal()) in found

    def test_another_signal_is_not_suppressed(self) -> None:
        record = concept_to_record(_proposal(ProposalStatus.PROPOSED))
        found = suppressed_fingerprints([record], at=T0, cooldown=COOLDOWN)
        assert signal_fingerprint(_signal(entity=LINE)) not in found

    def test_an_unreadable_row_is_skipped_not_fatal(self) -> None:
        good = concept_to_record(_proposal(ProposalStatus.PROPOSED))
        broken = replace(good, payload={"proposal_id": "00000000-0000-0000-0000-000000000000"})
        found = suppressed_fingerprints([broken, good], at=T0, cooldown=COOLDOWN)
        assert signal_fingerprint(_signal()) in found
