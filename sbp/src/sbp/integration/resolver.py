"""``is_about`` の対象解決（設計書 §3.3、integration ルール）。

業務側の参照文字列（資産タグ ``P-101``、SKU ``SKU-77``）を基盤側の安定 IRI に解決する。
**解決できない参照は捨てず** :class:`~sbp.core.records.UnresolvedRef` として残す。

捨てないのが重要な理由: 解決できないのは (a) マスタ同期の遅れ、(b) 業務側だけが知っている
実体、(c) 誤記 のいずれかであり、どれであるかは後から判明しうる。記録ごと捨てると、
後で分かったときに遡れない。(b) は「業務が知っていて物理が知らない」ケースで、存在不一致
（類型 1）の片側そのものである。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sbp.core.ids import site_iri
from sbp.core.logging import get_logger
from sbp.core.records import Record, UnresolvedRef
from sbp.core.time import now

if TYPE_CHECKING:
    from sbp.integration.mappings import MappingResult, SubjectReference

__all__ = ["EntityResolver", "MasterDataResolver", "ResolutionOutcome"]

log = get_logger(__name__)

_KIND_TO_PATH: dict[str, str] = {
    "asset": "asset",
    "material": "material",
    "location": "location",
}


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    """解決の結果。

    Attributes:
        record: ``is_about`` を埋めた（または埋められなかった）記録。
        unresolved: 解決できなかった参照。解決できたなら ``None``。
    """

    record: Record
    unresolved: UnresolvedRef | None = None

    @property
    def is_resolved(self) -> bool:
        """解決できたかどうか。"""
        return self.unresolved is None


@runtime_checkable
class EntityResolver(Protocol):
    """参照文字列 → 実体 IRI の解決の抽象。"""

    def resolve(self, raw_reference: str, kind: str) -> str | None:
        """参照を実体 IRI に解決する。

        Args:
            raw_reference: 業務側の参照文字列（資産タグなど）。
            kind: 参照の種別（``asset`` / ``material`` / ``location``）。

        Returns:
            解決できた実体 IRI、できなければ ``None``。
        """
        ...


class MasterDataResolver:
    """マスタデータ（既知の実体の集合）に基づく解決器。

    業務 ID から安定 IRI を導く（設計書 §3.12）。マスタに無い参照は解決しない——
    IRI を機械的に組み立てれば「必ず解決できる」が、それは**存在しない実体を発明する**ことに
    なる。業務側だけが知っている実体は不一致として扱うべきで、勝手に作ってはいけない。
    """

    def __init__(self, *, site_id: str, known: dict[str, frozenset[str]] | None = None) -> None:
        """Args:
        site_id: 拠点識別子（IRI の構成要素）。
        known: 種別 → 既知の業務 ID の集合。``None`` なら空（すべて未解決になる）。
        """
        self._site_id = site_id
        self._known: dict[str, set[str]] = {kind: set(ids) for kind, ids in (known or {}).items()}

    def register(self, kind: str, business_id: str) -> str:
        """マスタに実体を登録して IRI を返す（マスタ同期の代わり）。

        Args:
            kind: 種別（``asset`` / ``material`` / ``location``）。
            business_id: 業務側の識別子。

        Returns:
            登録した実体の IRI。
        """
        if kind not in _KIND_TO_PATH:
            raise ValueError(f"unknown entity kind: {kind!r}; known: {sorted(_KIND_TO_PATH)}")
        self._known.setdefault(kind, set()).add(business_id)
        return self.iri_for(kind, business_id)

    def iri_for(self, kind: str, business_id: str) -> str:
        """業務 ID から安定 IRI を組む（登録の有無を問わない）。

        Args:
            kind: 種別。
            business_id: 業務側の識別子。
        """
        return site_iri(self._site_id, _KIND_TO_PATH[kind], business_id)

    def resolve(self, raw_reference: str, kind: str) -> str | None:
        """参照を実体 IRI に解決する。マスタに無ければ ``None``。"""
        if kind not in _KIND_TO_PATH:
            return None
        if raw_reference not in self._known.get(kind, set()):
            return None
        return self.iri_for(kind, raw_reference)


def resolve_subject(result: MappingResult, resolver: EntityResolver) -> ResolutionOutcome:
    """写像結果の ``is_about`` を解決する。

    解決できなければ記録はそのまま（``is_about=None``）返し、:class:`UnresolvedRef` を併せて
    返す。呼び出し側は両方を保持する（記録を捨てない）。

    Args:
        result: 写像の結果。
        resolver: 解決器。

    Returns:
        解決の結果。
    """
    ref: SubjectReference | None = result.subject_ref
    if ref is None or result.raw_reference is None:
        # 参照を持たない記録（集計行など）。未解決ではなく「対象なし」。
        return ResolutionOutcome(record=result.record)

    iri = resolver.resolve(result.raw_reference, ref.kind)
    if iri is None:
        log.info(
            "resolver.unresolved",
            entity=result.raw_reference,
            kind=ref.kind,
            record_type=result.record.record_type,
            external_id=result.record.external_id,
        )
        return ResolutionOutcome(
            record=result.record,
            unresolved=UnresolvedRef(
                record_id=result.record.record_id,
                raw_reference=result.raw_reference,
                reference_field=ref.column,
                reason="not_found",
                attempted_at=now(),
            ),
        )
    return ResolutionOutcome(record=result.record.model_copy(update={"is_about": iri}))
