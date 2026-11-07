"""期限切れの期待の周期検証（M8-27-4、ADR-0017）。

純粋な判定は ``tests/unit/reasoning/test_expectation_runner.py``。ここで確かめるのは配線:

1. **書く順序** — 不一致・観測要求が先、期待の状態が最後（失敗したら ``Active`` のまま）。
2. **1 件の失敗で残りを止めない**。
3. **周期を守る** — 検証周期の前に回さない。
4. **配線漏れを数える** — 渡されなかった依存は ``verification_unwired`` に出て、検証は動かない。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from services.learning_worker import ExpectationVerification, LearningWorker

from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.time import now
from sbp.reasoning import Expectation, ExpectationStatus, ExpectedEffect
from sbp.reasoning.expectation_store import from_record, to_record
from sbp.reasoning.reconciliation import DiscrepancyKind
from sbp.reasoning.reconciliation.persistence import from_record as discrepancy_from_record
from sbp.reasoning.reconciliation.severity import BusinessValueRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.knowledge.expectations import ExpectationRecord
    from sbp.knowledge.observations import ObservationRow

REPO_ROOT = Path(__file__).resolve().parents[3]
PUMP = "https://sbp.example/site/siteA/asset/P-101"
WORK_ORDER = str(vocab.ent.WorkOrder)
INSPECTED = str(vocab.ent.Inspected)


def _expectation(*, hours_ago: float = 48.0, target: str = PUMP) -> Expectation:
    """期限が ``hours_ago`` 時間前より後（記号 1 時間・物理 24 時間）の ``Active``。"""
    created = now().replace(microsecond=0) - timedelta(hours=hours_ago)
    effects = (
        ExpectedEffect(
            fluent=WORK_ORDER,
            entity=target,
            expected=1.0,
            lower=0.5,
            upper=1.0,
            deadline=created + timedelta(hours=1),
            origin="symbolic",
            description="作業指示が起票される",
        ),
        ExpectedEffect(
            fluent=INSPECTED,
            entity=target,
            expected=0.9,
            lower=0.8,
            upper=1.0,
            deadline=created + timedelta(hours=24),
            origin="rollout",
            description="点検済みになる",
        ),
    )
    return Expectation(
        action_event=uuid4(),
        action_type=str(vocab.ent.CreateEmergencyInspection),
        target=target,
        expected_effects=effects,
        status=ExpectationStatus.ACTIVE,
        created_at=created,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class _World:
    """外界の代役（表・信念・記録の存在）。"""

    def __init__(self, *expectations: Expectation) -> None:
        self.rows = [to_record(item) for item in expectations]
        self.beliefs: dict[str, dict[str, float]] = {}
        self.present: dict[str, frozenset[str]] = {}
        self.discrepancies: list[DiscrepancyRecord] = []
        self.observations: list[ObservationRow] = []
        self.written: list[ExpectationRecord] = []
        self.fail_expectation_write = False
        self.fail_discrepancy_write = False

    async def overdue(self, at: datetime, limit: int) -> list[ExpectationRecord]:
        del at
        return self.rows[:limit]

    async def read_beliefs(self, entity: str) -> dict[str, float]:
        return self.beliefs.get(entity, {})

    async def records(self, entity: str, since: datetime) -> frozenset[str]:
        del since
        return self.present.get(entity, frozenset())

    async def write_discrepancy(self, record: DiscrepancyRecord) -> None:
        if self.fail_discrepancy_write:
            raise ConnectionError("db down")
        self.discrepancies.append(record)

    async def write_expectation(self, record: ExpectationRecord) -> None:
        if self.fail_expectation_write:
            raise ConnectionError("db down")
        self.written.append(record)

    async def enqueue(self, rows: Sequence[ObservationRow]) -> int:
        self.observations.extend(rows)
        return len(rows)


def _verification(world: _World, *, types: dict[str, str] | None = None) -> ExpectationVerification:
    values = BusinessValueRegistry.from_directory(REPO_ROOT / "value", default_scale=1_000_000.0)
    return ExpectationVerification(
        overdue=world.overdue,
        records=world.records,
        discrepancies=world.write_discrepancy,
        values=values,
        entity_types=lambda: types or {},
    )


def _worker(world: _World, *, types: dict[str, str] | None = None) -> LearningWorker:
    verification = _verification(world, types=types)
    return LearningWorker(
        Settings(),
        beliefs=world.read_beliefs,
        expectations=world.write_expectation,
        observations=world.enqueue,
        verification=verification,
    )


class TestVerdictsAreWritten:
    @pytest.mark.asyncio
    async def test_a_fulfilled_expectation_only_advances_its_state(self) -> None:
        world = _World(_expectation())
        world.beliefs[PUMP] = {INSPECTED: 0.95}
        world.present[PUMP] = frozenset({WORK_ORDER})
        assert await _worker(world).step() == 1
        assert [from_record(row).status for row in world.written] == [ExpectationStatus.FULFILLED]
        assert world.discrepancies == []
        assert world.observations == []

    @pytest.mark.asyncio
    async def test_a_violation_files_a_discrepancy_then_advances(self) -> None:
        world = _World(_expectation())
        world.beliefs[PUMP] = {INSPECTED: 0.95}  # 記録が無い → 記号側が外れる
        assert await _worker(world).step() == 1
        assert [from_record(row).status for row in world.written] == [ExpectationStatus.VIOLATED]
        assert len(world.discrepancies) == 1
        filed = discrepancy_from_record(world.discrepancies[0])
        assert filed.kind is DiscrepancyKind.EXISTENCE
        assert filed.subjects == (PUMP,)

    @pytest.mark.asyncio
    async def test_an_undetermined_expectation_asks_for_an_observation(self) -> None:
        world = _World(_expectation())
        world.present[PUMP] = frozenset({WORK_ORDER})  # 信念が無い → 観測不能
        assert await _worker(world).step() == 1
        assert [from_record(row).status for row in world.written] == [
            ExpectationStatus.UNDETERMINED
        ]
        assert [(row.entity, row.fluent) for row in world.observations] == [(PUMP, INSPECTED)]

    @pytest.mark.asyncio
    async def test_the_business_value_of_the_target_type_sets_the_severity(self) -> None:
        plain = _World(_expectation())
        typed = _World(_expectation())
        for world in (plain, typed):
            world.beliefs[PUMP] = {INSPECTED: 0.95}
        await _worker(plain).step()
        await _worker(typed, types={PUMP: str(vocab.ent.Pump)}).step()
        unknown = discrepancy_from_record(plain.discrepancies[0]).severity.value_at_stake
        pump = discrepancy_from_record(typed.discrepancies[0]).severity.value_at_stake
        assert unknown != pump, "型が引ければ登録の業務価値が効く"


class TestOnlyWhatIsDue:
    @pytest.mark.asyncio
    async def test_a_row_before_its_deadline_is_left_alone(self) -> None:
        """読み出しが誤って期限前を返しても、検証側が触らない（所見 118）。"""
        world = _World(_expectation(hours_ago=0.1))
        assert await _worker(world).step() == 0
        assert world.written == []

    @pytest.mark.asyncio
    async def test_the_period_is_respected(self) -> None:
        world = _World(_expectation())
        worker = _worker(world)
        assert await worker.step() == 1
        world.rows = [to_record(_expectation())]
        assert await worker.step() == 0, "検証周期の前には回さない"


class TestFailures:
    @pytest.mark.asyncio
    async def test_a_failed_discrepancy_write_leaves_the_expectation_active(self) -> None:
        """副作用が書けなければ状態を進めない。進めると再試行の対象から外れて違反が消える。"""
        world = _World(_expectation())
        world.beliefs[PUMP] = {INSPECTED: 0.95}
        world.fail_discrepancy_write = True
        worker = _worker(world)
        assert await worker.step() == 0
        assert world.written == []
        assert worker.failed_total == 1

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_the_rest(self) -> None:
        other = "https://sbp.example/site/siteA/asset/P-102"
        world = _World(_expectation(), _expectation(target=other))
        world.beliefs[other] = {INSPECTED: 0.95}
        world.present[other] = frozenset({WORK_ORDER})
        world.fail_expectation_write = False

        original = world.write_expectation
        calls = 0

        async def flaky(record: ExpectationRecord) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("db down")
            await original(record)

        world.write_expectation = flaky  # type: ignore[method-assign]
        worker = _worker(world)
        assert await worker.step() == 1
        assert worker.failed_total == 1


class TestUnwired:
    @pytest.mark.asyncio
    async def test_without_verification_nothing_is_verified_and_it_is_reported(self) -> None:
        world = _World(_expectation())
        worker = LearningWorker(
            Settings(),
            beliefs=world.read_beliefs,
            expectations=world.write_expectation,
            observations=world.enqueue,
        )
        assert worker.verification_unwired == ("verification",)
        assert await worker.step() == 0

    @pytest.mark.asyncio
    async def test_a_missing_observation_sink_stops_verification(self) -> None:
        """観測要求を積めないと、判定不能の期待が要求を残さず終わる。"""
        world = _World(_expectation())
        worker = LearningWorker(
            Settings(),
            beliefs=world.read_beliefs,
            expectations=world.write_expectation,
            verification=_verification(world),
        )
        assert worker.verification_unwired == ("observations",)
        assert await worker.step() == 0
        assert world.written == []
