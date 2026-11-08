"""資産台帳の同期（ADR-0016、設計書 §7.1 のマスタ「定期同期」）。

台帳の行（``ent:AssetRegisterEntry`` の記録）を、設備の型（``rdf:type``）と部分−全体
（``sbp:partOf``）の **observed のアサーション**にし、現在有効な分を投影グラフに作り直す。
台帳は小さいので、同期のたびに**全行**を受け取り、現在の台帳の文と突き合わせる——差分の取込
（``since``）では、台帳から消えた行が分からない。

- 新しい文は追記する。
- 台帳から消えた・変わった文は ``tx_to`` を閉じる（消さない。不変条件 2）。as-of で旧い台帳を
  読める。
- 同じ台帳を同期し直しても何も増えない（取込ワーカーは周期で回す）。

**推測しない。** 台帳の型名がオントロジーに無い行は登録しない（綴り違いの型を登録すると、どの型の
問い合わせにも当たらない設備が静かにできる）。理由を ``skipped`` に返す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.ids import site_iri
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.integration.ingest import Rejection
from sbp.knowledge import RDF_TYPE, rebuild_master_projection

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime
    from uuid import UUID

    from sbp.core.records import Record
    from sbp.knowledge import AssertionStore, GraphStore

__all__ = ["MasterDataSync", "MasterSyncReport"]

log = get_logger(__name__)

MASTER_REGISTER_TABLE = "asset_register"
"""台帳の表名（隔離キューの「入口」に使う。``services.ingest_worker.MASTER_TABLE`` と同じ値）。"""

type Statement = tuple[str, str, str]
"""``(主語 IRI, 述語 IRI, 目的語 IRI)``。"""

_NOT_ASSET_TYPES = vocab.FLUENT_TYPES | vocab.EVENT_TYPES


@dataclass(slots=True)
class MasterSyncReport:
    """同期 1 回の結果。

    Attributes:
        asserted: 追記した文の数。
        retracted: ``tx_to`` を閉じた文の数。
        unchanged: そのまま残った文の数。
        skipped: 登録しなかった行（外部 ID と理由）。
        rejected: ``skipped`` の行を生の入力ごと持つもの（隔離キューへ渡す。ADR-0035）。
        held: 型が無いため文を作らず、直前の文を閉じなかった資産タグ。
        entities: 台帳に載っている資産タグ（解決器に登録する）。
    """

    asserted: int = 0
    retracted: int = 0
    unchanged: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    entities: frozenset[str] = frozenset()


class MasterDataSync:
    """資産台帳の文を追記し、投影を作り直す。"""

    def __init__(self, store: AssertionStore, graph: GraphStore, *, site_id: str) -> None:
        """Args:
        store: 二時制アサーション API（台帳の文を書く）。
        graph: グラフストア（投影を書く。``store`` と同じストア）。
        site_id: 拠点識別子。
        """
        self._store = store
        self._graph = graph
        self._site_id = site_id
        self.provenance = site_iri(site_id, "prov", "asset-register")
        """台帳の文の来歴。現在の台帳の文はこれで見分ける。"""

    def sync(
        self,
        records: Sequence[Record],
        *,
        at: datetime,
        hold: Collection[str] = (),
    ) -> MasterSyncReport:
        """台帳の全行を同期する。

        Args:
            records: ``asset_register`` 写像を通った記録（**全行**）。
            at: 同期時刻（新しい文の ``tx_from`` と、閉じる文の ``tx_to``）。
            hold: 閉じない資産タグ（今回の読み出しで写像に失敗した行。壊れた編集で直前の有効な文を
                失わない。隔離に積まれ、人が確認する）。

        Returns:
            同期の結果。
        """
        report = MasterSyncReport()
        desired: dict[Statement, Record] = {}
        tags: set[str] = set()
        for record in records:
            statements = self._statements_of(record, report)
            if statements is None:
                continue
            tags.add(record.external_id)
            for statement in statements:
                desired[statement] = record

        current = self._current_statements()
        held = {site_iri(self._site_id, "asset", tag) for tag in (*hold, *report.held)}
        for statement, assertion_id in current.items():
            if statement not in desired and statement[0] not in held:
                self._store.retract(assertion_id, at)
                report.retracted += 1
        for statement, record in desired.items():
            if statement in current:
                report.unchanged += 1
                continue
            self._store.assert_(self._assertion(statement, record, at=at))
            report.asserted += 1

        rebuild_master_projection(self._graph, site_id=self._site_id, triples=sorted(desired))
        report.entities = frozenset(tags)
        log.info(
            "integration.master_synced",
            site=self._site_id,
            asserted=report.asserted,
            retracted=report.retracted,
            unchanged=report.unchanged,
            skipped=len(report.skipped),
        )
        return report

    def _statements_of(self, record: Record, report: MasterSyncReport) -> list[Statement] | None:
        """台帳の 1 行が主張する文。登録できない行は ``None`` を返し、理由を ``report`` に積む。"""
        asset_type = str(record.payload.get("asset_type") or "")
        type_iri = getattr(vocab.ent, asset_type, None) if asset_type else None
        if not isinstance(type_iri, str) or type_iri in _NOT_ASSET_TYPES:
            reason = f"asset_type {asset_type!r} is not a type in the ontology"
            report.skipped.append((record.external_id, reason))
            report.held.append(record.external_id)
            report.rejected.append(
                Rejection(
                    source=str(record.source),
                    table=MASTER_REGISTER_TABLE,
                    subject=record.external_id,
                    reason=reason,
                    payload={"fields": dict(record.payload)},
                )
            )
            log.warning(
                "integration.master_row_skipped", external_id=record.external_id, reason=reason
            )
            return None
        subject = site_iri(self._site_id, "asset", record.external_id)
        statements: list[Statement] = [(subject, RDF_TYPE, type_iri)]
        whole = record.payload.get("part_of")
        if whole:
            whole_iri = site_iri(self._site_id, "asset", str(whole))
            statements.append((subject, vocab.core.partOf, whole_iri))
        return statements

    def _current_statements(self) -> dict[Statement, UUID]:
        """いま有効な台帳の文 → アサーション ID。台帳の来歴を持つ文だけを見る。"""
        current: dict[Statement, UUID] = {}
        for predicate in (RDF_TYPE, vocab.core.partOf):
            for row in self._store.query(predicate=predicate, modality=Modality.OBSERVED):
                if row.provenance == self.provenance:
                    current[(row.subject, row.predicate, row.object_)] = row.assertion_id
        return current

    def _assertion(self, statement: Statement, record: Record, *, at: datetime) -> Assertion:
        """台帳の文 1 件のアサーション。

        確信度は 1.0 にする——台帳の信頼性モデルは混同行列を持たず（型の誤記を割り引く根拠が
        無い）、台帳は信念更新の尤度にも入れない。有効開始は台帳に登録された時刻。
        """
        subject, predicate, obj = statement
        return Assertion(
            subject=subject,
            predicate=predicate,
            object=obj,
            valid_from=record.occurred_at or record.recorded_at,
            tx_from=at,
            modality=Modality.OBSERVED,
            belief=BooleanBelief(p=1.0),
            provenance=self.provenance,
            ontology_version=record.ontology_version,
        )
