"""コマンドの検証とキュー投入の単体テスト（M8-08、ADR-0012）。

重点:

1. **未知のフィールドを弾く**（`extra="forbid"`）— 綴り間違いを黙って無視すると
   「送ったのに効いていない」状態になる。
2. **根拠を必須にする** — 根拠の無い解決は監査で読めない。却下理由も必須。
3. **冪等の鍵を要求する** — UI の再送で同じ判断が 2 回適用されない。
4. **受け付けるだけ** — 適用は learning-worker。ハンドラが重い仕事をすると HTTP の
   タイムアウトが人の判断を失わせる。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from sbp.api.commands import (
    CurationCommand,
    ExecutionCommand,
    ResolutionCommand,
    build_command_handler,
)
from sbp.core import vocab
from sbp.knowledge.decisions import DecisionKind, DecisionRow

OPERATOR = "https://sbp.example/person/operator-1"
DISCREPANCY_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
PUMP = "https://sbp.example/site/siteA/asset/P-101"
"""不一致の識別子は UUID（所見 77）。"""


def _resolution_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "decision_id": "d-1",
        "discrepancy_id": DISCREPANCY_ID,
        "outcome": "physical_correct",
        "note": "現場を見たら止まっていた",
        "resolved_by": OPERATOR,
    }
    return base | overrides


def _execution_payload(**overrides: object) -> dict[str, object]:
    """アクション実行の引数（既定は承認済み・上書きなし）。"""
    payload: dict[str, object] = {
        "decision_id": "a-1",
        "action_type": vocab.ent.CreateEmergencyInspection,
        "target": PUMP,
        "approved_by": OPERATOR,
        "reason": "停止の疑いが強く、緊急点検を起票する",
        "severity": 0.9,
    }
    return payload | overrides


def _curation_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "decision_id": "c-1",
        "proposal_id": "prop-1",
        "accepted": False,
        "reason": "既存の Accessible で表せる",
        "decided_by": OPERATOR,
    }
    return base | overrides


class TestResolutionCommand:
    """解決コマンドの契約。"""

    def test_a_valid_payload_parses(self) -> None:
        """正しい引数は読める。"""
        command = ResolutionCommand.model_validate(_resolution_payload())

        assert command.outcome == "physical_correct"
        assert command.corrections == {}

    def test_an_unknown_field_is_refused(self) -> None:
        """未知のフィールドを弾く（綴り間違いを黙って無視しない）。"""
        with pytest.raises(ValidationError):
            ResolutionCommand.model_validate(_resolution_payload(resolvedBy=OPERATOR))

    def test_an_empty_note_is_refused(self) -> None:
        """根拠が空の解決を受け付けない（監査で読めない）。"""
        with pytest.raises(ValidationError):
            ResolutionCommand.model_validate(_resolution_payload(note=""))

    def test_an_unknown_outcome_is_refused(self) -> None:
        """五類型の表に無い結果を受け付けない。"""
        with pytest.raises(ValidationError):
            ResolutionCommand.model_validate(_resolution_payload(outcome="maybe"))

    def test_the_decision_id_is_required(self) -> None:
        """冪等の鍵が無い判断を受け付けない。

        UI の再送で同じ判断が 2 回適用されると、書き戻しが 2 回走る。
        """
        payload = _resolution_payload()
        del payload["decision_id"]

        with pytest.raises(ValidationError):
            ResolutionCommand.model_validate(payload)


class TestCurationCommand:
    """概念採否コマンドの契約。"""

    def test_a_rejection_requires_a_reason(self) -> None:
        """却下にも理由が要る（残らないと同じ提案が繰り返し出る）。"""
        with pytest.raises(ValidationError):
            CurationCommand.model_validate(_curation_payload(reason=""))

    def test_a_valid_rejection_parses(self) -> None:
        """却下が読める。"""
        command = CurationCommand.model_validate(_curation_payload())

        assert command.accepted is False


class TestExecutionCommand:
    """アクション実行の引数（M8-21、ADR-0014）。"""

    def test_a_valid_approval_parses(self) -> None:
        """承認付きの実行要求が通る。"""
        command = ExecutionCommand.model_validate(_execution_payload())

        assert command.action_type.endswith("CreateEmergencyInspection")
        assert command.override_reason is None

    def test_a_reason_is_required(self) -> None:
        """理由の無い実行を拒否する（監査で読めない）。"""
        with pytest.raises(ValidationError):
            ExecutionCommand.model_validate(_execution_payload(reason=""))

    def test_an_empty_override_reason_is_refused(self) -> None:
        """上書きの理由が空なら拒否する。

        **上書きは安全ガードを外す操作である**（設計書 §9.3）。空文字を通すと、理由を
        書かせる意図が「フィールドを送れば通る」に変わる。
        """
        with pytest.raises(ValidationError):
            ExecutionCommand.model_validate(_execution_payload(override_reason=""))

    def test_a_severity_outside_the_unit_interval_is_refused(self) -> None:
        """深刻度は確率である（承認の判定に使う）。"""
        with pytest.raises(ValidationError):
            ExecutionCommand.model_validate(_execution_payload(severity=1.5))


class TestHandler:
    """ハンドラの振る舞い。"""

    @pytest.mark.asyncio
    async def test_a_resolution_is_enqueued(self) -> None:
        """解決が判断キューに積まれる。"""
        rows: list[DecisionRow] = []

        async def enqueue(row: DecisionRow) -> bool:
            rows.append(row)
            return True

        handler = build_command_handler(enqueue)
        result = handler("resolve_discrepancy", _resolution_payload())
        await asyncio.sleep(0)  # タスクを走らせる

        assert result["accepted"] is True
        assert result["decision_id"] == "d-1"
        assert len(rows) == 1
        assert rows[0].kind is DecisionKind.RESOLUTION
        assert rows[0].subject == DISCREPANCY_ID

    @pytest.mark.asyncio
    async def test_a_curation_is_enqueued(self) -> None:
        """概念採否が判断キューに積まれる。"""
        rows: list[DecisionRow] = []

        async def enqueue(row: DecisionRow) -> bool:
            rows.append(row)
            return True

        handler = build_command_handler(enqueue)
        handler("curate_concept", _curation_payload())
        await asyncio.sleep(0)

        assert rows[0].kind is DecisionKind.CURATION
        assert rows[0].subject == "prop-1"

    @pytest.mark.asyncio
    async def test_an_execution_is_enqueued(self) -> None:
        """アクションの承認が判断キューに積まれる。

        **主語は対象実体である**（アクション型ではない）。同じ型のアクションが別の設備に
        向くのが普通で、型を主語にすると表の `subject` で辿れなくなる。
        """
        rows: list[DecisionRow] = []

        async def enqueue(row: DecisionRow) -> bool:
            rows.append(row)
            return True

        handler = build_command_handler(enqueue)
        result = handler("execute_action", _execution_payload())
        await asyncio.sleep(0)

        assert result["accepted"] is True
        assert rows[0].kind is DecisionKind.ACTION
        assert rows[0].subject == PUMP
        assert rows[0].decided_by == OPERATOR

    @pytest.mark.asyncio
    async def test_an_unknown_command_is_refused(self) -> None:
        """未知のコマンド名を拒否する（``create_app`` が 409 に変える）。"""

        async def enqueue(row: DecisionRow) -> bool:
            del row
            return True

        handler = build_command_handler(enqueue)

        with pytest.raises(ValueError, match="unknown command"):
            handler("delete_everything", {})

    @pytest.mark.asyncio
    async def test_the_handler_does_not_wait_for_the_queue(self) -> None:
        """ハンドラはキューの書き込みを待たない（受け付けるだけ）。

        待つと、人が DB の応答を待つことになる。
        """
        started = asyncio.Event()

        async def slow_enqueue(row: DecisionRow) -> bool:
            del row
            started.set()
            await asyncio.sleep(10)
            return True

        handler = build_command_handler(slow_enqueue)
        result = handler("resolve_discrepancy", _resolution_payload())

        # 即座に返っている（タスクはまだ走っていなくてよい）
        assert result["accepted"] is True
        assert "learning-worker" in result["detail"]
