"""計画・規範の取り込み（設計書 §7.4、integration ルール）。

- **PlanItem → 様相 planned のアサーション。** 有効時間は計画区間、確信度は当該計画種の
  **遵守率の事前分布**。計画は守られないことがあるという前提を数値で持つのが要点である。
- **Norm → Datalog/ASP 規則 + 出典。** 違反判定の結果は様相 normative のアサーションになる。
  出典（文書・節）を必ず持つ（規範違反は人の判断に影響するため、根拠まで遡れなければ
  監査に使えない）。

この層はアサーションを**作る**が永続化はしない（層の依存の向き: integration は knowledge の
下位にある）。永続化は ``reasoning`` が ``knowledge`` の公開 API 経由で行う。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.ids import site_iri
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.records import Norm, NormSeverity, PlanItem, Record
from sbp.core.time import ensure_utc

__all__ = ["ADHERENCE_PRIORS", "NormRule", "plan_item_from_record", "to_planned_assertion"]

log = get_logger(__name__)

# 計画種ごとの遵守率の事前分布（設計書 §7.4）。担当者ヒアリング相当の初期値であり、
# 解決済み不一致から継続推定する対象でもある。値を 1.0 にしないのが要点で、
# 「計画は守られないことがある」という前提を数値で持つ。
#
#   PlannedOperation       稼働予定。設計書 §14 の「計画: 09:00-17:00 稼働（遵守率 0.9）」に対応
#   PreventiveMaintenance  予防保全。繰り延べられることが多い
#   Inspection             点検。比較的守られる
ADHERENCE_PRIORS: Final[dict[str, float]] = {
    "PlannedOperation": 0.9,
    "PreventiveMaintenance": 0.75,
    "Inspection": 0.85,
}

_DEFAULT_ADHERENCE: Final = 0.7
"""未知の計画種の遵守率。既知より低くする（知らない計画は信用しない）。"""


def plan_item_from_record(record: Record, *, intended_fluent: str) -> PlanItem:
    """計画の記録を :class:`~sbp.core.records.PlanItem` に変換する。

    Args:
        record: ``maintenance_plan`` 写像を通った記録。
        intended_fluent: 計画が成立させようとするフルーエント型 IRI。

    Returns:
        計画項目。遵守率の事前分布は計画種から引く。

    Raises:
        ValueError: 記録に計画区間（``window_start`` / ``window_end``）が無い場合。
    """
    payload = record.payload
    start_raw = payload.get("window_start")
    end_raw = payload.get("window_end")
    if start_raw is None or end_raw is None:
        raise ValueError(
            f"record {record.external_id} has no planned interval "
            "(window_start / window_end are required for a plan item)"
        )
    plan_type = str(payload.get("plan_type") or "")
    adherence = ADHERENCE_PRIORS.get(plan_type, _DEFAULT_ADHERENCE)
    if plan_type not in ADHERENCE_PRIORS:
        log.info(
            "plans.unknown_plan_type",
            plan_type=plan_type,
            external_id=record.external_id,
            adherence_prior=adherence,
        )
    return PlanItem(
        plan_type=record.record_type,
        source=record.source,
        external_id=record.external_id,
        target=record.is_about or f"urn:sbp:unresolved:{record.record_id}",
        planned_interval_start=_parse(start_raw),
        planned_interval_end=_parse(end_raw),
        intended_fluent=intended_fluent,
        adherence_prior=adherence,
        ontology_version=record.ontology_version,
    )


def to_planned_assertion(plan: PlanItem, *, site_id: str, tx_from: datetime) -> Assertion:
    """計画項目を様相 ``planned`` のアサーションにする（設計書 §7.4）。

    有効時間は計画区間そのもの。確信度は遵守率の事前分布であり、**1.0 にしない**のが要点。
    計画は「こうする予定」という主張であって、そうなる保証ではない。

    Args:
        plan: 計画項目。
        site_id: 拠点識別子（来歴 IRI に使う）。
        tx_from: 基盤がこの計画を知った時刻。

    Returns:
        様相 ``planned`` のアサーション。
    """
    return Assertion(
        subject=plan.target,
        predicate=plan.intended_fluent,
        object=True,
        valid_from=plan.planned_interval_start,
        valid_to=plan.planned_interval_end,
        tx_from=tx_from,
        modality=Modality.PLANNED,
        belief=BooleanBelief(p=plan.adherence_prior),
        provenance=site_iri(site_id, "prov", f"plan-{plan.plan_item_id}"),
        ontology_version=plan.ontology_version,
    )


@dataclass(frozen=True, slots=True)
class NormRule:
    """規範からコンパイルされた規則（設計書 §7.4）。

    規則本体は ``ontology/norms/*.lp`` に置き、ここはその参照と出典を結ぶ。規則文字列を
    Python 側で組み立てない（reasoning ルール: テンプレートは ``.lp`` 側に置く）。

    Attributes:
        norm: 元になった規範。
        rule_name: ``.lp`` 内の規則名（``norm_violation/3`` など）。
        confidence_propagation: 確信度の伝播方法。規則ごとに宣言する（reasoning ルール）。
    """

    norm: Norm
    rule_name: str
    confidence_propagation: str = "min"

    @property
    def severity_weight(self) -> float:
        """深刻度計算に用いる重み。"""
        return self.norm.severity.weight

    @property
    def requires_human_approval(self) -> bool:
        """違反時に人の承認を必須にするか（``critical`` のみ）。"""
        return self.norm.severity is NormSeverity.CRITICAL

    def citation(self) -> str:
        """出典の表示文字列（説明サービスが提示する）。"""
        return f"{self.norm.source_document}#{self.norm.source_clause}"


def _parse(value: object) -> datetime:
    """ISO 文字列または datetime を UTC datetime にする。"""
    if isinstance(value, datetime):
        return ensure_utc(value)
    return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
