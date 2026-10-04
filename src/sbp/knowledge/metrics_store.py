"""指標の読み書き（M8-06）。

``metrics`` 表（alembic 0001 で作った hypertable）への書き込みと、**系列ごとの最新値**の
読み出しを持つ。設計書 §12.3 の 6 領域 25 指標は :mod:`sbp.core.metrics` が列挙で固定して
おり、ここは永続化だけを担う。

**最新値だけを返す読み出しを用意する理由。** Prometheus は pull 型で、スクレイプした時点の
値を取る。履歴は Prometheus 側（と hypertable）に溜まるので、エクスポートに必要なのは
「いまの値」である。全履歴を出すと、スクレイプごとに同じ古い点を再投入することになり、
Prometheus 側で時刻が付け替わって履歴が壊れる。

**未定義の指標名を書かせない。** :func:`sbp.core.metrics.metric` を通すので、表に入るのは
列挙にある名前だけである。自由な文字列を許すと、同じものが 2 つの系列になって
ダッシュボードで突き合わせられない。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.metrics import MetricSample, metric
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import AsyncConnection

__all__ = ["MetricStore"]

log = get_logger(__name__)

_TABLE = "metrics"


class MetricStore:
    """``metrics`` 表の読み書き。

    Attributes:
        site: 拠点識別子（指標は拠点ごとに分かれる）。
    """

    def __init__(self, connection: AsyncConnection, *, site: str) -> None:
        """Args:
        connection: Postgres 接続（async）。
        site: 拠点識別子。
        """
        self._connection = connection
        self.site = site

    async def write(self, samples: Sequence[MetricSample]) -> int:
        """実測値を書く。

        同じ ``(name, ts, site, labels)`` は上書きする——同じ時刻の同じ系列に 2 つの値は
        無く、再計算したら新しい値が正しい。

        Args:
            samples: 実測値。

        Returns:
            書いた件数。
        """
        if not samples:
            return 0
        rows = [
            (
                sample.at,
                sample.name.key,
                sample.value,
                self.site,
                json.dumps(sample.labels, sort_keys=True, ensure_ascii=False),
            )
            for sample in samples
        ]
        async with self._connection.cursor() as cursor:
            await cursor.executemany(
                f"INSERT INTO {_TABLE} (ts, name, value, site, labels) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (name, ts, site, labels) DO UPDATE SET value = EXCLUDED.value",
                rows,
            )
        log.info("knowledge.metrics_written", site=self.site, count=len(rows))
        return len(rows)

    async def latest(self, *, limit: int = 1000) -> list[MetricSample]:
        """系列ごとの最新値を返す。

        系列は ``(name, labels)`` の組である。``DISTINCT ON`` で系列ごとに最新の 1 行を取る。

        Args:
            limit: 返す系列数の上限。超えたら警告を出す——黙って切ると、ダッシュボードに
                出ない指標が「測っていない」のか「切られた」のか分からない。

        Returns:
            実測値の列（系列名の辞書順）。**未定義の指標名の行は飛ばす**（列挙から消えた
            指標の古い行が表に残っていても、エクスポートで落ちない）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT DISTINCT ON (name, labels) name, value, ts, labels FROM {_TABLE} "
                "WHERE site = %s ORDER BY name, labels, ts DESC LIMIT %s",
                (self.site, limit + 1),
            )
            rows = await cursor.fetchall()
        truncated = len(rows) > limit
        samples: list[MetricSample] = []
        unknown: list[str] = []
        for row in rows[:limit]:
            key = str(row[0])
            try:
                definition = metric(key)
            except Exception:
                unknown.append(key)
                continue
            samples.append(
                MetricSample(
                    name=definition,
                    value=float(row[1]),
                    at=ensure_utc(row[2]),
                    labels=dict(row[3] or {}),
                )
            )
        if unknown:
            log.warning(
                "knowledge.metrics_unknown_names",
                site=self.site,
                names=sorted(set(unknown)),
                detail="列挙に無い指標名の行が表にある（過去の版が書いたもの）。エクスポートからは除いた",
            )
        if truncated:
            log.warning(
                "knowledge.metrics_truncated",
                site=self.site,
                limit=limit,
                detail="系列が上限を超えた。ダッシュボードに出ない指標がある（黙って切っていない）",
            )
        log.debug("knowledge.metrics_read", site=self.site, series=len(samples))
        return samples
