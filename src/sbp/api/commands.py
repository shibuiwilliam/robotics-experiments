"""コマンドの検証とキューへの投入（M8-08、ADR-0012）。

UI が `/commands/*` に POST する人の判断を、**検証してキューに積むところまで**を担う。
適用しない——ハンドラが重い仕事をすると、HTTP のタイムアウトが人の判断を失わせる。

**pydantic で検証する。** コマンドの引数は外部（UI、将来は他のクライアント）から来る契約で
あり、`extra="forbid"` で未知のフィールドを弾く。綴り間違いを黙って無視すると、「送ったのに
効いていない」状態になる。

**冪等の鍵を呼び出し側に要求する。** UI の再送（ネットワークの再試行、人の二度押し）で同じ
判断が 2 回適用されると書き戻しが 2 回走る。``decision_id`` を必須にし、キュー側で
``ON CONFLICT DO NOTHING`` にしてある。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from sbp.actions.requests import ExecutionCommand
from sbp.core.logging import get_logger
from sbp.knowledge.decisions import DecisionKind, DecisionRow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    Enqueue = Callable[[DecisionRow], Awaitable[bool]]
    """``DecisionQueue.enqueue`` を包む。"""

__all__ = [
    "AnswerCommand",
    "CurationCommand",
    "ExecutionCommand",
    "ResolutionCommand",
    "build_command_handler",
]

log = get_logger(__name__)


class ResolutionCommand(BaseModel):
    """不一致の解決（`/commands/resolve_discrepancy`）。

    Attributes:
        decision_id: 冪等の鍵。UI が生成する（再送で同じ値を送ること）。
        discrepancy_id: 対象の不一致。**UUID であることを要求する**——`Discrepancy` の
            識別子は UUID で、ここで文字列を許すと学習信号が元の不一致に辿れなくなる
            （観測要求の `discrepancy_id` が別の値になる。所見 77）。
        outcome: 解決結果（五類型の表の行）。
        note: 判断の根拠（人が書く）。**空を許さない**——根拠の無い解決は監査で読めない。
        resolved_by: 解決した主体の IRI。
        corrections: 訂正内容（``physical_correct`` のときだけ使う）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    discrepancy_id: UUID
    outcome: Literal["physical_correct", "record_correct", "both_wrong", "undetermined"]
    note: str = Field(min_length=1)
    resolved_by: str = Field(min_length=1)
    corrections: dict[str, Any] = Field(default_factory=dict)


class CurationCommand(BaseModel):
    """概念提案の採否（`/commands/curate_concept`）。

    Attributes:
        decision_id: 冪等の鍵。
        proposal_id: 対象の提案。
        accepted: 採択したか。
        reason: 判断の理由。**却下でも必須**——却下理由が残らないと同じ提案が繰り返し出る。
        decided_by: 判断した主体の IRI。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    accepted: bool
    reason: str = Field(min_length=1)
    decided_by: str = Field(min_length=1)


class AnswerCommand(BaseModel):
    """観測要求への人の確認回答（`/commands/answer_observation`。M8-29、ADR-0018）。

    **確信度を引数にしない。** 人の確信度は設定（``human_answer_confidence``）で、回答者が
    自由に決めると、同じ「見た」が回答者ごとに違う重みで信念に入る。

    Attributes:
        decision_id: 冪等の鍵。
        discrepancy_id: 観測要求の鍵の前半。**UUID に限らない**（期待の期限切れ由来は
            ``expectation:<id>``。ADR-0017）。
        fluent: 観測要求の鍵の後半（フルーエント型 IRI）。
        entity: 回答の対象実体。
        state: そのフルーエントは成立しているか。**真偽だけを受ける**（「たぶん」は回答に
            ならない。判断できないなら ``resolve_discrepancy`` の ``undetermined``）。
        note: 回答の根拠。**空を許さない**。
        answered_by: 回答した主体の IRI。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    discrepancy_id: str = Field(min_length=1)
    fluent: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    state: StrictBool
    note: str = Field(min_length=1)
    answered_by: str = Field(min_length=1)


def build_command_handler(enqueue: Enqueue) -> Any:
    """コマンドハンドラを作る（`create_app` の ``command_handler`` に渡す）。

    **同期の関数を返す。** ``create_app`` のハンドラは同期で呼ばれる（API 層の契約）。
    キューへの書き込みは非同期なので、タスクとして投げて即座に受領を返す
    ——「受け付けた」と「適用した」は別である（ADR-0012）。

    **キューそのものではなく積む関数を受け取る。** 接続はアプリの寿命（lifespan）に紐づくので、
    ハンドラを作る時点では開いていない。関数で受ければ、呼ばれた時点の接続を使える。

    Args:
        enqueue: 判断を積む非同期関数（``DecisionQueue.enqueue``）。

    Returns:
        ``(コマンド名, 引数) -> 結果`` の関数。

    Raises:
        ValueError: 未知のコマンド名、または引数が契約に合わない場合（``create_app`` が
            409 に変える）。
    """

    def handle(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """コマンド 1 件を検証してキューに積む。

        Args:
            name: コマンド名。
            payload: 引数（JSON オブジェクト）。
        """
        row = _to_row(name, payload)

        async def _enqueue() -> bool:
            """キューに積む（タスクとして投げる本体）。"""
            return await enqueue(row)

        # **受領を返すだけ。** 適用の成否は learning-worker が決め、キューの
        # applied_at / failed_at に残る
        task = asyncio.create_task(_enqueue())
        # 投げっぱなしにしない。失敗をログに出す——「受け付けた」と答えたのに積まれて
        # いない状態に気付けなくなる
        task.add_done_callback(_log_failure)
        log.info(
            "api.decision_accepted",
            command=name,
            decision_id=row.decision_id,
            subject=row.subject,
        )
        return {
            "accepted": True,
            "decision_id": row.decision_id,
            "detail": "受け付けた（適用は learning-worker が行う）",
        }

    return handle


def _log_failure(task: asyncio.Task[bool]) -> None:
    """積み込みの失敗をログに出す。

    Args:
        task: 完了したタスク。
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.error(
            "api.decision_enqueue_failed",
            error=str(error),
            detail="受け付けたと答えたのに積まれていない。UI の再送で同じ decision_id が来る",
        )


def _to_row(name: str, payload: dict[str, Any]) -> DecisionRow:
    """コマンド名と引数をキューの行にする。

    Args:
        name: コマンド名。
        payload: 引数。

    Raises:
        ValueError: 未知のコマンド名、または引数が契約に合わない場合。
    """
    if name == "resolve_discrepancy":
        command = ResolutionCommand.model_validate(payload)
        return DecisionRow(
            decision_id=command.decision_id,
            kind=DecisionKind.RESOLUTION,
            subject=str(command.discrepancy_id),
            decided_by=command.resolved_by,
            payload=command.model_dump(mode="json"),
        )
    if name == "execute_action":
        execution = ExecutionCommand.model_validate(payload)
        return DecisionRow(
            decision_id=execution.decision_id,
            kind=DecisionKind.ACTION,
            # **対象実体を主語にする。** アクション型ではない——同じ型のアクションが別の設備に
            # 向くのが普通で、型を主語にすると表の `subject` で辿れなくなる
            subject=execution.target,
            decided_by=execution.approved_by,
            payload=execution.model_dump(mode="json"),
        )
    if name == "curate_concept":
        curation = CurationCommand.model_validate(payload)
        return DecisionRow(
            decision_id=curation.decision_id,
            kind=DecisionKind.CURATION,
            subject=curation.proposal_id,
            decided_by=curation.decided_by,
            payload=curation.model_dump(mode="json"),
        )
    if name == "answer_observation":
        answer = AnswerCommand.model_validate(payload)
        return DecisionRow(
            decision_id=answer.decision_id,
            kind=DecisionKind.ANSWER,
            # 対象実体を主語にする（アクションの承認と同じ。要求の鍵は payload にある）
            subject=answer.entity,
            decided_by=answer.answered_by,
            payload=answer.model_dump(mode="json"),
        )
    raise ValueError(
        f"unknown command {name!r}; known commands: resolve_discrepancy, "
        "curate_concept, execute_action, answer_observation"
    )
