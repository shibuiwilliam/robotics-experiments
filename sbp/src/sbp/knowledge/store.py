"""グラフストアの抽象（設計書 §6.3）。

単体テストでは pyoxigraph を組み込みで使い（サーバ不要。CLAUDE.md インフラ節）、統合テストと
実運用では HTTP の SPARQL エンドポイントを使う。どちらも :class:`GraphStore` Protocol を満たす。

SPARQL 文字列を組み立てるのは ``sbp.knowledge`` の内側だけ（knowledge ルール）。この層は
「文字列を受け取って実行する」ところまでを担い、文字列の生成は :mod:`sbp.knowledge.assertions`
が行う。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sbp.core.errors import DependencyUnavailableError, KnowledgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sbp.core.config import Settings

__all__ = ["EmbeddedGraphStore", "GraphStore", "HttpGraphStore", "SolutionRow"]

log = get_logger(__name__)

SolutionRow = Mapping[str, str | None] if TYPE_CHECKING else dict
"""SPARQL SELECT の 1 行（変数名 → 値の文字列表現）。未束縛は ``None``。"""


@runtime_checkable
class GraphStore(Protocol):
    """RDF ストアの抽象。"""

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        """SELECT / ASK を実行して行のリストを返す。

        Args:
            sparql: SPARQL クエリ文字列。
        """
        ...

    def update(self, sparql: str) -> None:
        """INSERT / DELETE を実行する。

        Args:
            sparql: SPARQL Update 文字列。
        """
        ...

    def load_turtle(self, turtle: str, *, graph: str) -> None:
        """Turtle を名前付きグラフへ読み込む。

        Args:
            turtle: Turtle 文字列。
            graph: 投入先の名前付きグラフ IRI。
        """
        ...

    def health_check(self) -> None:
        """疎通を確認する。失敗したら :class:`DependencyUnavailableError` を送出する。"""
        ...


class EmbeddedGraphStore:
    """pyoxigraph の組み込みストア（単体テスト用。サーバ不要）。"""

    def __init__(self) -> None:
        import pyoxigraph as ox  # noqa: PLC0415

        self._ox = ox
        self._store = ox.Store()

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        """SELECT / ASK を実行する。

        ASK は pyoxigraph では ``QueryBoolean``（``bool`` の派生ではない）で返るため、
        ``variables`` を持つかどうかで SELECT と区別する。
        """
        result = self._store.query(sparql)
        if isinstance(result, self._ox.QueryBoolean):
            return [{"__ask__": "true" if bool(result) else "false"}]
        if not isinstance(result, self._ox.QuerySolutions):
            raise KnowledgeError(
                f"expected SELECT or ASK, got {type(result).__name__} "
                "(CONSTRUCT / DESCRIBE are not used by this layer)"
            )
        rows: list[dict[str, str | None]] = []
        variables = [str(v)[1:] for v in result.variables]
        for solution in result:
            row: dict[str, str | None] = {}
            for name in variables:
                term = solution[name]
                row[name] = None if term is None else _term_value(term)
            rows.append(row)
        return rows

    def update(self, sparql: str) -> None:
        """INSERT / DELETE を実行する。"""
        self._store.update(sparql)

    def load_turtle(self, turtle: str, *, graph: str) -> None:
        """Turtle を名前付きグラフへ読み込む。"""
        self._store.load(
            turtle.encode("utf-8"),
            format=self._ox.RdfFormat.TURTLE,
            to_graph=self._ox.NamedNode(graph),
        )

    def health_check(self) -> None:
        """組み込みストアは常に使える（整合性のため形だけ実装する）。"""
        return

    def __len__(self) -> int:
        """保持している quad の数（テストの検査用）。"""
        return len(self._store)


def _term_value(term: Any) -> str:
    """pyoxigraph の項から値の文字列を取り出す。

    リテラルは値そのもの、IRI は IRI 文字列を返す（データ型・言語タグは落とす）。
    呼び出し側が必要な型解釈を行う。
    """
    value = getattr(term, "value", None)
    return str(term) if value is None else str(value)


class HttpGraphStore:
    """HTTP の SPARQL エンドポイント（Oxigraph サーバ）を使うストア。

    ADR-0004 により Oxigraph のコンテナには healthcheck が無いため、:meth:`health_check` が
    実質の疎通確認になる。ワーカー起動時と統合テストの fixture から必ず呼ぶ。
    """

    def __init__(self, settings: Settings, *, timeout_s: float = 30.0) -> None:
        """Args:
        settings: 接続設定（SPARQL の 3 つの URL を使う）。
        timeout_s: HTTP タイムアウト（秒）。
        """
        import httpx  # noqa: PLC0415

        self._settings = settings
        self._client = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        """HTTP クライアントを閉じる。"""
        self._client.close()

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        """SELECT / ASK を実行する。"""
        response = self._client.post(
            self._settings.sparql_query_url,
            content=sparql.encode("utf-8"),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
        )
        if response.status_code >= 400:
            raise KnowledgeError(
                f"SPARQL query failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        if "boolean" in payload:
            return [{"__ask__": "true" if payload["boolean"] else "false"}]
        variables: list[str] = payload["head"]["vars"]
        rows: list[dict[str, str | None]] = []
        for binding in payload["results"]["bindings"]:
            rows.append({name: binding.get(name, {}).get("value") for name in variables})
        return rows

    def update(self, sparql: str) -> None:
        """INSERT / DELETE を実行する。"""
        response = self._client.post(
            self._settings.sparql_update_url,
            content=sparql.encode("utf-8"),
            headers={"Content-Type": "application/sparql-update"},
        )
        if response.status_code >= 400:
            raise KnowledgeError(
                f"SPARQL update failed with HTTP {response.status_code}: {response.text[:500]}"
            )

    def load_turtle(self, turtle: str, *, graph: str) -> None:
        """Turtle を名前付きグラフへ読み込む。"""
        response = self._client.post(
            self._settings.sparql_store_url,
            params={"graph": graph},
            content=turtle.encode("utf-8"),
            headers={"Content-Type": "text/turtle"},
        )
        if response.status_code >= 400:
            raise KnowledgeError(
                f"Turtle load failed with HTTP {response.status_code}: {response.text[:500]}"
            )

    def health_check(self) -> None:
        """SPARQL エンドポイントの疎通を確認する（ADR-0004 の実質 healthcheck）。

        Raises:
            DependencyUnavailableError: 接続できない、またはクエリが失敗した場合。
        """
        import httpx  # noqa: PLC0415

        try:
            rows = self.query("ASK { }")
        except (httpx.HTTPError, KnowledgeError) as exc:
            raise DependencyUnavailableError(
                f"Oxigraph is unreachable at {self._settings.sparql_query_url}: {exc}. "
                "Is the Compose stack up? (`make up`)"
            ) from exc
        if rows != [{"__ask__": "true"}]:
            raise DependencyUnavailableError(
                f"Oxigraph responded unexpectedly to ASK {{}}: {rows!r}"
            )
        log.debug("knowledge.store_healthy", endpoint=self._settings.sparql_query_url)
