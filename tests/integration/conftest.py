"""統合テストの fixture。

Compose の疎通を確認し、**未起動なら skip ではなく明示エラーにする**（infra-testing ルール）。
skip にすると「統合テストが通っていないのに緑に見える」状態が生まれ、ゲート判定が嘘になる。

ADR-0004 により Oxigraph にはコンテナの healthcheck が無い。ここでの疎通確認が実質の
healthcheck なので、省略すると検査が完全に消える。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from confluent_kafka.admin import AdminClient

from sbp.core.config import Settings
from sbp.core.errors import DependencyUnavailableError
from sbp.knowledge import AssertionStore, HttpGraphStore

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
"""リポジトリのルート（mappings/ と reliability/ を読むため）。"""

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

_SCENARIO_TEST_MARK = "g0-defer-"
"""統合テストが作るシナリオグラフの目印。後片付けの対象を見分けるために使う。"""

_SETUP_HINT = (
    "統合テストは Compose の 4 サービスを必要とする。`make up` で起動し `make seed` を実行する。"
    "ポートが他プロセスと衝突する場合は .env で退避させる（docs/runbooks/local-setup.md）。"
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    """統合テスト用の設定。

    ポート退避に追従できるよう、環境変数で上書きされた値をそのまま使う。
    テスト専用の上書きは ``SBP_IT_*`` ではなく通常の ``SBP_*`` で行う（本番と同じ経路を通す）。
    """
    return Settings(env="test")


@pytest.fixture(scope="session")
def graph_store(settings: Settings) -> Iterator[HttpGraphStore]:
    """Oxigraph への接続。疎通しなければ明示的に失敗する（ADR-0004 の実質 healthcheck）。"""
    store = HttpGraphStore(settings)
    try:
        store.health_check()
    except DependencyUnavailableError as exc:
        store.close()
        pytest.fail(f"Oxigraph unreachable: {exc}\n{_SETUP_HINT}", pytrace=False)
    yield store
    store.close()


@pytest.fixture(scope="session")
def kafka_available(settings: Settings) -> None:
    """Redpanda の疎通を確認する。未起動なら明示的に失敗する。"""
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap, "socket.timeout.ms": 5000})
    try:
        metadata = admin.list_topics(timeout=10)
    except Exception as exc:
        pytest.fail(
            f"Redpanda unreachable at {settings.kafka_bootstrap}: {exc}\n{_SETUP_HINT}",
            pytrace=False,
        )
    if not metadata.topics:
        pytest.fail(f"Redpanda has no topics; run `make seed`.\n{_SETUP_HINT}", pytrace=False)


@pytest.fixture(scope="session")
def postgres_available(settings: Settings) -> None:
    """Postgres の疎通とスキーマ適用を確認する。未適用なら明示的に失敗する。"""
    try:
        with psycopg.connect(settings.pg_dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('processed_events', 'latent_index', 'metrics', 'quarantine')"
            )
            row = cur.fetchone()
    except psycopg.Error as exc:
        pytest.fail(f"Postgres unreachable: {exc}\n{_SETUP_HINT}", pytrace=False)
    if row is None or row[0] != 4:
        pytest.fail(
            f"expected 4 projection tables, found {row[0] if row else 0}; run `make seed`.",
            pytrace=False,
        )


@pytest.fixture
def test_graph_suffix() -> str:
    """テストごとに独立した拠点 ID を返す（グラフを汚さないため）。

    拠点 ID をテスト用に変えることで、名前付きグラフ
    ``urn:sbp:g:<modality>:<site>:<version>`` がテストごとに分かれる。
    """
    return f"it-{os.getpid()}"


@pytest.fixture
def assertion_store(
    graph_store: HttpGraphStore, test_graph_suffix: str
) -> Iterator[AssertionStore]:
    """実サーバ上の二時制アサーション API。テスト後にそのテストのグラフを消す。"""
    store = AssertionStore(graph_store, site_id=test_graph_suffix)
    yield store
    # テスト用グラフだけを落とす（本番相当のグラフには触らない）。対象は 2 種類:
    #  - 主信念グラフ: urn:sbp:g:<modality>:<test_graph_suffix>:<version>
    #  - シナリオグラフ: urn:sbp:scenario:... （拠点でスコープされないため接尾辞で見分ける）
    # DELETE WHERE は FILTER を取れないため DELETE { } WHERE { } の形で書く。
    graph_store.update(
        "DELETE { GRAPH ?g { ?s ?p ?o } }\n"
        "WHERE {\n"
        "  GRAPH ?g { ?s ?p ?o }\n"
        f"  FILTER (CONTAINS(STR(?g), '{test_graph_suffix}')\n"
        f"          || CONTAINS(STR(?g), '{_SCENARIO_TEST_MARK}'))\n"
        "}"
    )
