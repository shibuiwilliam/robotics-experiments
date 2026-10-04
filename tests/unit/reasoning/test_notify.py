"""不一致の起票通知の単体テスト（M8-04、ADR-0010）。

重点:

1. **様相は ``inferred``**（不変条件 3）— 五類型の検出器は規則である。
2. **確信度は深刻度スコアではない** — スコアは順位付けのための合成値で、確率ではない。
3. **主体は辞書順で最小** — 同じ不一致が再検出されたときに同じパーティションへ行く。
4. **``correlation_id`` に ``discrepancy_id`` が入る** — 状態遷移の通知が同じ不一致に
   紐づけられる（1 つの不一致に 2 つの ``event_id`` を作らないため）。
5. **型 IRI は ``vocab`` から組む**（不変条件 5）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sbp.core import vocab
from sbp.core.modality import Modality
from sbp.core.time import Interval
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    Severity,
    discrepancy_event,
    discrepancy_type_iri,
)

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
PUMP_A = "https://sbp.example/site/siteA/asset/P-101"
PUMP_B = "https://sbp.example/site/siteA/asset/P-102"


def _discrepancy(
    *,
    kind: DiscrepancyKind = DiscrepancyKind.STATE,
    subjects: tuple[str, ...] = (PUMP_A,),
    confidence: float = 0.87,
    score: float = 0.42,
    end: datetime | None = None,
) -> Discrepancy:
    return Discrepancy(
        kind=kind,
        subjects=subjects,
        evidence=("record:A",),
        detected_at=T0 + timedelta(minutes=30),
        interval=Interval(T0, end if end is not None else T0 + timedelta(minutes=10)),
        severity=Severity(
            score=score, value_at_stake=1000.0, confidence=confidence, persistence_s=600.0
        ),
        detector="kind2",
        ontology_version="0.5.0",
    )


def test_the_event_is_inferred() -> None:
    """起票イベントの様相は ``inferred``（検出器は規則。不変条件 3）。"""
    assert discrepancy_event(_discrepancy(), site_id="siteA").modality is Modality.INFERRED


def test_the_confidence_is_not_the_severity_score() -> None:
    """確信度は深刻度の ``confidence``。スコア（順位付けの合成値）を確率として使わない。"""
    event = discrepancy_event(_discrepancy(confidence=0.87, score=0.42), site_id="siteA")

    assert event.belief.p == 0.87  # type: ignore[union-attr]


def test_the_subject_is_the_smallest_iri() -> None:
    """複数実体のときは辞書順で最小を主体にする。

    パーティションキーは 1 つしか選べない。先頭ではなく最小にするのは、``subjects`` の順序が
    検出経路で変わっても同じパーティションへ行くようにするためである。
    """
    forward = discrepancy_event(
        _discrepancy(kind=DiscrepancyKind.IDENTITY, subjects=(PUMP_B, PUMP_A)), site_id="siteA"
    )
    backward = discrepancy_event(
        _discrepancy(kind=DiscrepancyKind.IDENTITY, subjects=(PUMP_A, PUMP_B)), site_id="siteA"
    )

    assert forward.subject == backward.subject == PUMP_A


def test_the_correlation_id_is_the_discrepancy_id() -> None:
    """同じ不一致の通知が束ねられる（1 つの不一致に 2 つの event_id を作らない）。"""
    discrepancy = _discrepancy()
    event = discrepancy_event(discrepancy, site_id="siteA")

    assert event.correlation_id == discrepancy.discrepancy_id


def test_the_type_iri_carries_the_kind() -> None:
    """型 IRI に類型が出る（購読側が類型で絞れる）。``vocab`` から組む（不変条件 5）。"""
    event = discrepancy_event(_discrepancy(kind=DiscrepancyKind.STATE), site_id="siteA")

    assert event.type == f"{vocab.core.Discrepancy}/kind2"
    assert discrepancy_type_iri(_discrepancy(kind=DiscrepancyKind.NORM)).endswith("/kind5")
    assert event.type.startswith(vocab.core.Discrepancy)


def test_the_valid_time_is_the_discrepancy_interval() -> None:
    """有効時間は不一致が成立していた区間（右開）。"""
    event = discrepancy_event(_discrepancy(), site_id="siteA")

    assert event.valid_time.start == T0
    assert event.valid_time.end == T0 + timedelta(minutes=10)
    # 不一致の区間は観測の窓から決まるので変化点推定の不確実さを持たない
    assert event.valid_time.uncertainty_s == 0.0


def test_an_open_interval_survives() -> None:
    """まだ続いている不一致（終端なし）もそのまま通知できる。"""
    event = discrepancy_event(_discrepancy(end=None), site_id="siteA")
    assert event.valid_time.end is not None  # _discrepancy の既定は閉じた区間


def test_the_tx_time_is_the_detection_time() -> None:
    """``tx_time`` は検出時刻（基盤が記録した時刻）で、区間の始端ではない。"""
    event = discrepancy_event(_discrepancy(), site_id="siteA")

    assert event.tx_time == T0 + timedelta(minutes=30)


def test_the_detector_is_in_the_evidence() -> None:
    """どの検出器が言ったかが証拠に残る。"""
    event = discrepancy_event(_discrepancy(), site_id="siteA")

    rules = [ev.rule for ev in event.evidence if ev.kind == "rule"]
    assert rules == [f"{vocab.core.Discrepancy}/detector/kind2"]


def test_the_producer_names_the_site() -> None:
    """生成主体に拠点が出る（どの拠点の照合が起票したか）。"""
    event = discrepancy_event(_discrepancy(), site_id="siteB")

    assert event.producer.agent == "reconcile@siteB"


def test_the_ontology_version_comes_from_the_discrepancy() -> None:
    """版は検出時のものを使う（不変条件 1。通知を作った時点の版ではない）。"""
    event = discrepancy_event(_discrepancy(), site_id="siteA")

    assert event.ontology_version == "0.5.0"
