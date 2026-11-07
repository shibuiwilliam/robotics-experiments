"""期限切れの期待の周期検証を、production の配線で実サービス上に通す（M8-27-5、ADR-0017）。

外界（アクション実行後に基盤が持っている状態）だけをテストが置く:

- 期待 3 本（``Active``、期限切れ）を作業項目表に、
- 信念（``inferred``）を知識グラフに、
- 業務記録の存在を ``business_records`` に。

learning-worker は production の ``build_worker`` で組み、``step()`` を回す。確かめること:

1. 満たした期待は ``Fulfilled`` になる（不一致も観測要求も出ない）。
2. 記録が無い期待は ``Violated`` になり、類型 1 の不一致が起票される。
3. 信念が無い期待は ``Undetermined`` になり、観測要求が積まれる。
4. **2 周目は何も変わらない**（``Active`` でなくなったので対象から外れる）。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from services.learning_worker import build_worker

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import ReasoningSettings
from sbp.core.modality import Modality
from sbp.core.records import Record
from sbp.core.time import now
from sbp.knowledge import (
    AssertionStore,
    DiscrepancyStore,
    ExpectationStore,
    ObservationQueue,
    RecordIndex,
)
from sbp.reasoning import Expectation, ExpectationStatus, ExpectedEffect
from sbp.reasoning.expectation_runner import discrepancy_id_for
from sbp.reasoning.expectation_store import to_record
from sbp.reasoning.reconciliation import DiscrepancyKind
from sbp.reasoning.reconciliation.persistence import from_record as discrepancy_from_record

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sbp.core.config import Settings
    from sbp.knowledge import HttpGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

WORK_ORDER = str(vocab.ent.WorkOrder)
INSPECTED = str(vocab.ent.Inspected)


@pytest.fixture
def site(test_graph_suffix: str, assertion_store: AssertionStore) -> str:
    """テストごとの拠点（``assertion_store`` は後片付けのために要求する）。"""
    del assertion_store
    return f"{test_graph_suffix}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
async def connection(
    settings: Settings, site: str, postgres_available: None
) -> AsyncIterator[psycopg.AsyncConnection[tuple[Any, ...]]]:
    """表を置く接続。テスト後にこの拠点の行を消す。"""
    del postgres_available
    conn = await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True)
    yield conn
    for table in ("expectations", "discrepancies", "observation_requests", "business_records"):
        await conn.execute(f"DELETE FROM {table} WHERE site = %s", (site,))  # type: ignore[arg-type]
    await conn.close()


def _expectation(entity: str) -> Expectation:
    """期限が 1 日以上前に過ぎた ``Active``（記号 1 時間・物理 24 時間の効果）。"""
    created = now().replace(microsecond=0) - timedelta(days=3)
    effects = (
        ExpectedEffect(
            fluent=WORK_ORDER,
            entity=entity,
            expected=1.0,
            lower=0.5,
            upper=1.0,
            deadline=created + timedelta(hours=1),
            origin="symbolic",
            description="作業指示が起票される",
        ),
        ExpectedEffect(
            fluent=INSPECTED,
            entity=entity,
            expected=0.9,
            lower=0.8,
            upper=1.0,
            deadline=created + timedelta(hours=24),
            origin="rollout",
            description="点検済みになる",
        ),
    )
    return Expectation(
        action_event=uuid.uuid4(),
        action_type=str(vocab.ent.CreateEmergencyInspection),
        target=entity,
        expected_effects=effects,
        status=ExpectationStatus.ACTIVE,
        created_at=created,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


def _belief(entity: str, *, p: float) -> Assertion:
    return Assertion(
        subject=entity,
        predicate=INSPECTED,
        object=True,
        valid_from=now() - timedelta(hours=1),
        tx_from=now() - timedelta(hours=1),
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=p),
        provenance="urn:sbp:prov:it-expectation-verification",
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestPeriodicVerification:
    async def test_overdue_expectations_are_judged_and_their_consequences_written(
        self,
        settings: Settings,
        site: str,
        graph_store: HttpGraphStore,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        fine = f"https://sbp.example/site/{site}/asset/P-OK"
        no_record = f"https://sbp.example/site/{site}/asset/P-NOREC"
        no_belief = f"https://sbp.example/site/{site}/asset/P-NOBEL"
        expectations = {entity: _expectation(entity) for entity in (fine, no_record, no_belief)}

        # 外界として、期待・信念・記録の存在を置く
        store = ExpectationStore(connection, site=site)
        for item in expectations.values():
            await store.upsert(to_record(item))
        assertions = AssertionStore(graph_store, site_id=site)
        assertions.assert_(_belief(fine, p=0.95))
        assertions.assert_(_belief(no_record, p=0.95))
        index = RecordIndex(connection, site=site)
        for entity in (fine, no_belief):
            await index.add(
                [
                    Record(
                        record_type=WORK_ORDER,
                        source=str(vocab.ent.CmmsSource),
                        external_id=f"WO-{entity[-5:]}",
                        is_about=entity,
                        recorded_at=now() - timedelta(days=2),
                        ontology_version=vocab.ONTOLOGY_VERSION,
                    )
                ]
            )

        run_settings = settings.model_copy(
            update={"reasoning": ReasoningSettings(expectation_verify_period_s=1e-6)}
        )
        worker, _decisions = await build_worker(run_settings, site_id=site)
        assert worker.verification_unwired == ()
        assert await worker.step() == 3

        # 1-3. 状態と、それに伴うもの
        async def status_of(entity: str) -> str:
            row = await store.get(expectations[entity].expectation_id)
            assert row is not None
            return row.status

        assert await status_of(fine) == ExpectationStatus.FULFILLED.value
        assert await status_of(no_record) == ExpectationStatus.VIOLATED.value
        assert await status_of(no_belief) == ExpectationStatus.UNDETERMINED.value

        filed = await DiscrepancyStore(connection, site=site).get(
            discrepancy_id_for(expectations[no_record].expectation_id)
        )
        assert filed is not None
        assert discrepancy_from_record(filed).kind is DiscrepancyKind.EXISTENCE
        assert (
            await DiscrepancyStore(connection, site=site).get(
                discrepancy_id_for(expectations[fine].expectation_id)
            )
            is None
        )

        outstanding = await ObservationQueue(connection, site=site).outstanding()
        assert [(row.entity, row.fluent) for row in outstanding] == [(no_belief, INSPECTED)]
        assert outstanding[0].discrepancy_id == (
            f"expectation:{expectations[no_belief].expectation_id}"
        )

        # 4. 2 周目は何も変わらない（Active でなくなったので対象から外れる）
        assert await worker.step() == 0
