"""識別子。イベント ID は UUIDv7（時刻順序性）、実体 ID は安定 IRI（設計書 §3.12）。

不変条件 4 により全イベントは UUIDv7 の ``event_id`` を持つ。UUIDv7 は先頭 48 bit が
Unix ミリ秒であるため、文字列順＝おおよその生成時刻順になり、ログの走査やパーティション
内の整列に都合がよい（ただしグローバル順序は仮定しない）。
"""

from __future__ import annotations

import re
from typing import Annotated, Final
from uuid import UUID

from pydantic import AfterValidator
from uuid6 import uuid7

from sbp.core.errors import VocabularyError

__all__ = [
    "EventId",
    "Iri",
    "event_id_timestamp_ms",
    "new_event_id",
    "site_iri",
    "validate_iri",
]


def new_event_id() -> UUID:
    """新しい UUIDv7 を返す（時刻順序性を持つ）。"""
    return uuid7()


def event_id_timestamp_ms(event_id: UUID) -> int:
    """UUIDv7 に埋め込まれた Unix ミリ秒を取り出す。

    Args:
        event_id: UUIDv7。

    Raises:
        ValueError: バージョンが 7 でない場合。
    """
    if event_id.version != 7:
        raise ValueError(f"expected UUIDv7, got version {event_id.version}")
    return event_id.int >> 80


def _check_event_id(value: UUID) -> UUID:
    if value.version != 7:
        raise ValueError(f"event_id must be UUIDv7 (invariant 4), got version {value.version}")
    return value


EventId = Annotated[UUID, AfterValidator(_check_event_id)]
"""UUIDv7 であることを検証するイベント ID 型。"""


# IRI は RFC 3987 の完全な文法までは検査せず、SBP が実際に用いる形
# （scheme:// または urn: で始まり、空白と山括弧を含まない）に限って検証する。
_IRI_PATTERN: Final = re.compile(
    r"^(?:[a-z][a-z0-9+.\-]*:)(?://)?[^\s<>\"{}|\\^`]+$", re.IGNORECASE
)


def validate_iri(value: str) -> str:
    """IRI として妥当な文字列か検証して返す。

    Args:
        value: 検証対象の文字列。

    Raises:
        VocabularyError: IRI の形をしていない場合。
    """
    if not _IRI_PATTERN.match(value):
        raise VocabularyError(
            f"not a valid IRI: {value!r}. "
            "IRIs must come from sbp.core.vocab (invariant 5), never literals in code."
        )
    return value


Iri = Annotated[str, AfterValidator(validate_iri)]
"""IRI 文字列型。コードへの直書きは不変条件 5 違反であり、``sbp.core.vocab`` 経由で参照する。"""


def site_iri(site_id: str, kind: str, local_id: str) -> str:
    """拠点個体の IRI を組み立てる（``https://sbp.example/site/<site>/<kind>/<local>``）。

    業務 ID から派生する安定 IRI を作るための唯一の経路。文字列連結で IRI を作らない。

    Args:
        site_id: 拠点識別子（例 ``siteA``）。
        kind: 実体の種別を表すパス要素（例 ``asset``, ``sit``, ``track``）。
        local_id: 拠点内で一意なローカル識別子（例 ``P-101``）。
    """
    for name, part in (("site_id", site_id), ("kind", kind), ("local_id", local_id)):
        if not part or "/" in part or any(c.isspace() for c in part):
            raise VocabularyError(f"invalid {name} for site IRI: {part!r}")
    return f"https://sbp.example/site/{site_id}/{kind}/{local_id}"
