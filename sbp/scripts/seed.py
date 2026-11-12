"""``make seed`` — Compose 上の各ストアを使える状態にする。

実施内容（CLAUDE.md「よく使うコマンド」）:

1. トピック作成（Redpanda）— ``scripts/topics.py`` の定義どおり。手作業で作らない。
2. DB マイグレーション（alembic）— ``infra/sql/versions/``。DDL を手で流さない。
3. バケット作成（S3 互換ストア）— raw 層とモデル重み。
4. オントロジー投入（Oxigraph）— ``ontology/`` の TTL を名前付きグラフに読み込む。

いずれも**冪等**にする（既にあるものは作らない）。`make seed` を何度実行しても同じ状態になること。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import httpx

from sbp.core.config import Settings, get_settings
from sbp.core.logging import configure_logging, get_logger
from scripts._ontology import ONTOLOGY_DIR, TTL_GLOBS, ontology_version
from scripts.topics import TopicSpec, all_topics

_REPO_ROOT: Final = Path(__file__).resolve().parent.parent
log = get_logger(__name__)


# ---- 1. トピック -------------------------------------------------------------


def create_topics(settings: Settings) -> None:
    """Redpanda にトピックを作る（既存はそのまま）。

    Args:
        settings: 接続設定。
    """
    # NewTopic は confluent_kafka.admin から再公開されているが __all__ に無いため
    # pyright が private import とみなす。実体のあるモジュールから取る。
    from confluent_kafka.admin import AdminClient  # noqa: PLC0415
    from confluent_kafka.cimpl import NewTopic  # noqa: PLC0415

    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    existing = set(admin.list_topics(timeout=10).topics)
    specs: list[TopicSpec] = all_topics(settings.site_id)
    to_create = [s for s in specs if s.name not in existing]

    if not to_create:
        log.info("topics.unchanged", count=len(specs))
        return

    futures = admin.create_topics(
        [
            NewTopic(
                s.name,
                num_partitions=s.partitions,
                replication_factor=1,
                config={"retention.ms": str(s.retention_ms)},
            )
            for s in to_create
        ]
    )
    for name, future in futures.items():
        future.result(timeout=15)
        log.info("topics.created", topic=name)
    log.info("topics.done", created=len(to_create), total=len(specs))


# ---- 2. マイグレーション -----------------------------------------------------


def run_migrations() -> None:
    """alembic でスキーマを最新にする。"""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "infra/sql/alembic.ini", "upgrade", "head"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        log.error("migrations.failed", stderr=result.stderr.strip()[:2000])
        raise RuntimeError("alembic upgrade failed")
    log.info("migrations.done", output=result.stdout.strip().splitlines()[-1:] or ["(no output)"])


# ---- 3. バケット -------------------------------------------------------------


def create_buckets(settings: Settings) -> None:
    """S3 互換ストアに raw / models バケットを作る（既存はそのまま）。

    Args:
        settings: 接続設定。
    """
    import boto3  # noqa: PLC0415
    from botocore.exceptions import ClientError  # noqa: PLC0415

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
    )
    for bucket in (settings.s3_bucket_raw, settings.s3_bucket_models):
        try:
            client.head_bucket(Bucket=bucket)
            log.info("bucket.exists", bucket=bucket)
        except ClientError:
            client.create_bucket(Bucket=bucket)
            log.info("bucket.created", bucket=bucket)


# ---- 4. オントロジー投入 -----------------------------------------------------


def _ontology_graph_iri(version: str) -> str:
    """オントロジーを格納する名前付きグラフの IRI。

    版ごとに別グラフにすることで、版を跨いだ as-of クエリと再接地が成立する
    （名前付きグラフ戦略: ``urn:sbp:g:<modality>:<site>:<ontology_version>``。
    オントロジー自身は様相を持たないため ``schema`` を用いる）。
    """
    return f"urn:sbp:g:schema:ontology:{version}"


def load_ontology_into_store(settings: Settings) -> None:
    """``ontology/`` の TTL を Oxigraph の名前付きグラフへ投入する（冪等）。

    同じ版のグラフが既にあれば置き換える（オントロジーは追記専用の対象ではなく、
    版が同じなら内容も同じであるべきもの。アサーションの追記専用原則とは別）。

    Args:
        settings: 接続設定。
    """
    version = ontology_version()
    graph_iri = _ontology_graph_iri(version)
    payload: list[str] = []
    for pattern in TTL_GLOBS:
        for path in sorted(ONTOLOGY_DIR.glob(pattern)):
            payload.append(path.read_text(encoding="utf-8"))
    if not payload:
        raise FileNotFoundError(f"no Turtle files under {ONTOLOGY_DIR}")

    with httpx.Client(timeout=30.0) as client:
        # 既存の同版グラフを消してから入れる（PUT 相当。冪等にするため）。
        drop = client.post(
            settings.sparql_update_url,
            content=f"DROP SILENT GRAPH <{graph_iri}>",
            headers={"Content-Type": "application/sparql-update"},
        )
        drop.raise_for_status()
        response = client.post(
            settings.sparql_store_url,
            params={"graph": graph_iri},
            content="\n".join(payload).encode("utf-8"),
            headers={"Content-Type": "text/turtle"},
        )
        response.raise_for_status()

        count = client.post(
            settings.sparql_query_url,
            content=f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
        )
        count.raise_for_status()
        triples = count.json()["results"]["bindings"][0]["n"]["value"]

    log.info("ontology.loaded", graph=graph_iri, version=version, triples=int(triples))


# ---- エントリポイント --------------------------------------------------------


def main() -> int:
    """全ステップを順に実行する。失敗したら即座に非ゼロで返す。"""
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info(
        "seed.start",
        site=settings.site_id,
        kafka=settings.kafka_bootstrap,
        sparql=settings.sparql_store_url,
        s3=settings.s3_endpoint,
    )
    create_topics(settings)
    run_migrations()
    create_buckets(settings)
    load_ontology_into_store(settings)
    log.info("seed.done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
