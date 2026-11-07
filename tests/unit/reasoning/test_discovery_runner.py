"""概念発見の周期実行の部品（M8-09-2、ADR-0019）。

重点:

1. **運用の入口は反復する不一致** — 解決済みの不一致の ``(実体, 類型, 結果)`` が signal になる。
2. **指紋は時間で変わる値を含めない** — 件数が増えても同じ signal として抑えられる。
3. **再提案の抑制（H-10）** — 判断待ち・採択済みは常に、却下は冷却の間だけ。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sbp.core.errors import FixtureMissingError
from sbp.core.time import Interval
from sbp.knowledge.discrepancies import DiscrepancyRecord
from sbp.llm import LlmMode, LlmTask, ReplayProvider
from sbp.llm.base import Message, ProviderResponse, TokenUsage
from sbp.reasoning.concept_store import to_record as concept_to_record
from sbp.reasoning.discovery import (
    ConceptProposal,
    DiscoverySignal,
    DiscoveryTrigger,
    ProposalStatus,
)
from sbp.reasoning.discovery_runner import (
    DiscoveryReport,
    resolution_signals,
    run_discovery,
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

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sbp.knowledge.concepts import ConceptProposalRecord

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


class _Provider:
    """LLM の代役（呼ばれた回数と受けたメッセージを数える）。"""

    name = "stub"

    def __init__(self, *outputs: dict[str, object] | Exception) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[Message]] = []

    def complete_structured(
        self,
        messages: Sequence[Message],
        *,
        schema: dict[str, object],
        task: LlmTask,
        prompt_version: str,
    ) -> ProviderResponse:
        del schema
        self.calls.append(list(messages))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ProviderResponse(
            content=output,
            provider="stub",
            model="stub-model",
            prompt_version=prompt_version,
            prompt_hash="abcd1234",
            usage=TokenUsage(prompt=1, completion=1),
            task=task,
        )

    def embed(self, texts: Sequence[str], *, task: LlmTask) -> list[list[float]]:
        del texts, task
        return []


def _answer(name: str = "PartiallyRunning", **overrides: object) -> dict[str, object]:
    return {
        "name": name,
        "definition": "一部の系統だけが稼働している",
        "superclass": "FluentType",
        "rejected_alternatives": ["Running は全系統を要求する"],
        **overrides,
    }


def _run(
    signals: Sequence[DiscoverySignal],
    provider: _Provider,
    *,
    existing: Sequence[ConceptProposalRecord] = (),
    max_proposals: int = 3,
) -> DiscoveryReport:
    return run_discovery(
        signals,
        existing=existing,
        provider=provider,
        at=T0,
        cooldown=COOLDOWN,
        max_proposals=max_proposals,
        existing_fluents=("Running", "Faulted"),
    )


def _signals(*entities: str, strength: float = 0.5) -> list[DiscoverySignal]:
    return [replace(_signal(entity=entity), strength=strength) for entity in entities]


class TestRunDiscovery:
    def test_a_signal_becomes_a_proposed_concept_with_its_provenance(self) -> None:
        provider = _Provider(_answer())
        report = _run(_signals(PUMP), provider)
        assert len(report.proposals) == 1
        proposal = report.proposals[0]
        assert proposal.status is ProposalStatus.PROPOSED, "自動採用しない"
        assert proposal.llm_provenance["provider"] == "stub"
        assert "@" in proposal.llm_provenance["prompt_version"]
        assert proposal.signals[0].entities == (PUMP,)
        assert proposal.proposed_at == T0

    def test_a_suppressed_signal_costs_no_llm_call(self) -> None:
        existing = [concept_to_record(_proposal(ProposalStatus.PROPOSED))]
        provider = _Provider()
        report = _run(_signals(PUMP), provider, existing=existing)
        assert provider.calls == []
        assert report.suppressed == 1
        assert report.proposals == ()

    def test_the_cycle_limit_defers_the_weaker_signals(self) -> None:
        weak = replace(_signal(entity=LINE), strength=0.1)
        strong = replace(_signal(entity=PUMP), strength=0.9)
        provider = _Provider(_answer("Strong"))
        report = _run([weak, strong], provider, max_proposals=1)
        assert report.deferred == 1
        assert [p.signals[0].entities for p in report.proposals] == [(PUMP,)], "強い方から"
        assert len(provider.calls) == 1

    def test_one_call_per_signal(self) -> None:
        """プロンプトの制約: 1 回の提案は 1 概念まで。"""
        provider = _Provider(_answer("A"), _answer("B"))
        report = _run(_signals(PUMP, LINE), provider)
        assert len(provider.calls) == 2
        assert [p.name for p in report.proposals] == ["A", "B"]
        for call in provider.calls:
            assert call[1].content.count("repeated_discrepancy") == 1

    def test_an_llm_failure_is_counted_and_does_not_stop_the_rest(self) -> None:
        provider = _Provider(FixtureMissingError("no fixture"), _answer("B"))
        report = _run(_signals(PUMP, LINE), provider)
        assert report.failed == 1
        assert [p.name for p in report.proposals] == ["B"]

    def test_a_malformed_output_is_a_failure_not_a_proposal(self) -> None:
        provider = _Provider({"unexpected": True})
        report = _run(_signals(PUMP), provider)
        assert report.failed == 1
        assert report.proposals == ()

    def test_no_rejected_alternatives_means_the_existing_vocabulary_suffices(self) -> None:
        provider = _Provider(_answer(rejected_alternatives=[]))
        report = _run(_signals(PUMP), provider)
        assert report.declined == 1
        assert report.failed == 0
        assert report.proposals == ()

    def test_a_proposal_with_the_name_of_a_blocking_proposal_is_not_taken(self) -> None:
        """判断待ちの提案と同じ名前を、別の signal から返されても重複として採らない。"""
        existing = [
            concept_to_record(_proposal(ProposalStatus.PROPOSED, signal=_signal(entity=LINE)))
        ]
        provider = _Provider(_answer("partiallyrunning"))
        report = _run(_signals(PUMP), provider, existing=existing)
        assert report.declined == 1
        assert report.proposals == ()

    def test_after_the_cooldown_the_same_name_is_a_re_proposal_not_a_duplicate(self) -> None:
        rejected = replace(
            concept_to_record(_proposal(ProposalStatus.REJECTED)),
            updated_at=T0 - timedelta(days=90),
        )
        provider = _Provider(_answer("PartiallyRunning"))
        report = _run(_signals(PUMP), provider, existing=[rejected])
        assert [p.name for p in report.proposals] == ["PartiallyRunning"]

    def test_the_messages_are_deterministic(self) -> None:
        """fixture の鍵（内容ハッシュ）が安定する。順序や時刻が混ざらない。"""
        first = _Provider(_answer())
        second = _Provider(_answer())
        _run(_signals(PUMP), first)
        _run(_signals(PUMP), second)
        assert first.calls == second.calls

    def test_replay_without_a_fixture_fails_loudly_per_signal(self, tmp_path: Path) -> None:
        provider = ReplayProvider(mode=LlmMode.REPLAY, root=tmp_path)
        report = _run(_signals(PUMP), provider)  # type: ignore[arg-type]
        assert report.failed == 1
        assert report.proposals == ()
