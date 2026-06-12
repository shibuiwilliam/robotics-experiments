"""世界グラフ（C6）— 来歴・確信度・時刻付きABox。

D3方式: 各主張(Claim)は自分のnamed graph（claim IRI）に入り、メタグラフ
（META_GRAPH）でグラフIRIに orx-prov: メタデータを付与する。

pyoxigraph への書込はこのモジュールの公開APIのみが行う（CLAUDE.md §3.2）。
他モジュールからの直接INSERTはアーキテクチャテストで禁止。
"""

from __future__ import annotations

from pathlib import Path

import pyoxigraph as ox

from orx.common import iri
from orx.common.paths import ontology_dir, shapes_dir
from orx.common.schemas import Claim, StateSnapshot, Term, TripleRecord, Vec3

META_GRAPH = "https://orx.local/id/graph/meta"
CURRENT_GRAPH = "https://orx.local/id/graph/current"

# 主体×述語で最新が優先される「関数的」述語（同一主体に同時に1値）
FUNCTIONAL_PREDICATES = frozenset(
    {
        iri.st("inZone"),
        iri.st("posX"),
        iri.st("posY"),
        iri.st("posZ"),
        iri.upper("anchoredTo"),
    }
)

_TBOX_FILES = (
    "upper.ttl",
    "domains/spacetime.ttl",
    "domains/agency.ttl",
    "domains/business.ttl",
)


def _to_ox_term(term: Term) -> ox.NamedNode | ox.Literal:
    if term.kind == "iri":
        return ox.NamedNode(term.value)
    if term.datatype:
        return ox.Literal(term.value, datatype=ox.NamedNode(term.datatype))
    return ox.Literal(term.value)


def _double(value: float) -> ox.Literal:
    return ox.Literal(str(value), datatype=ox.NamedNode(iri.XSD_DOUBLE))


class WorldGraph:
    """共有世界グラフ。書込は `assert_claim` のみ。"""

    def __init__(self, tbox: bool = True, tbox_root: Path | None = None) -> None:
        self._store = ox.Store()
        self._claims: list[Claim] = []
        if tbox:
            root = tbox_root or ontology_dir()
            for rel in _TBOX_FILES:
                path = root / rel
                if not path.exists():
                    raise FileNotFoundError(f"TBoxファイルがありません: {path}")
                self._store.load(path=str(path), format=ox.RdfFormat.TURTLE)

    # ------------------------------------------------------------------ write

    def assert_claim(self, claim: Claim) -> str:
        """主張を書き込む。来歴・確信度・時刻はスキーマで必須（不変条件2）。"""
        if claim.predicate == "http://www.w3.org/2002/07/owl#sameAs":
            raise ValueError(
                "owl:sameAs の直書きは禁止 (PROJECT.md §5.2-3)。"
                "orx-upper:anchoredTo を使うこと。"
            )
        graph = ox.NamedNode(iri.claim(claim.claim_id))
        self._store.add(
            ox.Quad(
                ox.NamedNode(claim.subject),
                ox.NamedNode(claim.predicate),
                _to_ox_term(claim.object),
                graph,
            )
        )
        meta = ox.NamedNode(META_GRAPH)
        rdf_type = ox.NamedNode(iri.RDF_TYPE)
        self._store.add(ox.Quad(graph, rdf_type, ox.NamedNode(iri.prov("Claim")), meta))
        self._store.add(
            ox.Quad(
                graph,
                ox.NamedNode(iri.prov("assertedBy")),
                ox.NamedNode(claim.asserted_by),
                meta,
            )
        )
        self._store.add(
            ox.Quad(graph, ox.NamedNode(iri.prov("confidence")), _double(claim.confidence), meta)
        )
        self._store.add(
            ox.Quad(graph, ox.NamedNode(iri.prov("observedAt")), _double(claim.observed_at), meta)
        )
        if claim.valid_until is not None:
            self._store.add(
                ox.Quad(
                    graph, ox.NamedNode(iri.prov("validUntil")), _double(claim.valid_until), meta
                )
            )
        self._claims.append(claim)
        return graph.value

    # ----------------------------------------------------------------- belief

    def claims(self) -> list[Claim]:
        return list(self._claims)

    def _group_key(self, claim: Claim) -> tuple[str, ...]:
        if claim.predicate in FUNCTIONAL_PREDICATES:
            return (claim.subject, claim.predicate)
        return (claim.subject, claim.predicate, claim.object.canonical())

    def current_claims(self, at_time: float) -> list[Claim]:
        """時点 at_time の現在信念（失効除外＋関数的述語の調停: 最新優先）。

        P0の調停は「最新観測優先、同時刻なら高確信度」。来歴ベースの本格調停は
        P4 (H5) で拡張する。
        """
        groups: dict[tuple[str, ...], Claim] = {}
        for c in self._claims:
            if c.observed_at > at_time + 1e-9:
                continue
            if c.valid_until is not None and c.valid_until < at_time - 1e-9:
                continue
            key = self._group_key(c)
            cur = groups.get(key)
            if cur is None or (c.observed_at, c.confidence, c.claim_id) > (
                cur.observed_at,
                cur.confidence,
                cur.claim_id,
            ):
                groups[key] = c
        return [groups[k] for k in sorted(groups)]

    def snapshot(self, at_time: float) -> StateSnapshot:
        """評価用スナップショット（現在信念のトリプル＋位置）。"""
        current = self.current_claims(at_time)
        triples = [
            TripleRecord(subject=c.subject, predicate=c.predicate, object=c.object.canonical())
            for c in current
        ]
        coords: dict[str, dict[str, float]] = {}
        axis = {iri.st("posX"): "x", iri.st("posY"): "y", iri.st("posZ"): "z"}
        for c in current:
            ax = axis.get(c.predicate)
            if ax is not None:
                coords.setdefault(c.subject, {})[ax] = float(c.object.value)
        positions: dict[str, Vec3] = {
            s: (v["x"], v["y"], v["z"]) for s, v in coords.items() if len(v) == 3
        }
        return StateSnapshot(sim_time=at_time, triples=triples, positions=positions)

    def staleness_rate(self, at_time: float) -> float:
        """陳腐化率: 失効したまま後続主張に置換されていない信念グループの割合。"""
        latest: dict[tuple[str, ...], Claim] = {}
        for c in self._claims:
            key = self._group_key(c)
            cur = latest.get(key)
            if cur is None or (c.observed_at, c.confidence, c.claim_id) > (
                cur.observed_at,
                cur.confidence,
                cur.claim_id,
            ):
                latest[key] = c
        if not latest:
            return 0.0
        stale = sum(
            1
            for c in latest.values()
            if c.valid_until is not None and c.valid_until < at_time - 1e-9
        )
        return stale / len(latest)

    # ------------------------------------------------------------------ query

    def refresh_current_graph(self, at_time: float) -> None:
        """現在信念を CURRENT_GRAPH に物質化する（CQ・エージェント用の窓）。"""
        current_node = ox.NamedNode(CURRENT_GRAPH)
        for quad in list(self._store.quads_for_pattern(None, None, None, current_node)):
            self._store.remove(quad)
        for c in self.current_claims(at_time):
            self._store.add(
                ox.Quad(
                    ox.NamedNode(c.subject),
                    ox.NamedNode(c.predicate),
                    _to_ox_term(c.object),
                    current_node,
                )
            )

    def query(self, sparql: str) -> list[dict[str, str]]:
        """SELECTクエリを実行し、変数→値(文字列)の行リストを返す。"""
        solutions = self._store.query(sparql)
        rows: list[dict[str, str]] = []
        for solution in solutions:
            row: dict[str, str] = {}
            for var in solutions.variables:
                term = solution[var.value]
                if term is not None:
                    row[var.value] = term.value
            rows.append(row)
        return rows

    # ------------------------------------------------------------- validation

    def validate_shacl(self, shapes_root: Path | None = None) -> tuple[bool, str]:
        """ストア全量（データ＋メタ）をSHACL shapesで検証する。"""
        import pyshacl
        import rdflib

        data = rdflib.Graph()
        for quad in self._store.quads_for_pattern(None, None, None, None):
            data.add(
                (
                    rdflib.URIRef(quad.subject.value),
                    rdflib.URIRef(quad.predicate.value),
                    _to_rdflib_object(quad.object),
                )
            )
        shapes = rdflib.Graph()
        root = shapes_root or shapes_dir()
        for path in sorted(root.glob("*.ttl")):
            shapes.parse(path, format="turtle")
        conforms, _, results_text = pyshacl.validate(
            data_graph=data, shacl_graph=shapes, inference="none"
        )
        return bool(conforms), str(results_text)


def _to_rdflib_object(term: object) -> object:
    import rdflib

    if isinstance(term, ox.NamedNode):
        return rdflib.URIRef(term.value)
    if isinstance(term, ox.Literal):
        dt = rdflib.URIRef(term.datatype.value) if term.datatype else None
        return rdflib.Literal(term.value, datatype=dt)
    if isinstance(term, ox.BlankNode):
        return rdflib.BNode(term.value)
    raise TypeError(f"unsupported term type: {type(term)!r}")
