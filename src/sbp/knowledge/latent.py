"""潜在索引（設計書 §6.5、M4-08）。

> テーブル ``latent_index(entity, ts, level, wm_version, vec vector(D))``、HNSW（cosine）。
> ``level`` と ``wm_version`` で絞ってから近傍検索する。

**書き込み先は登録表から引く。** 次元は WM 版ごとに固定で、別の次元の潜在を同じ表に入れること
はできない（pgvector の列は次元が固定）。表名を呼び出し側が指定する設計にすると、版を上げた
ときに古い表へ書き続ける事故が起きる。``latent_index_registry`` を引いてから書くことで、
**未登録の版では書き込みが成立しない**（黙って既定の表に入らない）。

**生のベクトルだけを持ち、生観測は持たない**（不変条件 9）。潜在は再構成しても元の映像・音声に
戻らない次元まで落ちている、という前提でこの表が成立している。

**近傍検索の前に ``level`` と ``wm_version`` で絞る。** 階層が違う潜在（fast と slow）や版が違う
潜在は、同じ空間にあるように見えて意味が違う。混ぜて近傍を取ると「似ている」が無意味になる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from psycopg import sql

from sbp.core.errors import KnowledgeError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from numpy.typing import NDArray
    from psycopg import AsyncConnection

__all__ = ["LatentIndex", "LatentNeighbour", "LatentRegistration"]

log = get_logger(__name__)

_DEFAULT_LIMIT = 10
"""近傍検索の既定件数。"""


@dataclass(frozen=True, slots=True)
class LatentRegistration:
    """WM 版 → 潜在索引の表と次元。

    Attributes:
        wm_version: モデル版 IRI。
        table_name: 書き込み先の表名。
        dim: 潜在次元。
    """

    wm_version: str
    table_name: str
    dim: int


@dataclass(frozen=True, slots=True)
class LatentNeighbour:
    """近傍検索の 1 件。

    Attributes:
        entity: 実体 IRI。
        at: 潜在の時刻。
        level: 階層。
        distance: コサイン距離（0 が同一方向）。
    """

    entity: str
    at: datetime
    level: str
    distance: float

    @property
    def similarity(self) -> float:
        """コサイン類似度（``1 - distance``）。"""
        return 1.0 - self.distance


class LatentIndex:
    """潜在索引への書き込みと近傍検索。

    SQL を直接書くのは、pgvector の演算子（``<=>``）と HNSW のヒント（``ef_search``）を
    使うためである。ORM を通すとこれらが抽象の下に隠れ、索引が効いているかどうかが
    分からなくなる。
    """

    def __init__(self, connection: AsyncConnection) -> None:
        """Args:
        connection: psycopg の非同期接続。トランザクション管理は呼び出し側の責務。
        """
        self._connection = connection

    async def register(self, *, wm_version: str, dim: int) -> LatentRegistration:
        """WM 版を次元に対応する表へ登録する。

        同じ版を違う次元で登録し直すことはできない。潜在の次元が変わるのは**別のモデル**
        であり、同じ版 IRI を名乗ってはならない（過去の潜在が読めなくなる）。

        Args:
            wm_version: モデル版 IRI。
            dim: 潜在次元。

        Returns:
            登録内容。

        Raises:
            KnowledgeError: 次元に対応する表が無い、または同じ版が別の次元で登録済みの場合。
        """
        table = await self._table_for_dim(dim)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT table_name, dim FROM latent_index_registry WHERE wm_version = %s",
                (wm_version,),
            )
            row = await cursor.fetchone()
            if row is not None:
                existing_table, existing_dim = str(row[0]), int(row[1])
                if existing_dim != dim:
                    raise KnowledgeError(
                        f"{wm_version} is already registered with dim {existing_dim} "
                        f"(table {existing_table}); a different latent dimension is a different "
                        "model and needs its own version"
                    )
                return LatentRegistration(
                    wm_version=wm_version, table_name=existing_table, dim=existing_dim
                )
            await cursor.execute(
                "INSERT INTO latent_index_registry (wm_version, table_name, dim) "
                "VALUES (%s, %s, %s)",
                (wm_version, table, dim),
            )
        log.info("knowledge.latent_version_registered", wm_version=wm_version, table=table, dim=dim)
        return LatentRegistration(wm_version=wm_version, table_name=table, dim=dim)

    async def registration(self, wm_version: str) -> LatentRegistration:
        """登録済みの対応を引く。

        Args:
            wm_version: モデル版 IRI。

        Raises:
            KnowledgeError: 未登録の場合。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT table_name, dim FROM latent_index_registry WHERE wm_version = %s",
                (wm_version,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KnowledgeError(
                f"{wm_version} has no latent index registration; call register() first so that "
                "the write lands in the table with the matching dimension"
            )
        return LatentRegistration(wm_version=wm_version, table_name=str(row[0]), dim=int(row[1]))

    async def write(
        self,
        *,
        entity: str,
        wm_version: str,
        level: str,
        timestamps: Sequence[datetime],
        vectors: NDArray[np.float64],
    ) -> int:
        """潜在ベクトルを書き込む（同じ鍵は上書き）。

        同じ ``(entity, ts, level, wm_version)`` の再書き込みを上書きにしているのは、再接地
        ジョブが同じ窓を再計算して入れ直すためである。アサーションと違い潜在索引は**投影**で
        あり、追記専用の不変条件 2 の対象ではない。

        Args:
            entity: 実体 IRI。
            wm_version: モデル版 IRI。
            level: 階層（``fast`` / ``mid`` / ``slow``）。
            timestamps: 各行の時刻。
            vectors: 潜在ベクトル（``(行, 次元)``）。

        Returns:
            書き込んだ行数。

        Raises:
            KnowledgeError: 未登録の版、次元の不一致、または長さの不一致の場合。
        """
        registration = await self.registration(wm_version)
        if vectors.ndim != 2:
            raise KnowledgeError(f"vectors must be 2-D (row, dim), got shape {vectors.shape}")
        if vectors.shape[1] != registration.dim:
            raise KnowledgeError(
                f"{wm_version} is registered with dim {registration.dim} but the vectors have "
                f"{vectors.shape[1]}; the latent space changed, so the version must change too"
            )
        if len(timestamps) != vectors.shape[0]:
            raise KnowledgeError(f"{vectors.shape[0]} vectors but {len(timestamps)} timestamps")

        rows = [
            (entity, ensure_utc(at), level, wm_version, _to_pgvector(vectors[index]))
            for index, at in enumerate(timestamps)
        ]
        # 表名は識別子として組み立てる（文字列連結で SQL を作らない。登録表から来る値でも
        # 引用を省くと、表名に予期しない文字が入ったときに壊れる）
        statement = sql.SQL(
            "INSERT INTO {table} (entity, ts, level, wm_version, vec) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (entity, ts, level, wm_version) DO UPDATE SET vec = EXCLUDED.vec"
        ).format(table=sql.Identifier(registration.table_name))
        async with self._connection.cursor() as cursor:
            await cursor.executemany(statement, rows)
        log.debug(
            "knowledge.latents_written",
            entity=entity,
            wm_version=wm_version,
            level=level,
            rows=len(rows),
        )
        return len(rows)

    async def since(
        self,
        *,
        entity: str,
        wm_version: str,
        level: str,
        after: datetime | None = None,
        limit: int = 1_000,
    ) -> list[tuple[datetime, NDArray[np.float64]]]:
        """ある時刻より後の潜在を時刻順に返す（接地層が引く読み出し）。

        ANN 近傍検索（:meth:`neighbours`）とは用途が違う。接地層は「次に処理すべき潜在」を
        時刻順に取りたいので、類似度ではなく時刻で引く。

        **``after`` は右開（``ts > after``）。** 同じ時刻の行を 2 度処理しないためで、
        ウォーターマークを「最後に処理した時刻」として持てる。閉区間にすると毎回 1 行が
        重複し、事象昇格が同じ変化点を繰り返し出す。

        **上限を置く。** 潜在は 1 実体あたり毎秒単位で増えるので、無制限に読むと 1 ループが
        終わらなくなる。打ち切ったかどうかは呼び出し側が件数で判断できる（``limit`` 件
        返ったら続きがある可能性がある）。

        Args:
            entity: 実体 IRI。
            wm_version: モデル版 IRI。
            level: 階層（``fast`` / ``mid`` / ``slow``）。
            after: この時刻より後の行だけを返す（``None`` なら最初から）。
            limit: 返す行数の上限。

        Returns:
            ``(時刻, 潜在ベクトル)`` の列（時刻の昇順）。

        Raises:
            KnowledgeError: 未登録の版の場合。
        """
        registration = await self.registration(wm_version)
        statement = sql.SQL(
            "SELECT ts, vec FROM {table} "
            "WHERE entity = %s AND level = %s AND wm_version = %s "
            "AND (%s::timestamptz IS NULL OR ts > %s) "
            "ORDER BY ts LIMIT %s"
        ).format(table=sql.Identifier(registration.table_name))
        moment = None if after is None else ensure_utc(after)
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, (entity, level, wm_version, moment, moment, limit))
            rows = await cursor.fetchall()
        out = [(ensure_utc(row[0]), _from_pgvector(row[1])) for row in rows]
        log.debug(
            "knowledge.latents_read",
            entity=entity,
            wm_version=wm_version,
            level=level,
            rows=len(out),
            truncated=len(out) == limit,
        )
        return out

    async def latest(
        self, *, entity: str, wm_version: str, level: str
    ) -> tuple[datetime, NDArray[np.float64]] | None:
        """実体の最も新しい潜在 1 件（予測の起点）。無ければ ``None``。

        Args:
            entity: 実体 IRI。
            wm_version: モデル版 IRI。
            level: 階層。

        Raises:
            KnowledgeError: 未登録の版の場合。
        """
        registration = await self.registration(wm_version)
        statement = sql.SQL(
            "SELECT ts, vec FROM {table} "
            "WHERE entity = %s AND level = %s AND wm_version = %s "
            "ORDER BY ts DESC LIMIT 1"
        ).format(table=sql.Identifier(registration.table_name))
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, (entity, level, wm_version))
            row = await cursor.fetchone()
        return None if row is None else (ensure_utc(row[0]), _from_pgvector(row[1]))

    async def neighbours(
        self,
        *,
        query: NDArray[np.float64],
        wm_version: str,
        level: str,
        limit: int = _DEFAULT_LIMIT,
        entity: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[LatentNeighbour, ...]:
        """コサイン距離で近傍を返す（``level`` と ``wm_version`` で絞ってから検索）。

        Args:
            query: 問い合わせベクトル。
            wm_version: モデル版 IRI。
            level: 階層。
            limit: 返す件数。
            entity: 実体で絞る（``None`` なら全実体）。
            since: この時刻以降に絞る。
            until: この時刻より前に絞る（右開）。

        Returns:
            近い順の近傍。

        Raises:
            KnowledgeError: 未登録の版、次元の不一致、または ``limit`` が正でない場合。
        """
        registration = await self.registration(wm_version)
        if query.ndim != 1 or query.size != registration.dim:
            raise KnowledgeError(
                f"query must be a {registration.dim}-dimensional vector, got shape {query.shape}"
            )
        if limit < 1:
            raise KnowledgeError(f"limit must be at least 1, got {limit}")

        conditions: list[sql.Composable] = [
            sql.SQL("level = %s"),
            sql.SQL("wm_version = %s"),
        ]
        parameters: list[object] = [level, wm_version]
        if entity is not None:
            conditions.append(sql.SQL("entity = %s"))
            parameters.append(entity)
        if since is not None:
            conditions.append(sql.SQL("ts >= %s"))
            parameters.append(ensure_utc(since))
        if until is not None:
            conditions.append(sql.SQL("ts < %s"))  # 区間は右開（不変条件 8）
            parameters.append(ensure_utc(until))

        vector = _to_pgvector(query)
        statement = sql.SQL(
            "SELECT entity, ts, level, vec <=> %s AS distance FROM {table} "
            "WHERE {conditions} ORDER BY vec <=> %s LIMIT %s"
        ).format(
            table=sql.Identifier(registration.table_name),
            conditions=sql.SQL(" AND ").join(conditions),
        )
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, [vector, *parameters, vector, limit])
            rows = await cursor.fetchall()
        return tuple(
            LatentNeighbour(
                entity=str(row[0]), at=ensure_utc(row[1]), level=str(row[2]), distance=float(row[3])
            )
            for row in rows
        )

    async def count(self, *, wm_version: str, level: str | None = None) -> int:
        """登録済みの潜在の件数（再接地ジョブの進捗確認に使う）。

        Args:
            wm_version: モデル版 IRI。
            level: 階層で絞る。
        """
        registration = await self.registration(wm_version)
        conditions: list[sql.Composable] = [sql.SQL("wm_version = %s")]
        parameters: list[object] = [wm_version]
        if level is not None:
            conditions.append(sql.SQL("level = %s"))
            parameters.append(level)
        statement = sql.SQL("SELECT count(*) FROM {table} WHERE {conditions}").format(
            table=sql.Identifier(registration.table_name),
            conditions=sql.SQL(" AND ").join(conditions),
        )
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, parameters)
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def _table_for_dim(self, dim: int) -> str:
        """次元に対応する表名を返す。

        存在しない次元は**マイグレーションを切るべき状況**であり、実行時に表を作らない。
        アプリがスキーマを作ると、本番とマイグレーションの状態が食い違う。
        """
        candidates = ("latent_index", f"latent_index_d{dim}")
        async with self._connection.cursor() as cursor:
            for table in candidates:
                await cursor.execute(
                    "SELECT a.atttypmod FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = %s AND a.attname = 'vec'",
                    (table,),
                )
                row = await cursor.fetchone()
                if row is not None and int(row[0]) == dim:
                    return table
        raise KnowledgeError(
            f"no latent index table holds {dim}-dimensional vectors; add one in an alembic "
            "migration (the application does not create schema at runtime)"
        )


def _to_pgvector(vector: NDArray[np.float64]) -> str:
    """numpy ベクトルを pgvector のリテラルにする。"""
    return "[" + ",".join(f"{float(value):.8g}" for value in vector) + "]"


def _from_pgvector(value: object) -> NDArray[np.float64]:
    """pgvector の値を numpy ベクトルに戻す。

    psycopg が pgvector 型を知らないと文字列（``"[1,2,3]"``）で返ってくる。登録済みの
    アダプタがあればリストで返るので、**両方を受ける**——型が環境依存なのに片方しか扱わないと、
    アダプタの有無で動かなくなる。

    Args:
        value: psycopg が返した pgvector の値。

    Raises:
        KnowledgeError: 解釈できない形の場合。
    """
    if isinstance(value, str):
        body = value.strip().removeprefix("[").removesuffix("]")
        if not body:
            return np.zeros(0, dtype=np.float64)
        return np.asarray([float(part) for part in body.split(",")], dtype=np.float64)
    if isinstance(value, (list, tuple)):
        return np.asarray([float(part) for part in value], dtype=np.float64)
    if isinstance(value, np.ndarray):
        return np.asarray(value, dtype=np.float64)
    raise KnowledgeError(f"cannot read a pgvector value of type {type(value).__name__}")
