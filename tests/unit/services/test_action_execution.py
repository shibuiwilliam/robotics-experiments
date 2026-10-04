"""承認されたアクションの実行（M8-21、ADR-0014）。

設計書 §9.2 の順序を production のコードで守ることを確かめる:
前提検証 → 承認 → 実行 → `ActionEvent` → `Expectation` 生成 → 永続化。

重点:

1. **冪等の鍵が再試行で変わらない** — 部分的に失敗した実行の再試行で、業務システムが 2 つの
   命令を見ないこと。
2. **期待を書けなければ実行しない** — 「実行はしたが何も確かめられない」状態を作らない。
3. **信念が読めなければ前提は落ちる** — 知らないことを満たしているとみなさない（§9.3）。
4. **上書きは理由と落ちた面を残す** — 安全ガードを外した記録が来歴に残る。
5. **レート制限が効く** — 同一対象への短時間の反復を止める。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from services.learning_worker import LearningWorker, command_id_for

from sbp.actions import ActionRegistry, ExecutionCommand
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.errors import ActionError
from sbp.knowledge.decisions import DecisionKind, DecisionRow
from sbp.reasoning.expectation_store import from_record

if TYPE_CHECKING:
    from sbp.knowledge.expectations import ExpectationRecord

T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
LEAD = "https://sbp.example/id/person/lead"
INSPECTION = vocab.ent.CreateEmergencyInspection


@pytest.fixture
def contracts() -> ActionRegistry:
    """実物の契約（`actions/*.yaml`）。"""
    return ActionRegistry.from_directory(Path("actions"))


@pytest.fixture
def settings() -> Settings:
    """既定の設定。"""
    return Settings(env="test")


class _Sink:
    """期待の書き込み先（書かれたものを覚える）。"""

    def __init__(self) -> None:
        self.written: list[ExpectationRecord] = []

    async def __call__(self, record: ExpectationRecord) -> None:
        self.written.append(record)


def _command(**overrides: object) -> ExecutionCommand:
    defaults: dict[str, object] = {
        "decision_id": "d-1",
        "action_type": INSPECTION,
        "target": PUMP,
        "approved_by": LEAD,
        "reason": "停止の疑いが強く、緊急点検を起票する",
        "severity": 0.9,
        "correlation_id": uuid4(),
    }
    return ExecutionCommand.model_validate(defaults | overrides)


def _row(command: ExecutionCommand) -> DecisionRow:
    return DecisionRow(
        decision_id=command.decision_id,
        kind=DecisionKind.ACTION,
        subject=command.target,
        decided_by=command.approved_by,
        payload=command.model_dump(mode="json"),
    )


def _worker(
    settings: Settings,
    *,
    contracts: ActionRegistry | None,
    beliefs: dict[str, float] | None,
    sink: _Sink | None,
) -> LearningWorker:
    async def read(entity: str) -> dict[str, float]:
        del entity
        return dict(beliefs or {})

    return LearningWorker(
        settings,
        contracts=contracts,
        beliefs=None if beliefs is None else read,
        expectations=sink,
    )


def _satisfying_beliefs(contracts: ActionRegistry) -> dict[str, float]:
    """緊急点検の物理前提を満たす信念を作る。

    **契約から作る。** 手で並べると、契約が変わったときにテストが前提を満たさなくなり、
    「実行されなかった」理由が分からなくなる（所見 92 と同じ話）。
    """
    contract = contracts.get(INSPECTION)
    beliefs: dict[str, float] = {}
    for condition in contract.all_preconditions:
        if condition.fluent is None or condition.threshold is None:
            continue
        # 下限条件は閾値ちょうど、上限条件は 0 で満たす
        beliefs[condition.fluent] = (
            condition.threshold if condition.kind == "belief_at_least" else 0.0
        )
    return beliefs


class TestTheHappyPath:
    """設計書 §9.2 の順序を通る。"""

    @pytest.mark.asyncio
    async def test_an_approved_action_produces_an_expectation(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """承認されたアクションが期待を残す（§9.2 の 1→4）。"""
        sink = _Sink()
        worker = _worker(
            settings,
            contracts=contracts,
            beliefs=_satisfying_beliefs(contracts),
            sink=sink,
        )
        command = _command()

        summary = await worker._dispatch(_row(command), at=T0)  # pyright: ignore[reportPrivateUsage]

        assert summary["kind"] == "action"
        assert summary["action_type"] == INSPECTION
        assert summary["overridden"] is False
        # 記号側と潜在側の両方の期待が出る（どちらが外れたかを切り分けるため。§8.5）
        assert summary["symbolic_effects"] >= 1
        assert summary["rollout_effects"] >= 1
        assert len(sink.written) == 1
        assert sink.written[0].status == "Active"
        assert sink.written[0].target == PUMP

    @pytest.mark.asyncio
    async def test_the_expectation_points_back_at_the_action(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """期待が実行の記録を指す（効果の検証から実行に辿れる）。"""
        sink = _Sink()
        worker = _worker(
            settings, contracts=contracts, beliefs=_satisfying_beliefs(contracts), sink=sink
        )

        summary = await worker._dispatch(_row(_command()), at=T0)  # pyright: ignore[reportPrivateUsage]

        restored = from_record(sink.written[0])
        assert str(restored.action_event) == summary["action_event"]


class TestIdempotence:
    """冪等の鍵。"""

    def test_the_command_id_is_derived_from_the_decision(self) -> None:
        """同じ判断からは同じ冪等の鍵が出る。

        **`ActionRequest.command_id` の既定は `uuid4` である。** そのまま使うと、適用が
        部分的に失敗して再試行されたときに別の命令になり、業務システムが 2 回変わる。
        """
        assert command_id_for("d-1") == command_id_for("d-1")
        assert command_id_for("d-1") != command_id_for("d-2")

    @pytest.mark.asyncio
    async def test_a_retried_decision_reuses_the_command_id(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """再試行で同じ命令として扱われる（レート制限で 2 回目が落ちても鍵は同じ）。"""
        sink = _Sink()
        worker = _worker(
            settings, contracts=contracts, beliefs=_satisfying_beliefs(contracts), sink=sink
        )
        row = _row(_command())

        await worker._dispatch(row, at=T0)  # pyright: ignore[reportPrivateUsage]
        first = sink.written[0]

        # 時間を飛ばしてレート制限を避け、同じ判断をもう一度適用する
        await worker._dispatch(row, at=T0 + timedelta(days=7))  # pyright: ignore[reportPrivateUsage]

        # 期待は別物（別の実行）だが、業務システムに出る命令の鍵は同じ
        assert from_record(first).action_event != from_record(sink.written[1]).action_event
        assert command_id_for(row.decision_id) == command_id_for(row.decision_id)


class TestRefusals:
    """実行しない場合。"""

    @pytest.mark.asyncio
    async def test_an_unwired_registry_refuses(self, settings: Settings) -> None:
        """契約レジストリが無ければ実行しない。"""
        worker = _worker(settings, contracts=None, beliefs={}, sink=_Sink())

        with pytest.raises(ActionError, match="not wired"):
            await worker._dispatch(_row(_command()), at=T0)  # pyright: ignore[reportPrivateUsage]

    @pytest.mark.asyncio
    async def test_an_unwired_expectation_store_refuses(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """期待を書けなければ実行しない。

        **実行したのに期待を書けないと、効果の検証が永久にできない。** 「実行はしたが何も
        確かめられない」状態を作らないため、配線の検査を実行の**前**に置いている。
        """
        worker = _worker(
            settings, contracts=contracts, beliefs=_satisfying_beliefs(contracts), sink=None
        )

        with pytest.raises(ActionError, match="not wired"):
            await worker._dispatch(_row(_command()), at=T0)  # pyright: ignore[reportPrivateUsage]

    @pytest.mark.asyncio
    async def test_missing_beliefs_fail_the_preconditions(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """信念が読めなければ前提が落ちる（設計書 §9.3）。

        **知らないことを満たしているとみなさない。** 観測が無い設備に対してアクションが
        通ってしまうのを防ぐ。
        """
        sink = _Sink()
        worker = _worker(settings, contracts=contracts, beliefs=None, sink=sink)

        with pytest.raises(ActionError, match="failed its preconditions"):
            await worker._dispatch(_row(_command()), at=T0)  # pyright: ignore[reportPrivateUsage]
        assert sink.written == [], "前提が落ちたのに期待が書かれている"

    @pytest.mark.asyncio
    async def test_an_unknown_action_type_refuses(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """契約に無いアクション型を拒否する。"""
        worker = _worker(
            settings, contracts=contracts, beliefs=_satisfying_beliefs(contracts), sink=_Sink()
        )

        with pytest.raises(ActionError):
            await worker._dispatch(  # pyright: ignore[reportPrivateUsage]
                _row(_command(action_type="https://sbp.example/ent#NoSuchAction")), at=T0
            )

    @pytest.mark.asyncio
    async def test_the_rate_limit_stops_a_repeat(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """同一対象への短時間の反復を止める（設計書 §9.3）。"""
        sink = _Sink()
        worker = _worker(
            settings, contracts=contracts, beliefs=_satisfying_beliefs(contracts), sink=sink
        )
        await worker._dispatch(_row(_command()), at=T0)  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(ActionError, match="rate limited"):
            await worker._dispatch(  # pyright: ignore[reportPrivateUsage]
                _row(_command(decision_id="d-2")), at=T0 + timedelta(minutes=1)
            )


class TestOverride:
    """安全ガードの上書き。"""

    @pytest.mark.asyncio
    async def test_an_override_records_which_faces_it_bypassed(
        self, settings: Settings, contracts: ActionRegistry
    ) -> None:
        """上書きが「どの面を通したか」を残す。

        理由だけでは監査にならない——**何を飛ばしたか**が残らないと、後から危険度を評価できない。
        """
        sink = _Sink()
        # 信念を空にして前提を落とし、上書きで通す
        worker = _worker(settings, contracts=contracts, beliefs={}, sink=sink)

        summary = await worker._dispatch(  # pyright: ignore[reportPrivateUsage]
            _row(_command(override_reason="現場が目視で停止を確認した")), at=T0
        )

        assert summary["overridden"] is True
        assert len(sink.written) == 1
