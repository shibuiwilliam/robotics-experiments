"""概念発見の周期実行の配線（M8-09-4、ADR-0019）。

発見の中身は ``tests/unit/reasoning/test_discovery_runner.py``。ここで確かめるのは配線:

1. **解決済みの不一致から提案が表に書かれる**（``PROPOSED`` のまま）。
2. **周期を守る** — 発見周期の前に回さない。
3. **LLM の失敗・依存の障害・書き込みの失敗でループを止めない**。件数に残す。
4. **配線が無ければ発見しない**（黙って成功にしない）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from services.learning_worker import ConceptDiscovery, LearningWorker

from sbp.core.config import ReasoningSettings, Settings
from sbp.core.errors import FixtureMissingError
from sbp.core.time import Interval, now
from sbp.knowledge.concepts import ConceptProposalRecord
from sbp.llm.base import ProviderResponse, TokenUsage
from sbp.reasoning.concept_store import from_record
from sbp.reasoning.discovery import ProposalStatus
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

    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.llm.base import LlmTask, Message

T0 = datetime(2026, 11, 7, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _resolved() -> DiscrepancyRecord:
    open_ = Discrepancy(
        kind=DiscrepancyKind.STATE,
        subjects=(PUMP,),
        detected_at=T0,
        interval=Interval(T0, T0 + timedelta(minutes=10)),
        severity=Severity(score=0.5, value_at_stake=1.0, confidence=0.9, persistence_s=60.0),
        detector="kind2",
        ontology_version="0.6.0",
    )
    assigned = open_.transition(DiscrepancyStatus.TRIAGED, at=T0).transition(
        DiscrepancyStatus.ASSIGNED, at=T0, assignee_role="https://sbp.example/role/planner"
    )
    resolved = assigned.transition(
        DiscrepancyStatus.RESOLVED,
        at=T0,
        resolution=Resolution(
            outcome=ResolutionOutcome.BOTH_WRONG,
            note="人が確認した",
            resolved_by="https://sbp.example/person/p1",
            resolved_at=T0,
        ),
    )
    return to_record(resolved)


class _Provider:
    name = "stub"

    def __init__(self, *outputs: dict[str, object] | Exception) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def complete_structured(
        self,
        messages: Sequence[Message],
        *,
        schema: dict[str, object],
        task: LlmTask,
        prompt_version: str,
    ) -> ProviderResponse:
        del messages, schema
        self.calls += 1
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


_ANSWER: dict[str, object] = {
    "name": "PartiallyRunning",
    "definition": "一部の系統だけが稼働している",
    "rejected_alternatives": ["Running は全系統を要求する"],
}


class _World:
    def __init__(self, *, resolved: int = 3) -> None:
        self.resolved = [_resolved() for _ in range(resolved)]
        self.existing: list[ConceptProposalRecord] = []
        self.saved: list[ConceptProposalRecord] = []
        self.fail_resolved = False
        self.fail_save = False

    async def read_resolved(self) -> list[DiscrepancyRecord]:
        if self.fail_resolved:
            raise ConnectionError("db down")
        return self.resolved

    async def read_existing(self) -> list[ConceptProposalRecord]:
        return self.existing

    async def save(self, record: ConceptProposalRecord) -> None:
        if self.fail_save:
            raise ConnectionError("db down")
        self.saved.append(record)


def _worker(world: _World, provider: _Provider, *, every_step: bool = False) -> LearningWorker:
    settings = (
        Settings(reasoning=ReasoningSettings(discovery_period_s=1e-6)) if every_step else Settings()
    )
    return LearningWorker(
        settings,
        discovery=ConceptDiscovery(
            resolved=world.read_resolved,
            existing=world.read_existing,
            save=world.save,
            provider=provider,
            fluents=("Running", "Faulted"),
        ),
    )


class TestDiscoveryCycle:
    @pytest.mark.asyncio
    async def test_repeated_resolutions_become_a_proposed_concept_row(self) -> None:
        world, provider = _World(), _Provider(_ANSWER)
        worker = _worker(world, provider)
        assert await worker.step() == 1
        assert len(world.saved) == 1
        proposal = from_record(world.saved[0])
        assert proposal.status is ProposalStatus.PROPOSED, "自動採用しない"
        assert proposal.signals[0].entities == (PUMP,)
        assert worker.proposed_total == 1

    @pytest.mark.asyncio
    async def test_below_the_repeat_threshold_nothing_is_asked(self) -> None:
        world, provider = _World(resolved=2), _Provider()
        assert await _worker(world, provider).step() == 0
        assert provider.calls == 0

    @pytest.mark.asyncio
    async def test_the_period_is_respected(self) -> None:
        world, provider = _World(), _Provider(_ANSWER)
        worker = _worker(world, provider)
        assert await worker.step() == 1
        assert await worker.step() == 0, "発見周期の前には回さない"
        assert provider.calls == 1

    @pytest.mark.asyncio
    async def test_a_pending_proposal_suppresses_the_next_cycle(self) -> None:
        world, provider = _World(), _Provider(_ANSWER)
        worker = _worker(world, provider, every_step=True)
        await worker.step()
        world.existing = list(world.saved)
        assert await worker.step() == 0
        assert provider.calls == 1, "抑制された signal は LLM を呼ばない（H-10）"


class TestFailures:
    @pytest.mark.asyncio
    async def test_an_llm_failure_is_counted_not_silent(self) -> None:
        world, provider = _World(), _Provider(FixtureMissingError("no fixture"))
        worker = _worker(world, provider)
        assert await worker.step() == 0
        assert world.saved == []
        assert worker.discovery_failed_total == 1

    @pytest.mark.asyncio
    async def test_an_unavailable_dependency_does_not_stop_the_step(self) -> None:
        world, provider = _World(), _Provider()
        world.fail_resolved = True
        worker = _worker(world, provider)
        assert await worker.step() == 0
        assert worker.discovery_failed_total == 1

    @pytest.mark.asyncio
    async def test_a_failed_save_is_counted_and_the_proposal_returns_next_cycle(self) -> None:
        world, provider = _World(), _Provider(_ANSWER)
        world.fail_save = True
        worker = _worker(world, provider)
        assert await worker.step() == 0
        assert worker.discovery_failed_total == 1
        assert worker.proposed_total == 0


class TestRetryAndRanking:
    @pytest.mark.asyncio
    async def test_a_dependency_failure_retries_soon_not_after_a_full_period(self) -> None:
        """読み出しの一時的な障害で 1 日分の発見を失わない（レビュー指摘）。"""
        world, provider = _World(), _Provider(_ANSWER)
        world.fail_resolved = True
        worker = _worker(world, provider)
        assert await worker.step() == 0
        world.fail_resolved = False
        # 既定の周期は 1 日だが、障害後の再試行は 5 分以内に来る
        assert worker.discover_due(at=now() + timedelta(minutes=6))
        assert not worker.discover_due(at=now() + timedelta(seconds=1))

    @pytest.mark.asyncio
    async def test_an_unproductive_signal_is_remembered_for_the_next_cycle(self) -> None:
        world, provider = _World(), _Provider(FixtureMissingError("no fixture"))
        worker = _worker(world, provider)
        await worker.step()
        assert sum(worker.unproductive_signals.values()) == 1


class TestUnwired:
    @pytest.mark.asyncio
    async def test_without_discovery_nothing_happens(self) -> None:
        worker = LearningWorker(Settings())
        assert await worker.step() == 0
        assert worker.proposed_total == 0
