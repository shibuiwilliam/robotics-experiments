"""コネクタ（設計書 §7.1、integration ルール）。

1 コネクタ 1 ファイルを原則とし、共通 IF は :class:`Connector` Protocol（``pull(since)`` と
``stream()``）。実システム接続は設定でのみ有効化し、開発では
:mod:`sbp.integration.mock` の台本付きモックを使う。

取り込んだ生レコードは :class:`~sbp.core.records.Record` として保持し、BusinessEvent に
変換する。``is_about`` の解決は :mod:`sbp.integration.resolver` の責務で、未解決は
``UnresolvedRef`` として残す（捨てない）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from datetime import datetime

__all__ = ["Connector", "RawRecord", "SchemaFingerprint"]


@dataclass(frozen=True, slots=True)
class RawRecord:
    """業務システムから取り出したままの 1 行。

    写像（RML）を通す前の生データ。``source`` と ``table`` で写像を選び、``fields`` を
    オントロジーの語彙へ移す。

    Attributes:
        source: 情報源 IRI（``ent:CmmsSource`` など）。
        table: 業務システム側のテーブル・エンティティ名。
        external_id: 業務システム側の主キー（文字列化したもの）。
        fields: 列名 → 値。型は業務システム依存のまま保持し、写像で解釈する。
        fetched_at: 取得時刻。
    """

    source: str
    table: str
    external_id: str
    fields: dict[str, Any]
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class SchemaFingerprint:
    """業務スキーマの指紋（設計書 §7.2、M1-04）。

    起動時にこれを比較して業務側のスキーマ変更を検知する。変更は例外ではなく
    ``MappingDriftDetected`` イベントとして出す（取り込みを止めずに人へ知らせる）。

    Attributes:
        source: 情報源 IRI。
        table: テーブル名。
        columns: 列名と型名の組（順序は正規化のためソート済みであること）。
    """

    source: str
    table: str
    columns: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def digest(self) -> str:
        """指紋のハッシュ。比較と記録に使う。"""
        payload = f"{self.source}|{self.table}|" + ",".join(f"{n}:{t}" for n, t in self.columns)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def diff(self, other: SchemaFingerprint) -> dict[str, list[str]]:
        """他の指紋との差分を返す。

        Args:
            other: 比較対象（通常は前回起動時の指紋）。

        Returns:
            ``added`` / ``removed`` / ``retyped`` のそれぞれに列名のリスト。
        """
        mine = dict(self.columns)
        theirs = dict(other.columns)
        return {
            "added": sorted(mine.keys() - theirs.keys()),
            "removed": sorted(theirs.keys() - mine.keys()),
            "retyped": sorted(
                name for name in mine.keys() & theirs.keys() if mine[name] != theirs[name]
            ),
        }


@runtime_checkable
class Connector(Protocol):
    """業務システムからの取り込みの抽象（設計書 §7.1）。

    ``pull`` は差分取り込み（CDC・API ポーリング）、``stream`` は継続取り込みに対応する。
    どちらも :class:`RawRecord` を返し、写像より手前で止まる（意味づけはこの層の外）。
    """

    @property
    def source(self) -> str:
        """この コネクタが読む情報源の IRI。"""
        ...

    async def fingerprints(self) -> Sequence[SchemaFingerprint]:
        """現在のスキーマ指紋を返す（起動時のドリフト検知に使う）。"""
        ...

    async def pull(self, since: datetime | None = None) -> Sequence[RawRecord]:
        """``since`` 以降に変更された行を取り出す。

        Args:
            since: この時刻より後に更新された行だけを返す。``None`` なら全件。
        """
        ...

    def stream(self) -> AsyncIterator[RawRecord]:
        """変更を継続的に受け取る。

        Returns:
            :class:`RawRecord` の非同期イテレータ。
        """
        ...
