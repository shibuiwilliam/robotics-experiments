"""業務記録の取り込み経路（設計書 §2.5 の「記録パス」、§7.3）。

``RawRecord → 写像 → is_about 解決 → BusinessEvent`` を 1 本につなぐ。この層は
**イベントを出すところまで**で、信念への反映は ``reasoning`` の責務（層の依存の向き）。

生成するイベントの様相は ``inferred`` ではなく ``observed`` でもない。業務記録は
「業務システムがそう主張している」という事実であり、それ自体は観測でも推定でもない。
設計書 §3.5 の様相格子に照らすと、記録由来の主張は**信念更新の入力（尤度）**であって
信念そのものではない。したがってイベントの様相は ``observed``（記録が存在するという事実は
観測されている）とし、そこから導かれる状態の信念は ``reasoning`` が ``inferred`` で出す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sbp.core.belief import BooleanBelief
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    Producer,
    RecordEvidence,
    ValidTime,
    topic_name,
)
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.records import Record, UnresolvedRef
from sbp.integration.resolver import resolve_subject

if TYPE_CHECKING:
    from sbp.integration.connectors import Connector, RawRecord, SchemaFingerprint
    from sbp.integration.mappings import MappingRegistry
    from sbp.integration.reliability import ReliabilityRegistry
    from sbp.integration.resolver import EntityResolver

__all__ = ["IngestResult", "IngestionPipeline", "MappingDriftDetected"]

log = get_logger(__name__)

_PRODUCER_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class MappingDriftDetected:
    """業務スキーマの変更検知（設計書 §7.2、integration ルール）。

    例外ではなくイベントとして出す。取り込みを止めると業務側の変更で基盤が停止することに
    なり、「業務システムは system of record のまま」という原則に反する。知らせて走り続ける。

    Attributes:
        source: 情報源 IRI。
        table: テーブル名。
        previous_digest: 前回の指紋。
        current_digest: 今回の指紋。
        diff: 追加・削除・型変更された列。
    """

    source: str
    table: str
    previous_digest: str
    current_digest: str
    diff: dict[str, list[str]]

    @property
    def is_breaking(self) -> bool:
        """写像を壊す変更かどうか。

        列の追加だけなら写像は壊れない（宣言した列が残っているため）。削除と型変更は
        写像の版を上げる必要がある。
        """
        return bool(self.diff.get("removed") or self.diff.get("retyped"))


@dataclass(slots=True)
class IngestResult:
    """取り込み 1 回の結果。

    Attributes:
        records: 写像と解決を通った記録。
        events: 発行すべきイベント。
        unresolved: 解決できなかった参照（捨てずに保持する）。
        drift: 検知したスキーマ変更。
        failed: 写像に失敗した行（理由付き）。
    """

    records: list[Record] = field(default_factory=list)
    events: list[tuple[str, EventEnvelope]] = field(default_factory=list)
    unresolved: list[UnresolvedRef] = field(default_factory=list)
    drift: list[MappingDriftDetected] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        """``is_about`` が解決できた記録の数。"""
        return sum(1 for r in self.records if r.is_resolved)


class IngestionPipeline:
    """取り込み経路。写像・解決・信頼性モデルを束ねる。

    ``is_about`` が未解決の記録も**イベントとして出す**。未解決であること自体が情報であり
    （業務が知っていて物理が知らない可能性）、存在不一致の検出材料になる。
    """

    def __init__(
        self,
        *,
        site_id: str,
        mappings: MappingRegistry,
        resolver: EntityResolver,
        reliability: ReliabilityRegistry,
        ontology_version: str,
    ) -> None:
        """Args:
        site_id: 拠点識別子（トピック名と IRI に使う）。
        mappings: 写像レジストリ。
        resolver: ``is_about`` の解決器。
        reliability: 信頼性モデルのレジストリ。
        ontology_version: 取り込み時のオントロジー版。
        """
        self._site_id = site_id
        self._mappings = mappings
        self._resolver = resolver
        self._reliability = reliability
        self._ontology_version = ontology_version
        self._fingerprints: dict[tuple[str, str], str] = {}

    async def check_drift(self, connector: Connector) -> list[MappingDriftDetected]:
        """スキーマ指紋を比較して変更を検知する（起動時・取り込み前）。

        Args:
            connector: 検査するコネクタ。

        Returns:
            検知した変更のリスト（初回は空）。
        """
        detected: list[MappingDriftDetected] = []
        fingerprints: tuple[SchemaFingerprint, ...] = tuple(await connector.fingerprints())
        for fingerprint in fingerprints:
            key = (fingerprint.source, fingerprint.table)
            current = fingerprint.digest()
            previous = self._fingerprints.get(key)
            if previous is not None and previous != current:
                # 差分を出すために前回の列構成が必要。指紋だけでは差分が取れないため、
                # ここでは digest の変化を報告し、列単位の差分は呼び出し側が
                # 2 つの SchemaFingerprint を渡して diff() を取る。
                detected.append(
                    MappingDriftDetected(
                        source=fingerprint.source,
                        table=fingerprint.table,
                        previous_digest=previous,
                        current_digest=current,
                        diff={"added": [], "removed": [], "retyped": []},
                    )
                )
                log.warning(
                    "integration.mapping_drift",
                    source=fingerprint.source,
                    table=fingerprint.table,
                    previous=previous,
                    current=current,
                )
            self._fingerprints[key] = current
        return detected

    def ingest_rows(self, rows: list[RawRecord]) -> IngestResult:
        """生の行を写像・解決し、イベントまで作る。

        Args:
            rows: 取り込んだ行。

        Returns:
            取り込み結果。写像に失敗した行は ``failed`` に入り、他の行の処理は止めない
            （1 行の誤りで取り込み全体を止めない）。
        """
        result = IngestResult()
        for raw in rows:
            try:
                mapping = self._mappings.for_table(raw.table)
                mapped = mapping.apply(raw, ontology_version=self._ontology_version)
            except Exception as exc:
                result.failed.append((raw.external_id, str(exc)))
                log.warning(
                    "integration.mapping_failed", external_id=raw.external_id, error=str(exc)
                )
                continue

            outcome = resolve_subject(mapped, self._resolver)
            record = outcome.record
            result.records.append(record)
            if outcome.unresolved is not None:
                result.unresolved.append(outcome.unresolved)

            # 信頼性モデルを引いて記録に紐づける（未登録なら例外。既定で信頼しない）
            model = self._reliability.for_record(record)
            record = record.model_copy(
                update={"reliability_profile": _reliability_iri(model.source, model.record_type)}
            )
            result.records[-1] = record

            topic = topic_name(self._site_id, EventLayer.INTEGRATION, "business")
            result.events.append((topic, self._to_event(record, mapped.mapping_version)))
        log.info(
            "integration.ingested",
            rows=len(rows),
            records=len(result.records),
            resolved=result.resolved_count,
            unresolved=len(result.unresolved),
            failed=len(result.failed),
        )
        return result

    def _to_event(self, record: Record, mapping_version: str) -> EventEnvelope:
        """記録を業務事象のイベントに載せる。

        様相は ``observed``: 「この記録が存在する」ことは観測された事実である。記録が主張する
        状態が真かどうかは別問題で、それは ``reasoning`` が信頼性モデルを使って
        ``inferred`` の信念に変える。

        適用した写像の版を producer に残す。業務スキーマ変更で写像を上げたとき、どの版で
        作られたイベントかを後から辿れないと再接地の範囲が決められない（設計書 §12.4）。
        """
        occurred = record.occurred_at or record.recorded_at
        return EventEnvelope(
            type=record.record_type,
            subject=record.is_about or _unresolved_subject(record),
            valid_time=ValidTime(start=occurred, uncertainty_s=0.0),
            tx_time=record.recorded_at,
            modality=Modality.OBSERVED,
            belief=BooleanBelief(p=1.0),  # 記録が存在することは確実
            evidence=(
                RecordEvidence(
                    record=_record_iri(self._site_id, record),
                    source=record.source,
                    source_reliability=record.reliability_profile,
                ),
            ),
            producer=Producer(
                agent=f"integration@{self._site_id}",
                version=f"{_PRODUCER_VERSION}+mapping-{mapping_version}",
            ),
            ontology_version=record.ontology_version,
        )


def _record_iri(site_id: str, record: Record) -> str:
    """記録の IRI。"""
    from sbp.core.ids import site_iri  # noqa: PLC0415 — 循環 import 回避

    return site_iri(site_id, "record", str(record.record_id))


def _unresolved_subject(record: Record) -> str:
    """``is_about`` 未解決の記録の主体 IRI。

    記録自身を主体にする。「対象が分からない記録がある」という事実を実体単位の順序に
    乗せるため、記録ごとに別の主体になる（未解決の記録同士で順序を仮定しない）。
    """
    return f"urn:sbp:unresolved:{record.record_id}"


def _reliability_iri(source: str, record_type: str) -> str:
    """信頼性モデルの IRI（来歴から辿れるようにする）。"""
    source_local = source.rsplit("#", 1)[-1]
    type_local = record_type.rsplit("#", 1)[-1]
    return f"urn:sbp:reliability:{source_local}:{type_local}"
