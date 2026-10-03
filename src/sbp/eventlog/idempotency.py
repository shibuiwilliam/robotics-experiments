"""冪等化（不変条件 4、設計書 §6.1）。

Consumer は at-least-once。副作用を起こす前に ``processed_events`` を見て ``event_id`` の
重複を弾く（knowledge ルール）。

「先に記録してから副作用」ではなく「**副作用と記録を同一トランザクションに入れる**」のが
正しい形だが、副作用が外部システム（Oxigraph への書き込み）に及ぶため厳密な 2 相コミットは
行わない。代わりに次の順序を守る。

1. ``mark_processing`` で ``event_id`` を挿入（重複なら ``False`` が返り、処理を飛ばす）
2. 副作用を実行
3. 失敗したら ``unmark`` で記録を取り消し、再配送で再試行できるようにする

この順序だと「記録済みだが副作用が未完了」の窓が残る。それを許容できるのは、上位の副作用
（アサーションの追記）自体が ``event_id`` を来歴に持ち、同じイベントからの二重追記を
知識層側で検出できるからである。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from psycopg import AsyncConnection

__all__ = ["IdempotencyStore", "InMemoryIdempotencyStore", "PostgresIdempotencyStore"]

log = get_logger(__name__)


@runtime_checkable
class IdempotencyStore(Protocol):
    """``event_id`` による重複排除の抽象。"""

    async def mark_processing(
        self, *, event_id: UUID, consumer_group: str, topic: str, subject: str
    ) -> bool:
        """処理開始を記録する。

        Args:
            event_id: イベントの UUIDv7。
            consumer_group: consumer グループ名（同じイベントを別用途で処理できるように分ける）。
            topic: 取得元トピック。
            subject: 主体実体 IRI。

        Returns:
            新規なら ``True``、既に処理済みなら ``False``（呼び出し側は処理を飛ばす）。
        """
        ...

    async def unmark(self, *, event_id: UUID, consumer_group: str) -> None:
        """処理記録を取り消す（副作用が失敗したとき）。

        Args:
            event_id: イベントの UUIDv7。
            consumer_group: consumer グループ名。
        """
        ...


class InMemoryIdempotencyStore:
    """テスト用の冪等ストア。プロセス内の集合だけを使う。"""

    def __init__(self) -> None:
        self._seen: set[tuple[str, UUID]] = set()

    async def mark_processing(
        self, *, event_id: UUID, consumer_group: str, topic: str, subject: str
    ) -> bool:
        """処理開始を記録する（引数 ``topic`` / ``subject`` は実装との IF 統一のために取る）。"""
        del topic, subject
        key = (consumer_group, event_id)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    async def unmark(self, *, event_id: UUID, consumer_group: str) -> None:
        """処理記録を取り消す。"""
        self._seen.discard((consumer_group, event_id))

    def __len__(self) -> int:
        """記録済みイベント数。"""
        return len(self._seen)


class PostgresIdempotencyStore:
    """``processed_events`` 表を使う冪等ストア。

    挿入は ``ON CONFLICT DO NOTHING`` で行い、挿入行数で新規かどうかを判定する。
    判定と挿入を 1 文で済ませるため、同時実行でも二重処理にならない。
    """

    def __init__(self, connection: AsyncConnection[object]) -> None:
        """Args:
        connection: psycopg の非同期接続。トランザクション管理は呼び出し側の責務。
        """
        self._connection = connection

    async def mark_processing(
        self, *, event_id: UUID, consumer_group: str, topic: str, subject: str
    ) -> bool:
        """処理開始を記録する。既に処理済みなら ``False``。"""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO processed_events (event_id, consumer_group, topic, subject)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (consumer_group, event_id) DO NOTHING
                """,
                (event_id, consumer_group, topic, subject),
            )
            inserted = cursor.rowcount == 1
        if not inserted:
            log.debug(
                "idempotency.duplicate",
                event_id=str(event_id),
                consumer_group=consumer_group,
                entity=subject,
            )
        return inserted

    async def unmark(self, *, event_id: UUID, consumer_group: str) -> None:
        """処理記録を取り消す（副作用が失敗したとき）。"""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM processed_events WHERE consumer_group = %s AND event_id = %s",
                (consumer_group, event_id),
            )
