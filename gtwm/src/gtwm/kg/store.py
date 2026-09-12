"""`KGStore`：信念グラフの格納・問合せ・SHACL検証・バイテンポラルスナップショット。

ontology.md の契約：`add_beliefs()` `query(sparql)` `validate(shapes)` `snapshot(t)`。
rdflib（既定、インプロセス）と Oxigraph（Docker、SPARQL HTTP プロトコル）の2実装を提供する。

信念の格納形式は `schema.py` の NOTE のとおり標準 RDF 具象化（reification）を用いる
（ontology.md が前提とする RDF-star は現行の rdflib 7.6.0 では構文解析できないため）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import rdflib
from pyshacl import validate as shacl_validate
from rdflib import XSD, URIRef
from rdflib import Literal as RdfLiteral
from rdflib.query import ResultRow

from gtwm.kg.schema import GT, Belief

QUERIES_DIR = Path(__file__).parent / "queries"


def load_query(name: str) -> str:
    """`kg/queries/<name>` から SPARQL テキストを読む。文字列埋め込みは禁止（ontology.md）。"""
    path = QUERIES_DIR / name
    if not path.suffix:
        path = path.with_suffix(".rq")
    return path.read_text(encoding="utf-8")


def _term_to_sparql(value: Any) -> str:
    """Python値をSPARQLリテラル/IRIの字句表現に変換する（VALUES句注入用）。"""
    if isinstance(value, URIRef):
        return f"<{value}>"
    if isinstance(value, datetime):
        return f'"{value.isoformat()}"^^xsd:dateTime'
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return f"<{value}>"
    if isinstance(value, str) and value.startswith("gt:"):
        return f"gt:{value[len('gt:') :]}"
    return f'"{value}"'


def _inject_values(sparql: str, bindings: dict[str, Any]) -> str:
    """`WHERE {` の直後に `VALUES ?var { ... }` を注入し、パラメータ束縛する。

    SPARQL 本文はすべて .rq ファイルに置き、Python側では値の注入のみを行う
    （新しいクエリロジックを文字列として書かない）。
    """
    if not bindings:
        return sparql
    marker = "WHERE {"
    idx = sparql.find(marker)
    if idx == -1:
        raise ValueError("bindings を使うクエリは 'WHERE {' を含む必要がある")
    insert_at = idx + len(marker)
    values_clauses = "\n".join(
        f"  VALUES ?{name} {{ {_term_to_sparql(value)} }}" for name, value in bindings.items()
    )
    return sparql[:insert_at] + "\n" + values_clauses + sparql[insert_at:]


@dataclass
class ValidationReport:
    """SHACL 検証結果。"""

    conforms: bool
    violations: list[dict[str, str]] = field(default_factory=list)
    text_report: str = ""


class KGStore(Protocol):
    """add_beliefs/query/validate/snapshot を持つストアの契約（ontology.md）。"""

    def add_beliefs(self, beliefs: list[Belief]) -> None: ...

    def query(
        self, sparql: str, bindings: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...

    def validate(self, shapes_paths: list[Path]) -> ValidationReport: ...

    def snapshot(self, t: datetime) -> rdflib.Graph: ...


def _all_belief_rows(source: rdflib.Graph) -> list[Any]:
    return list(source.query(load_query("all_beliefs")))


def _valid_at(rows: list[Any], t_lit: RdfLiteral) -> list[Any]:
    """有効時間 [validFrom, validTo) が t を含む行だけを残す（ontology.md の snapshot(t) 定義）。

    意図的に (subject, predicate) の重複排除はしない：正しく閉区間化された信念
    （後続の信念が前の信念の validTo を締める。`kg/epcis.py` 参照）であれば同一時刻に
    重複することは無いはずで、それでも重複が残る場合こそ「同一有効時刻に複数の値を
    主張している」という真の乖離であり、`PalletSingleLocationShape` 等の maxCount
    制約はこれを検出するために存在する。ここで握りつぶすと検証の意味が無くなる。
    """
    return [
        row
        for row in rows
        if row.validFrom <= t_lit and (row.validTo is None or row.validTo > t_lit)
    ]


def _materialize(rows: list[Any]) -> rdflib.Graph:
    out = rdflib.Graph()
    out.bind("gt", GT)
    for row in rows:
        out.add((row.subject, row.predicate, row.object))
    return out


def _snapshot_graph(source: rdflib.Graph, t: datetime) -> rdflib.Graph:
    """`source` から時刻 t に有効な信念を材料化した平坦グラフを作る（共通ロジック）。"""
    t_lit = RdfLiteral(t.isoformat(), datatype=XSD.dateTime)
    return _materialize(_valid_at(_all_belief_rows(source), t_lit))


def _validate_graph(data_graph: rdflib.Graph, shapes_paths: list[Path]) -> ValidationReport:
    """信頼度 0.7 以上の信念の「現在の状態」を材質化して SHACL 検証する（ontology.md）。

    確信度 0.7 未満の信念は検証対象外（ontology.md）。「現在」は、検証対象グラフに
    含まれる信念のうち最大の transactionTime（システムが最後に何かを知った時刻）を
    仮の現在時刻として、その時刻に有効な信念を snapshot(t) と同じ規則で材質化する。
    """
    shapes_graph = rdflib.Graph()
    for p in shapes_paths:
        shapes_graph.parse(p, format="turtle")

    threshold = RdfLiteral(0.7, datatype=XSD.double)
    confident_rows = [row for row in _all_belief_rows(data_graph) if row.confidence >= threshold]
    if not confident_rows:
        high_conf = rdflib.Graph()
    else:
        now_t = max(row.transactionTime for row in confident_rows)
        high_conf = _materialize(_valid_at(confident_rows, now_t))

    # pyshacl の生の conforms は allow_warnings=True だと常に True になりがちなので使わず、
    # 抽出した violations の有無で独自に判定する。
    _, results_graph, results_text = shacl_validate(
        high_conf,
        shacl_graph=shapes_graph,
        advanced=True,
        allow_infos=True,
        allow_warnings=True,
    )
    violations = [
        {
            "focusNode": str(row.focusNode),
            "message": str(row.message),
            "severity": str(row.severity),
        }
        for row in results_graph.query(load_query("shacl_violation_messages"))
        if isinstance(row, ResultRow)
    ]
    return ValidationReport(
        conforms=len(violations) == 0, violations=violations, text_report=results_text
    )


class RdflibKGStore:
    """既定のインプロセス実装（rdflib、標準 RDF 具象化）。"""

    def __init__(self) -> None:
        self.graph = rdflib.Graph()
        self.graph.bind("gt", GT)

    def add_beliefs(self, beliefs: list[Belief]) -> None:
        for belief in beliefs:
            for triple in belief.to_reified_triples():
                self.graph.add(triple)

    def query(self, sparql: str, bindings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        init_bindings = {k: _to_rdflib_term(v) for k, v in (bindings or {}).items()}
        result = self.graph.query(sparql, initBindings=init_bindings)
        return [row.asdict() for row in result if isinstance(row, ResultRow)]

    def validate(self, shapes_paths: list[Path]) -> ValidationReport:
        return _validate_graph(self.graph, shapes_paths)

    def snapshot(self, t: datetime) -> rdflib.Graph:
        return _snapshot_graph(self.graph, t)


def _to_rdflib_term(value: Any) -> Any:
    if isinstance(value, datetime):
        return RdfLiteral(value.isoformat(), datatype=XSD.dateTime)
    if isinstance(value, str) and value.startswith("gt:"):
        return GT[value[len("gt:") :]]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return URIRef(value)
    return RdfLiteral(value)


class OxigraphKGStore:
    """Docker上の Oxigraph サーバ（SPARQL 1.1 HTTP プロトコル）を使う実装。

    埋め込み pyoxigraph ではなく `http://localhost:7878` の `/query` `/update`
    エンドポイントに対して SPARQL Query/Update を発行する（`docker-compose.yml`
    profile `core` が起動するサーバを対象とする、`integration` マーカーのテストのみで使用）。
    """

    def __init__(self, base_url: str = "http://localhost:7878") -> None:
        self.base_url = base_url.rstrip("/")

    def _post_update(self, update: str) -> None:
        req = urllib.request.Request(
            f"{self.base_url}/update",
            data=update.encode("utf-8"),
            headers={"Content-Type": "application/sparql-update"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()

    def _post_query(self, query: str) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/query",
            data=query.encode("utf-8"),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return dict(json.loads(resp.read()))

    def clear(self) -> None:
        self._post_update("CLEAR DEFAULT")

    def add_beliefs(self, beliefs: list[Belief]) -> None:
        g = rdflib.Graph()
        for belief in beliefs:
            for triple in belief.to_reified_triples():
                g.add(triple)
        if len(g) == 0:
            return
        nt = g.serialize(format="nt")
        self._post_update(f"INSERT DATA {{ {nt} }}")

    def query(self, sparql: str, bindings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        full_query = _inject_values(
            sparql, {k: _bindings_value(v) for k, v in (bindings or {}).items()}
        )
        raw = self._post_query(full_query)
        vars_ = raw["head"]["vars"]
        rows = []
        for binding in raw["results"]["bindings"]:
            row: dict[str, Any] = {}
            for v in vars_:
                if v in binding:
                    row[v] = binding[v]["value"]
            rows.append(row)
        return rows

    def validate(self, shapes_paths: list[Path]) -> ValidationReport:
        return _validate_graph(self._fetch_full_graph(), shapes_paths)

    def _fetch_full_graph(self) -> rdflib.Graph:
        req = urllib.request.Request(
            f"{self.base_url}/query",
            data=b"CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
            headers={"Content-Type": "application/sparql-query", "Accept": "text/turtle"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
        g = rdflib.Graph()
        g.parse(data=body, format="turtle")
        return g

    def snapshot(self, t: datetime) -> rdflib.Graph:
        return _snapshot_graph(self._fetch_full_graph(), t)

    def is_available(self) -> bool:
        try:
            self._post_query("ASK { ?s ?p ?o }")
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            return False


def _bindings_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("gt:"):
        return URIRef(f"{GT}{value[len('gt:') :]}")
    return value


__all__ = [
    "KGStore",
    "OxigraphKGStore",
    "RdflibKGStore",
    "ValidationReport",
    "load_query",
]
