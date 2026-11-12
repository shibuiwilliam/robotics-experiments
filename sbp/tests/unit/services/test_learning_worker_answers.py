"""人の確認回答の適用（M8-29-3、ADR-0018）。

重点:

1. **送って、満たす** — 主張イベントを業務トピックへ送り、観測要求を満たす（この順）。
2. **送れなければ満たさない** — 送信の失敗で判断は失敗として残り、要求は未充足のまま。
3. **キューに無い要求への回答は失敗** — 誤送信を黙って通さない。実体が食い違う回答も。
4. **配線が無ければ適用済みにしない** — 送れない回答を適用済みにすると、人の確認が消える。
5. **再投入で同じ ``event_id``** — 冪等（不変条件 4）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from services.learning_worker import AnswerPath, LearningWorker

from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.time import Interval
from sbp.knowledge.decisions import DecisionKind, DecisionRow
from sbp.knowledge.observations import ObservationRow

if TYPE_CHECKING:
    from sbp.core.events import EventEnvelope

T0 = datetime(2026, 11, 7, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
OPERATOR = "https://sbp.example/person/operator-1"
RUNNING = str(vocab.ent.Running)
KEY = "expectation:abc"


def _request(*, entity: str = PUMP) -> ObservationRow:
    return ObservationRow(
        entity=entity,
        fluent=RUNNING,
        interval=Interval(T0 - timedelta(hours=1), T0),
        reason="判定する観測が無い",
        utility=0.6,
        discrepancy_id=KEY,
    )


def _row(*, decision_id: str = "a-1", state: bool = True, entity: str = PUMP) -> DecisionRow:
    return DecisionRow(
        decision_id=decision_id,
        kind=DecisionKind.ANSWER,
        subject=entity,
        decided_by=OPERATOR,
        payload={
            "decision_id": decision_id,
            "discrepancy_id": KEY,
            "fluent": RUNNING,
            "entity": entity,
            "state": state,
            "note": "現場で目視した",
            "answered_by": OPERATOR,
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


class _World:
    def __init__(
        self,
        *,
        request: ObservationRow | None = None,
        fail_send: bool = False,
        fail_satisfy: bool = False,
    ) -> None:
        self.request = request
        self.fail_send = fail_send
        self.fail_satisfy = fail_satisfy
        self.sent: list[tuple[str, EventEnvelope]] = []
        self.satisfied: list[tuple[str, str]] = []
        self.calls: list[str] = []

    async def send(self, topic: str, event: EventEnvelope) -> None:
        self.calls.append("send")
        if self.fail_send:
            raise ConnectionError("broker down")
        self.sent.append((topic, event))

    async def get(self, discrepancy_id: str, fluent: str) -> ObservationRow | None:
        if self.request is None:
            return None
        if (discrepancy_id, fluent) != (self.request.discrepancy_id, self.request.fluent):
            return None
        return self.request

    async def satisfy(self, discrepancy_id: str, fluent: str, at: datetime) -> None:
        del at
        self.calls.append("satisfy")
        if self.fail_satisfy:
            raise ConnectionError("db down")
        self.satisfied.append((discrepancy_id, fluent))

    def path(self) -> AnswerPath:
        return AnswerPath(events=self.send, request=self.get, satisfy=self.satisfy)


def _worker(
    rows: list[DecisionRow], world: _World | None, *, wired: bool = True
) -> tuple[LearningWorker, _Queue]:
    queue = _Queue(rows)
    worker = LearningWorker(
        Settings(site_id="siteA"),
        decisions=queue,  # type: ignore[arg-type]
        answers=world.path() if (world is not None and wired) else None,
    )
    return worker, queue


class TestApplication:
    @pytest.mark.asyncio
    async def test_the_answer_is_sent_then_the_request_is_satisfied(self) -> None:
        world = _World(request=_request())
        worker, queue = _worker([_row()], world)
        assert await worker.step() == 1
        assert world.calls == ["send", "satisfy"], "送信が先"
        topic, event = world.sent[0]
        assert topic == "siteA.integration.business"
        assert event.belief.p == pytest.approx(0.98)  # type: ignore[union-attr]
        assert world.satisfied == [(KEY, RUNNING)]
        assert queue.applied[0][0] == "a-1"
        assert queue.failed == []

    @pytest.mark.asyncio
    async def test_a_no_answer_is_sent_as_the_complement(self) -> None:
        world = _World(request=_request())
        worker, _queue = _worker([_row(state=False)], world)
        await worker.step()
        assert world.sent[0][1].belief.p == pytest.approx(0.02)  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_the_same_decision_gives_the_same_event_id_on_retry(self) -> None:
        first = _World(request=_request())
        second = _World(request=_request())
        await _worker([_row()], first)[0].step()
        await _worker([_row()], second)[0].step()
        assert first.sent[0][1].event_id == second.sent[0][1].event_id


class TestFailures:
    @pytest.mark.asyncio
    async def test_a_failed_send_leaves_the_request_unsatisfied(self) -> None:
        world = _World(request=_request(), fail_send=True)
        worker, queue = _worker([_row()], world)
        assert await worker.step() == 0
        assert world.satisfied == []
        assert queue.applied == []
        assert "ConnectionError" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_a_failed_satisfy_after_a_send_is_a_failure_with_a_stable_event_id(self) -> None:
        """送ったのに要求を満たせなかった判断は失敗として残る（自動では再試行しない）。

        人が同じ ``decision_id`` で再投入すれば、同じ ``event_id`` で送られて消費側が弾く。
        """
        first = _World(request=_request(), fail_satisfy=True)
        worker, queue = _worker([_row()], first)
        assert await worker.step() == 0
        assert len(first.sent) == 1
        assert queue.applied == []
        assert "ConnectionError" in queue.failed[0][1]

        retry = _World(request=_request())
        await _worker([_row()], retry)[0].step()
        assert retry.sent[0][1].event_id == first.sent[0][1].event_id

    @pytest.mark.asyncio
    async def test_an_answer_to_an_unknown_request_fails(self) -> None:
        world = _World(request=None)
        worker, queue = _worker([_row()], world)
        assert await worker.step() == 0
        assert world.sent == []
        assert "no observation request" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_an_answer_about_another_entity_fails(self) -> None:
        world = _World(request=_request(entity="https://sbp.example/site/siteA/asset/P-102"))
        worker, queue = _worker([_row()], world)
        assert await worker.step() == 0
        assert world.sent == []
        assert "differs" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_without_the_answer_path_the_decision_is_not_marked_applied(self) -> None:
        world = _World(request=_request())
        worker, queue = _worker([_row()], world, wired=False)
        assert await worker.step() == 0
        assert queue.applied == []
        assert "not wired" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_a_malformed_payload_fails_rather_than_guessing(self) -> None:
        world = _World(request=_request())
        bad = _row()
        bad = DecisionRow(
            decision_id=bad.decision_id,
            kind=bad.kind,
            subject=bad.subject,
            decided_by=bad.decided_by,
            payload={"decision_id": "a-1"},
            received_at=T0,
        )
        worker, queue = _worker([bad], world)
        assert await worker.step() == 0
        assert world.sent == []
        assert queue.failed


class TestClose:
    @pytest.mark.asyncio
    async def test_closers_run_in_reverse_and_survive_a_failure(self) -> None:
        order: list[str] = []
        worker, _queue = _worker([], None)

        async def first() -> None:
            order.append("first")

        async def broken() -> None:
            raise RuntimeError("stop failed")

        worker.add_closer(first)
        worker.add_closer(broken)
        await worker.aclose()
        assert order == ["first"], "壊れた後始末のあとでも残りが呼ばれる"
