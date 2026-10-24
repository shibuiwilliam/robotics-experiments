"""計画・規範の取り込み（設計書 §7.4、integration ルール）。

- **PlanItem → 様相 planned のアサーション。** 有効時間は計画区間、確信度は当該計画種の
  **遵守率の事前分布**。計画は守られないことがあるという前提を数値で持つのが要点である。
- **Norm → Datalog/ASP 規則 + 出典。** 違反判定の結果は様相 normative のアサーションになる。
  出典（文書・節）を必ず持つ（規範違反は人の判断に影響するため、根拠まで遡れなければ
  監査に使えない）。

計画の同期（:class:`PlanSync`）は ``knowledge`` の公開 API（``AssertionStore``）で planned の文を
追記・閉鎖する（ADR-0016）。層の依存の向きは integration → knowledge（import-linter の契約）で、
2026-10-25 まで本 docstring は逆向き（integration が knowledge の下位）と書いていた。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal
from uuid import NAMESPACE_URL, uuid5

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.ids import site_iri
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.records import Norm, NormSeverity, PlanItem, Record
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from sbp.knowledge import AssertionRow, AssertionStore

__all__ = [
    "ADHERENCE_PRIORS",
    "INTENDED_FLUENTS",
    "NormRule",
    "PlanSync",
    "intended_fluent_for",
    "plan_item_from_record",
    "plan_provenance",
    "to_planned_assertion",
]

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

# 計画種ごとに、計画が成立させようとするフルーエント（ADR-0016）。遵守率の表の隣に置くのは、
# 計画種を足すときに両方を同じ場所で決めるため。**未知の計画種は推測しない**——「何を成立させる
# 計画か」を取り違えると、planned の文が別のフルーエントの事前として信念更新に効く。
INTENDED_FLUENTS: Final[dict[str, str]] = {
    "PlannedOperation": vocab.ent.Running,
    "PreventiveMaintenance": vocab.ent.UnderMaintenance,
    "Inspection": vocab.ent.Inspected,
}


def intended_fluent_for(record: Record) -> str | None:
    """計画の記録が成立させようとするフルーエント型 IRI。未知の計画種なら ``None``。

    Args:
        record: ``maintenance_plan`` 写像を通った記録。
    """
    plan_type = str(record.payload.get("plan_type") or "")
    fluent = INTENDED_FLUENTS.get(plan_type)
    if fluent is None:
        log.info("plans.no_intended_fluent", plan_type=plan_type, external_id=record.external_id)
    return fluent


def plan_provenance(site_id: str, external_id: str) -> str:
    """計画の文の来歴 IRI。業務側の計画 ID から導くので、同じ計画は同じ来歴になる。

    計画が変わったとき、旧い planned の文をこの来歴で見つけて閉じる（:class:`PlanSync`）。
    計画 ID は IRI に使えない文字を含みうるので、そのまま埋めずに uuid5 にする。

    Args:
        site_id: 拠点識別子。
        external_id: 業務システム側の計画 ID。
    """
    return site_iri(site_id, "prov", f"plan-{uuid5(NAMESPACE_URL, external_id).hex}")


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
        provenance=plan_provenance(site_id, plan.external_id),
        ontology_version=plan.ontology_version,
    )


type PlanSyncOutcome = Literal["asserted", "replaced", "unchanged"]
"""計画 1 件の同期の結果。"""


class PlanSync:
    """計画を planned の文として同期する（ADR-0016）。

    取込は周期で回り、同じ計画を何度も読む。**同じ計画は何も増やさず**、計画が変わったら旧文の
    ``tx_to`` を閉じて新文を足す（上書きしない。不変条件 2）。旧文は計画の来歴
    （:func:`plan_provenance`）で見分ける。
    """

    def __init__(self, store: AssertionStore, *, site_id: str) -> None:
        """Args:
        store: 二時制アサーション API。
        site_id: 拠点識別子。
        """
        self._store = store
        self._site_id = site_id

    def sync(self, plan: PlanItem, *, at: datetime) -> PlanSyncOutcome:
        """計画 1 件を同期する。

        Args:
            plan: 計画項目（対象の実体が解決済みであること。未解決の計画は呼び出し側が飛ばす）。
            at: 同期時刻（新しい文の ``tx_from`` と、閉じる文の ``tx_to``）。

        Returns:
            ``asserted``（初めて）/ ``replaced``（変わった）/ ``unchanged``（同じ）。
        """
        wanted = to_planned_assertion(plan, site_id=self._site_id, tx_from=at)
        provenance = plan_provenance(self._site_id, plan.external_id)
        current = [
            row
            for row in self._store.query(subject=plan.target, modality=Modality.PLANNED)
            if row.provenance == provenance
        ]
        same = [row for row in current if _same_plan(row, wanted)]
        if same and len(current) == len(same):
            return "unchanged"
        for row in current:
            if not _same_plan(row, wanted):
                self._store.retract(row.assertion_id, at)
        if not same:
            self._store.assert_(wanted)
        outcome: PlanSyncOutcome = "replaced" if current else "asserted"
        log.info(
            "plans.synced",
            external_id=plan.external_id,
            entity=plan.target,
            outcome=outcome,
        )
        return outcome


def _same_plan(row: AssertionRow, wanted: Assertion) -> bool:
    """同じ計画の文か（フルーエント・計画区間・遵守率が一致）。"""
    confidence = wanted.belief.p if isinstance(wanted.belief, BooleanBelief) else None
    return (
        row.predicate == wanted.predicate
        and row.valid_from == wanted.valid_from
        and row.valid_to == wanted.valid_to
        and confidence is not None
        and abs(row.confidence - confidence) < 1e-9
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
