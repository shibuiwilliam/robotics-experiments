"""隔離の確認の適用（M10-02、ADR-0035）。

重点: 確認が隔離の行に反映される／**無い・確認済み・他拠点の隔離への確認は失敗**
（黙って成功にしない）／**配線が無ければ適用済みにしない**（確認が消える）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from services.learning_worker import LearningWorker

from sbp.core.config import Settings
from sbp.knowledge.decisions import DecisionKind, DecisionRow

T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)
OPERATOR = "https://sbp.example/person/operator-1"


def _row(quarantine_id: UUID, *, decision_id: str = "q-1") -> DecisionRow:
    return DecisionRow(
        decision_id=decision_id,
        kind=DecisionKind.QUARANTINE_REVIEW,
        subject=str(quarantine_id),
        decided_by=OPERATOR,
        payload={
            "decision_id": decision_id,
            "quarantine_id": str(quarantine_id),
            "resolution": "業務側で修正済み",
            "reviewed_by": OPERATOR,
        },
        received_at=T0,
    )


class _Queue:
    def __init__(self, rows: list[DecisionRow]) -> None:
        self.rows = rows
        self.applied: list[tuple[str, dict[str, Any]]] = []
        self.failed: list[tuple[str, str]] = []

    async def pending(self, *, limit: int = 50) -> list[DecisionRow]:
        return self.rows[:limit]

    async def mark_applied(
        self, decision_id: str, *, at: datetime, artifacts: dict[str, Any] | None = None
    ) -> None:
        del at
        self.applied.append((decision_id, artifacts or {}))

    async def mark_failed(self, decision_id: str, *, at: datetime, error: str) -> None:
        del at
        self.failed.append((decision_id, error))


class _Store:
    def __init__(self, known: set[UUID]) -> None:
        self.known = known
        self.reviewed: list[tuple[UUID, str, datetime]] = []

    async def review(self, event_id: UUID, resolution: str, at: datetime) -> bool:
        if event_id not in self.known:
            return False
        self.known.discard(event_id)  # 一度だけ（確認済みは更新しない）
        self.reviewed.append((event_id, resolution, at))
        return True


def _worker(rows: list[DecisionRow], store: _Store | None) -> tuple[LearningWorker, _Queue]:
    queue = _Queue(rows)
    worker = LearningWorker(
        Settings(site_id="siteA"),
        decisions=queue,  # type: ignore[arg-type]
        quarantine=None if store is None else store.review,
    )
    return worker, queue


class TestQuarantineReview:
    @pytest.mark.asyncio
    async def test_the_review_reaches_the_quarantine_row(self) -> None:
        qid = uuid4()
        store = _Store({qid})
        worker, queue = _worker([_row(qid)], store)
        assert await worker.step() == 1
        assert store.reviewed == [(qid, "業務側で修正済み", T0)]
        assert queue.applied[0][1]["quarantine_id"] == str(qid)
        assert queue.failed == []

    @pytest.mark.asyncio
    async def test_a_review_of_an_unknown_or_reviewed_row_fails_visibly(self) -> None:
        store = _Store(set())
        worker, queue = _worker([_row(uuid4())], store)
        await worker.step()
        assert queue.applied == []
        assert "unknown, already reviewed" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_an_unwired_store_never_marks_the_decision_applied(self) -> None:
        worker, queue = _worker([_row(uuid4())], None)
        await worker.step()
        assert queue.applied == []
        assert "not wired" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_a_second_review_of_the_same_row_fails(self) -> None:
        qid = uuid4()
        store = _Store({qid})
        worker, queue = _worker([_row(qid, decision_id="q-1"), _row(qid, decision_id="q-2")], store)
        await worker.step()
        assert [d for d, _a in queue.applied] == ["q-1"]
        assert [d for d, _e in queue.failed] == ["q-2"]
