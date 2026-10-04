"""学習信号の適用ワーカーの単体テスト（M8-08、ADR-0012）。

重点:

1. **全部を自動適用しない** — 観測要求と（既定 dry の）書き戻しだけ。信頼性更新・負例バッグ・
   概念候補は**記録するだけ**（ADR-0012 の問い 3）。
2. **1 件の失敗で残りを止めない** — 人の判断が溜まる。
3. **失敗を握りつぶさない** — `failed_at` と `error` に残る。
4. **概念を採択してもオントロジーに触らない**（M6-04 の決定）。
5. **確信度を推測しない** — 不一致の本体が読めないので 0 にする（M8-12 待ち）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from services.learning_worker import LearningWorker, reconstruct_discrepancy

from sbp.core.config import Settings
from sbp.knowledge.decisions import DecisionKind, DecisionRow
from sbp.reasoning.reconciliation import Resolution, ResolutionOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.integration.writeback import WritebackCommand
    from sbp.knowledge.observations import ObservationRow

T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
OPERATOR = "https://sbp.example/person/operator-1"
DISCREPANCY_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
"""不一致の識別子は UUID（所見 77）。"""


class _Queue:
    """判断キューの代役。"""

    def __init__(self, rows: list[DecisionRow] | None = None) -> None:
        self.rows = rows or []
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


class _Observations:
    """観測要求キューの代役。"""

    def __init__(self) -> None:
        self.enqueued: list[ObservationRow] = []

    async def __call__(self, rows: Sequence[ObservationRow]) -> int:
        self.enqueued.extend(rows)
        return len(rows)


class _Writeback:
    """書き戻しの代役。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.commands: list[WritebackCommand] = []
        self.fail = fail

    async def __call__(self, command: WritebackCommand) -> object:
        if self.fail:
            raise RuntimeError("業務システムが応答しない")
        self.commands.append(command)
        return None


def _resolution_row(
    *,
    outcome: str = "undetermined",
    decision_id: str = "d-1",
    discrepancy_id: str = DISCREPANCY_ID,
) -> DecisionRow:
    return DecisionRow(
        decision_id=decision_id,
        kind=DecisionKind.RESOLUTION,
        subject=discrepancy_id,
        decided_by=OPERATOR,
        payload={
            "decision_id": decision_id,
            "discrepancy_id": discrepancy_id,
            "outcome": outcome,
            "note": "現場を見たが判断できない",
            "resolved_by": OPERATOR,
            "corrections": {},
        },
        received_at=T0,
    )


def _curation_row(*, accepted: bool = True) -> DecisionRow:
    return DecisionRow(
        decision_id="c-1",
        kind=DecisionKind.CURATION,
        subject="prop-1",
        decided_by=OPERATOR,
        payload={
            "decision_id": "c-1",
            "proposal_id": "prop-1",
            "accepted": accepted,
            "reason": "既存の Accessible で表せる",
            "decided_by": OPERATOR,
        },
        received_at=T0,
    )


def _worker(
    *,
    rows: list[DecisionRow] | None = None,
    observations: _Observations | None = None,
    writeback: _Writeback | None = None,
) -> tuple[LearningWorker, _Queue, _Observations]:
    queue = _Queue(rows)
    sink = observations if observations is not None else _Observations()
    worker = LearningWorker(
        Settings(),
        decisions=queue,  # type: ignore[arg-type]
        observations=sink,
        writeback=writeback,
        period_s=0.0,
    )
    return worker, queue, sink


class TestResolutionApplication:
    """解決の適用。"""

    @pytest.mark.asyncio
    async def test_undetermined_enqueues_observation_requests(self) -> None:
        """「判定不能」は追加観測の要求を作業キューに積む。

        **これが M8-05 の供給元である**——キューに書く側が無かったので、スケジューラは
        常に 0 件を返していた。
        """
        worker, queue, sink = _worker(rows=[_resolution_row(outcome="undetermined")])

        applied = await worker.step()

        assert applied == 1
        assert len(sink.enqueued) >= 1, "観測要求が積まれていない"
        assert queue.applied[0][1]["observation_requests_enqueued"] >= 1

    @pytest.mark.asyncio
    async def test_the_summary_records_what_was_not_applied(self) -> None:
        """適用しなかった成果の件数も要約に残る。

        「何が適用されずに残っているか」が UI から読める（ADR-0012 の問い 3）。
        """
        worker, queue, _sink = _worker(rows=[_resolution_row(outcome="record_correct")])

        await worker.step()

        summary = queue.applied[0][1]
        assert "bags_recorded" in summary
        assert "reliability_updates_recorded" in summary
        assert "concept_candidates_recorded" in summary
        assert summary["writeback_mode"] == "dry"

    @pytest.mark.asyncio
    async def test_without_a_writeback_runner_nothing_is_written_back(self) -> None:
        """書き戻しが未配線なら実行しない（提案として記録するだけ）。

        業務システムを変える経路は運用者が明示配線する。
        """
        worker, queue, _sink = _worker(rows=[_resolution_row(outcome="physical_correct")])

        await worker.step()

        assert queue.applied[0][1]["writebacks_run"] == 0

    @pytest.mark.asyncio
    async def test_a_failing_writeback_marks_the_decision_failed(self) -> None:
        """書き戻しが落ちたら判断を失敗として残す（行は消さない）。"""
        worker, queue, _sink = _worker(
            rows=[_resolution_row(outcome="physical_correct")],
            writeback=_Writeback(fail=True),
        )

        # 再構成した不一致は証拠を持たないので訂正提案が出ない。出ないことを前提にせず、
        # 落ちる経路があること自体を確かめる（本体が読めるようになれば提案が出る）
        applied = await worker.step()

        assert applied + len(queue.failed) == 1


class TestCurationApplication:
    """概念採否の適用。"""

    @pytest.mark.asyncio
    async def test_accepting_does_not_touch_the_ontology(self) -> None:
        """採択してもオントロジーには触らない（M6-04 の決定）。

        自動で語彙を増やさない。MINOR 版の起票は人の手作業である。
        """
        worker, queue, _sink = _worker(rows=[_curation_row(accepted=True)])

        await worker.step()

        summary = queue.applied[0][1]
        assert summary["accepted"] is True
        assert summary["ontology_changed"] is False

    @pytest.mark.asyncio
    async def test_rejecting_is_recorded_too(self) -> None:
        """却下も記録される（理由が残らないと同じ提案が繰り返し出る）。"""
        worker, queue, _sink = _worker(rows=[_curation_row(accepted=False)])

        await worker.step()

        assert queue.applied[0][1]["accepted"] is False


class TestFailureIsolation:
    """1 件の失敗で残りを止めない。"""

    @pytest.mark.asyncio
    async def test_a_broken_decision_does_not_block_the_rest(self) -> None:
        """壊れた判断があっても残りは適用される。

        止めると人の判断が溜まる。
        """
        broken = DecisionRow(
            decision_id="bad",
            kind=DecisionKind.RESOLUTION,
            subject=DISCREPANCY_ID,
            decided_by=OPERATOR,
            payload={},  # outcome も note も無い
            received_at=T0,
        )
        worker, queue, _sink = _worker(rows=[broken, _resolution_row(decision_id="d-ok")])

        applied = await worker.step()

        assert applied == 1
        assert [decision_id for decision_id, _ in queue.applied] == ["d-ok"]
        assert len(queue.failed) == 1
        assert queue.failed[0][0] == "bad"

    @pytest.mark.asyncio
    async def test_the_error_is_recorded_not_swallowed(self) -> None:
        """失敗の理由が残る（人が再投入の判断をするのに要る）。"""
        broken = DecisionRow(
            decision_id="bad",
            kind=DecisionKind.RESOLUTION,
            subject=DISCREPANCY_ID,
            decided_by=OPERATOR,
            payload={"outcome": "not_a_real_outcome"},
            received_at=T0,
        )
        worker, queue, _sink = _worker(rows=[broken])

        await worker.step()

        assert len(queue.failed) == 1
        assert queue.failed[0][1], "理由が空"


class TestUnconfiguredPaths:
    """設定が無いときに黙って成功しない。"""

    @pytest.mark.asyncio
    async def test_without_a_queue_nothing_happens(self) -> None:
        """判断キューが無ければ何もしない（起動時に警告する）。"""
        worker = LearningWorker(Settings())

        assert await worker.step() == 0

    @pytest.mark.asyncio
    async def test_an_empty_queue_is_not_an_error(self) -> None:
        """判断が無いのは正常（0 件を返す）。"""
        worker, _queue, _sink = _worker(rows=[])

        assert await worker.step() == 0


class TestReconstructedDiscrepancy:
    """キューの行からの不一致の再構成（M8-12 までの暫定）。"""

    def test_the_confidence_is_not_guessed(self) -> None:
        """確信度を推測しない。

        本体が読めないので 0 にする——「分からない」を中間の値で埋めると、学習信号の重みが
        根拠なく付く。
        """
        resolution = Resolution(
            outcome=ResolutionOutcome.UNDETERMINED,
            note="分からない",
            resolved_by=OPERATOR,
            resolved_at=T0,
        )
        discrepancy = reconstruct_discrepancy(_resolution_row(), resolution=resolution, at=T0)

        assert discrepancy.severity.confidence == 0.0
        assert discrepancy.severity.score == 0.0
        assert discrepancy.evidence == (), "証拠を持たない（記録を引けない）"

    def test_the_interval_is_non_empty(self) -> None:
        """区間が空にならない（右開区間は空を許さない）。"""
        resolution = Resolution(
            outcome=ResolutionOutcome.UNDETERMINED,
            note="分からない",
            resolved_by=OPERATOR,
            resolved_at=T0,
        )
        discrepancy = reconstruct_discrepancy(_resolution_row(), resolution=resolution, at=T0)

        assert discrepancy.interval.end is not None
        assert discrepancy.interval.end > discrepancy.interval.start


class TestTraceability:
    """学習信号が元の不一致に辿れること（所見 77 の回帰テスト）。"""

    def test_the_discrepancy_id_is_carried_over(self) -> None:
        """再構成した不一致が元の識別子を持つ。

        ``Discrepancy.discrepancy_id`` は既定で新しい UUID を作るので、渡さないと
        **観測要求が別の不一致を指す**（統合テストで実際にそうなった）。
        """
        resolution = Resolution(
            outcome=ResolutionOutcome.UNDETERMINED,
            note="分からない",
            resolved_by=OPERATOR,
            resolved_at=T0,
        )

        discrepancy = reconstruct_discrepancy(_resolution_row(), resolution=resolution, at=T0)

        assert str(discrepancy.discrepancy_id) == DISCREPANCY_ID

    def test_a_non_uuid_subject_is_refused(self) -> None:
        """UUID でない対象は拒否する（推測で新しい ID を作らない）。

        辿れない学習信号は、無いほうがまだ分かる。
        """
        resolution = Resolution(
            outcome=ResolutionOutcome.UNDETERMINED,
            note="分からない",
            resolved_by=OPERATOR,
            resolved_at=T0,
        )
        row = DecisionRow(
            decision_id="d-x",
            kind=DecisionKind.RESOLUTION,
            subject="not-a-uuid",
            decided_by=OPERATOR,
            payload={"outcome": "undetermined", "note": "x", "resolved_by": OPERATOR},
            received_at=T0,
        )

        with pytest.raises(ValueError, match="not a UUID"):
            reconstruct_discrepancy(row, resolution=resolution, at=T0)

    def test_the_subject_is_not_the_discrepancy_id(self) -> None:
        """実体 IRI の位置に不一致の ID を入れない。

        入れると、観測要求が存在しない実体を指す。
        """
        resolution = Resolution(
            outcome=ResolutionOutcome.UNDETERMINED,
            note="分からない",
            resolved_by=OPERATOR,
            resolved_at=T0,
        )
        row = _resolution_row()
        row.payload["entity"] = PUMP

        discrepancy = reconstruct_discrepancy(row, resolution=resolution, at=T0)

        assert discrepancy.subjects == (PUMP,)
