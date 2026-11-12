"""指標の読み書き（M8-06）。

**ここでしか確かめられない。** `MetricStore` は SQL（jsonb の labels を鍵に含む UPSERT、
`DISTINCT ON` による系列ごとの最新値）そのもので、単体テストの代役では
「系列ごとに最新の 1 行だけが返る」が本当に効いているか分からない。

重点:

1. **系列ごとに最新値だけ返る** — 全履歴を出すと、スクレイプごとに古い点を再投入して
   Prometheus 側の履歴が壊れる。
2. **ラベルが系列を分ける** — 同じ指標名でも実体ごとに別の系列である。
3. **同じ (name, ts, labels) は上書き** — 同じ時刻の同じ系列に 2 つの値は無い。
4. **列挙に無い指標名の行はエクスポートから除く** — 過去の版が書いた行で落ちない。
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from sbp.core.config import Settings
from sbp.core.metrics import MetricSample, metric, prometheus_exposition
from sbp.core.time import now
from sbp.knowledge import MetricStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SITE = "siteMetricsTest"


def _sample(
    *,
    key: str = "probe_auroc",
    value: float = 0.9,
    minutes_ago: int = 0,
    labels: dict[str, str] | None = None,
) -> MetricSample:
    return MetricSample(
        name=metric(key),
        value=value,
        at=now().replace(microsecond=0) - timedelta(minutes=minutes_ago),
        labels=labels if labels is not None else {},
    )


class TestWriteAndRead:
    """書く・読む。"""

    @pytest.mark.asyncio
    async def test_a_written_sample_comes_back(self, settings: Settings) -> None:
        """書いた値が最新値として返る。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = MetricStore(connection, site=SITE)
            sample = _sample(labels={"probe": "Running"})
            assert await store.write([sample]) == 1

            latest = await store.latest()
            assert len(latest) == 1
            assert latest[0].name.key == "probe_auroc"
            assert latest[0].value == 0.9
            assert latest[0].labels == {"probe": "Running"}
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_only_the_latest_value_per_series_is_returned(self, settings: Settings) -> None:
        """系列ごとに最新の 1 点だけ返る。

        全履歴を出すと、スクレイプごとに同じ古い点を再投入することになり、Prometheus 側で
        時刻が付け替わって履歴が壊れる。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = MetricStore(connection, site=SITE)
            await store.write(
                [
                    _sample(value=0.70, minutes_ago=30),
                    _sample(value=0.80, minutes_ago=10),
                    _sample(value=0.95, minutes_ago=0),
                ]
            )

            latest = await store.latest()
            assert [sample.value for sample in latest] == [0.95]
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_labels_separate_series(self, settings: Settings) -> None:
        """ラベルが違えば別の系列（同じ指標名でも実体ごとに分かれる）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = MetricStore(connection, site=SITE)
            await store.write(
                [
                    _sample(value=0.91, labels={"probe": "Running"}),
                    _sample(value=0.88, labels={"probe": "Faulted"}),
                ]
            )

            latest = await store.latest()
            assert {sample.labels["probe"]: sample.value for sample in latest} == {
                "Running": 0.91,
                "Faulted": 0.88,
            }
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_the_same_point_is_overwritten(self, settings: Settings) -> None:
        """同じ ``(name, ts, site, labels)`` は上書きする（再計算したら新しい値が正しい）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = MetricStore(connection, site=SITE)
            first = _sample(value=0.50)
            await store.write([first])
            await store.write([MetricSample(name=first.name, value=0.75, at=first.at, labels={})])

            latest = await store.latest()
            assert [sample.value for sample in latest] == [0.75]
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_other_sites_are_not_returned(self, settings: Settings) -> None:
        """他拠点の指標は返らない（指標は拠点ごとに分かれる）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await MetricStore(connection, site=SITE).write([_sample()])

            other = MetricStore(connection, site=f"{SITE}-other")
            assert await other.latest() == []
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_an_unknown_metric_name_is_skipped(self, settings: Settings) -> None:
        """列挙に無い指標名の行はエクスポートから除く（落ちない）。

        過去の版が書いた行が表に残っていても、エクスポート全体が失敗してはいけない
        ——1 つの古い行で全系列が見えなくなる。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = MetricStore(connection, site=SITE)
            await store.write([_sample()])
            # 列挙に無い名前を直接入れる（過去の版が書いた行を模す）
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO metrics (ts, name, value, site, labels) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb)",
                    (now(), "retired_metric", 1.0, SITE, "{}"),
                )

            latest = await store.latest()
            assert [sample.name.key for sample in latest] == ["probe_auroc"]
            await connection.rollback()


class TestExposition:
    """エクスポートまで通す。"""

    @pytest.mark.asyncio
    async def test_the_round_trip_produces_a_valid_exposition(self, settings: Settings) -> None:
        """表 → 最新値 → exposition format まで通る（API の /metrics と同じ経路）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = MetricStore(connection, site=SITE)
            await store.write(
                [
                    _sample(key="probe_auroc", value=0.93, labels={"probe": "Running"}),
                    _sample(key="discrepancies_raised", value=3.0, labels={"kind": "2"}),
                ]
            )

            body = prometheus_exposition(await store.latest())

            assert "# TYPE sbp_grounding_probe_auroc_ratio gauge" in body
            # 件数は counter（単位 count の接尾辞は _total）
            assert "# TYPE sbp_reconciliation_discrepancies_raised_total counter" in body
            assert 'sbp_grounding_probe_auroc_ratio{probe="Running"} 0.93' in body
            await connection.rollback()
