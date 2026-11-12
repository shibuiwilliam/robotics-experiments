"""前提の三面検証（設計書 §9.2 の 1、M5-02）。

> 前提検証：記号（SHACL/Datalog）・物理（信念クエリ）・規範（Norm 規則）。

**三面を AND で要求し、どの面で落ちたかを残す。** 「実行できません」だけでは人が上書きの判断
をできない。物理条件で落ちたのか規程で落ちたのかで、取るべき手は全く違う（前者は観測を増やす、
後者は規程の例外承認を取る）。

**物理条件は信念を読む**（記録ではない）。「帳簿では近づける」ではなく「近づけると信じている
確信度」で判定する。記録を直接読むと、記録が誤っている場合にアクションが物理的に実行不能な
状態で走る——設計書 §9.3 の安全ガードが止めたいのはこれである。

**規範条件は規則エンジンの結果を受け取る。** ここで規則を評価しないのは、clingo の実行が
``reasoning`` 層にあるためで、層依存（``actions`` は ``reasoning`` を import できる）とは別に
**同じ規則を 2 箇所で評価しない**ためである。評価が 2 系統あると、照合エンジンが起票した規範
違反とアクションの前提判定が食い違う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sbp.actions.contracts import ActionContract, Precondition
    from sbp.core.records import Record

__all__ = ["ConditionResult", "PreconditionReport", "check_preconditions"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ConditionResult:
    """前提 1 条件の判定結果。

    Attributes:
        condition: 判定した条件。
        satisfied: 満たしているか。
        observed: 実際に観測した値（確信度、該当件数など）。人が読む説明に入る。
        note: 判定の補足（なぜ満たさないか）。
    """

    condition: Precondition
    satisfied: bool
    observed: float | None
    note: str

    @property
    def face(self) -> Literal["symbolic", "physical", "normative"]:
        """どの面の条件か。"""
        return self.condition.face


@dataclass(frozen=True, slots=True)
class PreconditionReport:
    """三面の判定結果をまとめたもの。

    Attributes:
        action_type: 対象のアクション型 IRI。
        target: 対象実体の IRI。
        results: 各条件の判定。
        missing_evidence: 判定に必要な証拠が無かった条件（信念が未知など）。
    """

    action_type: str
    target: str
    results: tuple[ConditionResult, ...]
    missing_evidence: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        """すべての面を満たしているか。"""
        return all(result.satisfied for result in self.results)

    def failed_faces(self) -> tuple[str, ...]:
        """落ちた面の名前（重複なし、宣言順）。"""
        order = ("symbolic", "physical", "normative")
        failed = {result.face for result in self.results if not result.satisfied}
        return tuple(face for face in order if face in failed)

    def failures(self) -> tuple[ConditionResult, ...]:
        """落ちた条件。"""
        return tuple(result for result in self.results if not result.satisfied)

    def explain(self) -> str:
        """人が読む説明（承認画面と来歴に出す）。"""
        if self.satisfied:
            return f"{self.action_type} の前提は三面すべて満たしている"
        parts = [
            f"[{result.face}] {result.condition.description}"
            + (f"（観測 {result.observed:.3f}）" if result.observed is not None else "")
            + (f": {result.note}" if result.note else "")
            for result in self.failures()
        ]
        return f"{self.action_type} の前提が未達: " + " / ".join(parts)


def check_preconditions(
    contract: ActionContract,
    *,
    target: str,
    beliefs: Mapping[str, float] | None = None,
    records: Sequence[Record] = (),
    norm_violations: Sequence[str] = (),
) -> PreconditionReport:
    """三面の前提を検証する（設計書 §9.2）。

    **信念が未知の物理条件は「満たしていない」扱いにする。** 知らないことを「満たしている」と
    すると、観測が無い設備に対してアクションが通ってしまう（設計書 §9.3 の安全ガードの要点）。
    ただし「未達」と「判定不能」は区別できるよう ``missing_evidence`` に残す。

    Args:
        contract: 検証する契約。
        target: 対象実体の IRI。
        beliefs: フルーエント型 IRI → 確信度（``reasoning`` の信念更新の出力）。
        records: 対象に関する業務記録。
        norm_violations: 規則エンジンが出した規範違反の規則名。

    Returns:
        三面の判定結果。
    """
    known = dict(beliefs or {})
    results: list[ConditionResult] = []
    missing: list[str] = []

    for condition in contract.all_preconditions:
        if condition.kind == "belief_at_least":
            assert condition.fluent is not None and condition.threshold is not None
            value = known.get(condition.fluent)
            if value is None:
                missing.append(condition.fluent)
                results.append(
                    ConditionResult(
                        condition=condition,
                        satisfied=False,
                        observed=None,
                        note="信念が無い（知らないことを満たしているとみなさない）",
                    )
                )
                continue
            results.append(
                ConditionResult(
                    condition=condition,
                    satisfied=value >= condition.threshold,
                    observed=value,
                    note="" if value >= condition.threshold else f"閾値 {condition.threshold} 未満",
                )
            )

        elif condition.kind == "belief_at_most":
            assert condition.fluent is not None and condition.threshold is not None
            value = known.get(condition.fluent)
            if value is None:
                missing.append(condition.fluent)
                results.append(
                    ConditionResult(
                        condition=condition,
                        satisfied=False,
                        observed=None,
                        note="信念が無い（知らないことを満たしているとみなさない）",
                    )
                )
                continue
            results.append(
                ConditionResult(
                    condition=condition,
                    satisfied=value <= condition.threshold,
                    observed=value,
                    note="" if value <= condition.threshold else f"閾値 {condition.threshold} 超過",
                )
            )

        elif condition.kind in {"absent_record", "present_record"}:
            matches = _matching_records(records, condition, target=target)
            present = bool(matches)
            wanted = condition.kind == "present_record"
            results.append(
                ConditionResult(
                    condition=condition,
                    satisfied=present is wanted,
                    observed=float(len(matches)),
                    note=""
                    if present is wanted
                    else (
                        f"該当する記録が {len(matches)} 件ある" if present else "該当する記録が無い"
                    ),
                )
            )

        else:  # norm_applies
            violated = condition.rule_ref in set(norm_violations)
            results.append(
                ConditionResult(
                    condition=condition,
                    satisfied=not violated,
                    observed=None,
                    note=f"規範 {condition.rule_ref} に違反している" if violated else "",
                )
            )

    report = PreconditionReport(
        action_type=contract.action_type,
        target=target,
        results=tuple(results),
        missing_evidence=tuple(dict.fromkeys(missing)),
    )
    log.info(
        "actions.preconditions_checked",
        action_type=contract.action_type,
        target=target,
        satisfied=report.satisfied,
        failed_faces=list(report.failed_faces()),
        missing_evidence=len(report.missing_evidence),
    )
    return report


def _matching_records(
    records: Sequence[Record], condition: Precondition, *, target: str
) -> tuple[Record, ...]:
    """条件に合う記録を返す。

    ``where`` は payload の部分一致（宣言した鍵だけを見る）。完全一致にすると、payload に列が
    1 つ増えただけで前提が成立しなくなる。
    """
    return tuple(
        record
        for record in records
        if record.record_type == condition.record_type
        and record.is_about == target
        and all(record.payload.get(key) == value for key, value in condition.where.items())
    )
