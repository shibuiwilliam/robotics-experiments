"""不一致と作業項目レコードの相互変換の単体テスト（M8-12、ADR-0013）。

重点:

1. **往復で失われない** — 深刻度の内訳・履歴・解決・学習信号まで戻る。
2. **読み出しで検証する** — 古い版が書いた行が今の不変条件を満たさないまま下流に流れない。
3. **列と本体の食い違いを拒否する** — どちらが正しいか分からない行を黙って使わない。
4. **終端状態の判定が `reasoning` 側の列挙と一致する** — 食い違うと後退の拒否が効かない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sbp.core.time import Interval
from sbp.knowledge.discrepancies import DiscrepancyRecord
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Resolution,
    ResolutionOutcome,
    Severity,
    from_record,
    to_record,
)

T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
ROLE = "https://sbp.example/role/maintenance-planner"


def _discrepancy(**overrides: object) -> Discrepancy:
    defaults: dict[str, object] = {
        "kind": DiscrepancyKind.STATE,
        "subjects": (PUMP,),
        "assertions": ("urn:sbp:assertion:1",),
        "evidence": ("urn:sbp:record:A",),
        "detected_at": T0,
        "interval": Interval(T0, T0 + timedelta(minutes=10)),
        "severity": Severity(
            score=0.42, value_at_stake=1000.0, confidence=0.87, persistence_s=600.0
        ),
        "detector": "kind2",
        "ontology_version": "0.5.0",
    }
    return Discrepancy.model_validate(defaults | overrides)


class TestRoundTrip:
    """往復。"""

    def test_a_round_trip_preserves_everything(self) -> None:
        """変換して戻すと同じ不一致になる。"""
        original = _discrepancy()

        restored = from_record(to_record(original))

        assert restored == original

    def test_the_queryable_columns_are_filled(self) -> None:
        """問い合わせ用の列が埋まる（並べ替えと絞り込みに使う）。"""
        record = to_record(_discrepancy())

        assert record.kind == 2
        assert record.status == "Open"
        assert record.subjects == (PUMP,)
        assert record.severity_score == 0.42
        assert record.detected_at == T0

    def test_a_resolved_discrepancy_round_trips(self) -> None:
        """解決まで進んだ不一致も往復する（入れ子の中身が失われない）。"""
        resolution = Resolution(
            outcome=ResolutionOutcome.PHYSICAL_CORRECT,
            note="現場を見たら止まっていた",
            resolved_by=ROLE,
            resolved_at=T0 + timedelta(hours=1),
        )
        original = (
            _discrepancy()
            .transition(DiscrepancyStatus.TRIAGED, at=T0)
            .transition(DiscrepancyStatus.ASSIGNED, at=T0, assignee_role=ROLE)
            .transition(
                DiscrepancyStatus.RESOLVED,
                at=T0 + timedelta(hours=1),
                resolution=resolution,
            )
        )

        restored = from_record(to_record(original))

        assert restored == original
        assert restored.resolution is not None
        assert len(restored.history) == len(original.history)


class TestValidation:
    """読み出しの検証。"""

    def test_an_inconsistent_payload_is_refused(self) -> None:
        """列と本体の識別子が食い違う行を拒否する。

        どちらが正しいか分からないので、黙って本体を信じない。
        """
        record = to_record(_discrepancy())
        broken = DiscrepancyRecord(
            discrepancy_id=uuid4(),  # 本体と違う識別子
            kind=record.kind,
            status=record.status,
            status_rank=record.status_rank,
            subjects=record.subjects,
            severity_score=record.severity_score,
            detected_at=record.detected_at,
            payload=record.payload,
        )

        with pytest.raises(ValueError, match="does not match payload id"):
            from_record(broken)

    def test_a_payload_violating_the_state_machine_is_refused(self) -> None:
        """状態と中身が整合しない行を拒否する。

        ``Resolved`` なのに解決が無い行は、`Discrepancy` の検証器が弾く——古い版が書いた行が
        今の不変条件を満たさないまま下流に流れない。
        """
        record = to_record(_discrepancy())
        payload = dict(record.payload)
        payload["status"] = "Resolved"  # resolution は無いまま
        broken = DiscrepancyRecord(
            discrepancy_id=record.discrepancy_id,
            kind=record.kind,
            status="Resolved",
            status_rank=DiscrepancyStatus.RESOLVED.rank,
            subjects=record.subjects,
            severity_score=record.severity_score,
            detected_at=record.detected_at,
            payload=payload,
        )

        with pytest.raises(Exception, match="resolution"):
            from_record(broken)


class TestTerminalStatuses:
    """終端状態の判定（`knowledge` と `reasoning` の一致）。"""

    def test_the_terminal_set_matches_the_state_machine(self) -> None:
        """`DiscrepancyRecord.is_terminal` が状態機械の判定と一致する。

        **食い違うと後退の拒否が効かない。** `knowledge` は `reasoning` の列挙を import
        できないので値を写してあり、ここが唯一の照合点である。
        """
        for status in DiscrepancyStatus:
            record = DiscrepancyRecord(
                discrepancy_id=uuid4(),
                kind=2,
                status=status.value,
                status_rank=status.rank,
                subjects=(PUMP,),
                severity_score=0.0,
                detected_at=T0,
            )
            assert record.is_terminal == status.is_terminal, status.value

    def test_the_rank_is_monotone_along_the_lifecycle(self) -> None:
        """ライフサイクルに沿って ``rank`` が単調に増える（後退検出の前提）。

        ``Suppressed`` は ``Open`` からの分岐だが終端なので最上位に置いてある——抑制した
        不一致が非終端で上書きされないようにするためである（所見 80）。
        """
        assert DiscrepancyStatus.OPEN.rank < DiscrepancyStatus.TRIAGED.rank
        assert DiscrepancyStatus.TRIAGED.rank < DiscrepancyStatus.ASSIGNED.rank
        assert DiscrepancyStatus.ASSIGNED.rank < DiscrepancyStatus.RESOLVED.rank
        assert DiscrepancyStatus.RESOLVED.rank < DiscrepancyStatus.LEARNED.rank
        # 終端はどちらも最上位（抑制を非終端で上書きできない）
        assert DiscrepancyStatus.SUPPRESSED.rank == DiscrepancyStatus.LEARNED.rank
        assert all(
            status.rank == DiscrepancyStatus.LEARNED.rank
            for status in DiscrepancyStatus
            if status.is_terminal
        )
