"""期待と作業項目レコードの相互変換の単体テスト（M8-18）。

重点:

1. **往復で失われない** — 期待効果・検証結果・履歴まで戻る。
2. **期限は効果の最小値** — ボードが「期限の近い順」で並べるので、読み出しで計算すると
   並べ替えができない。
3. **`Created` は期限を持たない** — 効果がまだ無い。`None` を 0 や現在時刻で埋めない。
4. **終端状態の判定と `rank` が `reasoning` 側の列挙と一致する** — 食い違うと後退の拒否が
   効かない（所見 80）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sbp.knowledge.expectations import ExpectationRecord
from sbp.reasoning.expectation import (
    Expectation,
    ExpectationStatus,
    ExpectedEffect,
    VerificationMethod,
    VerificationResult,
)
from sbp.reasoning.expectation_store import from_record, to_record

T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
ACTION = "https://sbp.example/ent#CreateEmergencyInspection"
RUNNING = "https://sbp.example/ent#Running"
FAULTED = "https://sbp.example/ent#Faulted"


def _effect(*, fluent: str = RUNNING, minutes: int = 60) -> ExpectedEffect:
    return ExpectedEffect(
        fluent=fluent,
        entity=PUMP,
        expected=0.9,
        lower=0.8,
        upper=1.0,
        deadline=T0 + timedelta(minutes=minutes),
        origin="symbolic",
        description="点検後は稼働に戻る",
    )


def _expectation(**overrides: object) -> Expectation:
    defaults: dict[str, object] = {
        "action_event": uuid4(),
        "action_type": ACTION,
        "target": PUMP,
        "expected_effects": (_effect(),),
        "status": ExpectationStatus.ACTIVE,
        "created_at": T0,
        "ontology_version": "0.5.0",
    }
    return Expectation.model_validate(defaults | overrides)


class TestRoundTrip:
    """往復。"""

    def test_a_round_trip_preserves_everything(self) -> None:
        """変換して戻すと同じ期待になる。"""
        original = _expectation()

        assert from_record(to_record(original)) == original

    def test_the_queryable_columns_are_filled(self) -> None:
        """問い合わせ用の列が埋まる。"""
        record = to_record(_expectation())

        assert record.status == "Active"
        assert record.action_type == ACTION
        assert record.target == PUMP
        assert record.created_at == T0

    def test_the_deadline_column_equals_the_model_property(self) -> None:
        """期限の列が ``Expectation.deadline`` と一致する。

        **投影の列が同名のプロパティと違う値を持ってはいけない。** 初版は列に最小値を入れ、
        モデルのプロパティは最大値を返していた——`from_record(r).deadline != r.deadline` が
        黙って成り立つ状態で、どちらを見ているかで答えが変わる（所見 90）。
        """
        original = _expectation(
            expected_effects=(
                _effect(fluent=RUNNING, minutes=120),
                _effect(fluent=FAULTED, minutes=30),
            )
        )

        record = to_record(original)

        assert record.deadline == original.deadline
        # 全体の期限は「最も遅い効果」——それより前に判定を確定できない
        assert record.deadline == T0 + timedelta(minutes=120)

    def test_a_created_expectation_has_no_deadline(self) -> None:
        """``Created`` は期限を持たない（効果がまだ無い）。

        `None` を 0 や現在時刻で埋めると、「期限切れが近い」判断の対象になってしまう。
        """
        record = to_record(_expectation(status=ExpectationStatus.CREATED, expected_effects=()))

        assert record.deadline is None

    def test_a_verified_expectation_round_trips(self) -> None:
        """検証まで進んだ期待も往復する（入れ子の中身が失われない）。"""
        verification = VerificationResult(
            method=VerificationMethod.CONFORMAL,
            verified_at=T0 + timedelta(hours=2),
            symbolic_passed=True,
            rollout_passed=False,
            observations={RUNNING: 0.92},
            statistic=1.5,
            note="記号側は通ったが rollout が外れた",
        )
        # 記号側だけを宣言した期待で rollout_passed を偽にすると Violated になる
        original = _expectation().verify(verification, at=T0 + timedelta(hours=2))

        restored = from_record(to_record(original))

        assert restored == original
        assert restored.verification is not None
        # **宣言していない面の判定は記録に残るが、判定には使われない。** この期待は記号側しか
        # 効果を宣言していないので、rollout が偽でも Fulfilled になる——「観測していない面を
        # 当たりとして数えない」の裏返しで、宣言していない面は無視する
        assert restored.verification.rollout_passed is False
        assert restored.status is ExpectationStatus.FULFILLED


class TestValidation:
    """読み出しの検証。"""

    def test_an_inconsistent_payload_is_refused(self) -> None:
        """列と本体の識別子が食い違う行を拒否する。"""
        record = to_record(_expectation())
        broken = ExpectationRecord(
            expectation_id=uuid4(),
            status=record.status,
            status_rank=record.status_rank,
            action_type=record.action_type,
            target=record.target,
            created_at=record.created_at,
            deadline=record.deadline,
            payload=record.payload,
        )

        with pytest.raises(ValueError, match="does not match payload id"):
            from_record(broken)


class TestStatusRank:
    """終端状態と `rank`（`knowledge` と `reasoning` の一致）。"""

    def test_the_terminal_set_matches_the_state_machine(self) -> None:
        """``ExpectationRecord.is_terminal`` が状態機械の判定と一致する。

        **食い違うと後退の拒否が効かない**（所見 80）。`knowledge` は `reasoning` の列挙を
        import できないので値を写してあり、ここが唯一の照合点である。
        """
        for status in ExpectationStatus:
            record = ExpectationRecord(
                expectation_id=uuid4(),
                status=status.value,
                status_rank=status.rank,
                action_type=ACTION,
                target=PUMP,
                created_at=T0,
            )
            assert record.is_terminal == status.is_terminal, status.value

    def test_the_rank_is_monotone_along_the_lifecycle(self) -> None:
        """ライフサイクルに沿って ``rank`` が単調に増える（後退検出の前提）。

        3 つの判定（``Fulfilled`` / ``Violated`` / ``Undetermined``）は**同じ位置**である
        ——どれも ``Active`` の次で、互いに遷移しない。
        """
        assert ExpectationStatus.CREATED.rank < ExpectationStatus.ACTIVE.rank
        assert ExpectationStatus.ACTIVE.rank < ExpectationStatus.FULFILLED.rank
        assert (
            ExpectationStatus.FULFILLED.rank
            == ExpectationStatus.VIOLATED.rank
            == ExpectationStatus.UNDETERMINED.rank
        )
        assert ExpectationStatus.VIOLATED.rank < ExpectationStatus.LEARNED.rank
        assert all(
            status.rank == ExpectationStatus.LEARNED.rank
            for status in ExpectationStatus
            if status.is_terminal
        )
