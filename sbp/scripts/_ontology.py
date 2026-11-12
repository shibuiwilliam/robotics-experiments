"""オントロジー読み込みの共通処理。生成スクリプトと検査スクリプトが共有する。

TTL の読み書きには rdflib を使う（ontology ルール）。pyoxigraph は実行時の知識グラフ用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, SOSA

ONTOLOGY_DIR: Final = Path(__file__).resolve().parent.parent / "ontology"
SCHEMAS_DIR: Final = Path(__file__).resolve().parent.parent / "schemas"
VOCAB_PATH: Final = Path(__file__).resolve().parent.parent / "src" / "sbp" / "core" / "vocab.py"

SBP: Final = Namespace("https://sbp.example/core#")
ENT: Final = Namespace("https://sbp.example/ent#")

NAMESPACES: Final[dict[str, Namespace]] = {"core": SBP, "ent": ENT}
"""生成される vocab のプレフィクス名 → 名前空間。"""

TTL_GLOBS: Final = ("core/*.ttl", "domain/*.ttl", "shapes/*.ttl")
"""読み込む TTL（``imports/`` は標準オントロジーであり改変・生成の対象外）。"""


@dataclass(frozen=True, slots=True)
class Term:
    """オントロジーの用語 1 件。

    Attributes:
        iri: 完全 IRI。
        prefix: 所属する名前空間のプレフィクス名（``core`` / ``ent``）。
        local_name: IRI のローカル名（``Running`` など）。
        kind: 用語の種別。``class`` / ``property`` / ``fluent_type`` / ``event_type`` /
            ``individual`` のいずれか。
        label_ja: 日本語ラベル。
        label_en: 英語ラベル。
        definition: ``skos:definition``。
        layer: ``sbp:layer``（L1..L4）。
        owner: ``sbp:owner``。
    """

    iri: str
    prefix: str
    local_name: str
    kind: str
    label_ja: str | None
    label_en: str | None
    definition: str | None
    layer: str | None
    owner: str | None


def load_ontology() -> Graph:
    """``ontology/`` の TTL をすべて 1 つのグラフに読み込む。

    Raises:
        FileNotFoundError: TTL が 1 つも見つからない場合。
    """
    graph = Graph()
    found = False
    for pattern in TTL_GLOBS:
        for path in sorted(ONTOLOGY_DIR.glob(pattern)):
            graph.parse(path, format="turtle")
            found = True
    if not found:
        raise FileNotFoundError(f"no Turtle files under {ONTOLOGY_DIR}")
    return graph


def ontology_version() -> str:
    """``ontology/VERSION`` の内容を返す。"""
    return (ONTOLOGY_DIR / "VERSION").read_text(encoding="utf-8").strip()


def _text(graph: Graph, subject: URIRef, predicate: URIRef, lang: str | None = None) -> str | None:
    for obj in graph.objects(subject, predicate):
        literal_lang = getattr(obj, "language", None)
        if lang is None or literal_lang == lang:
            return str(obj)
    return None


def _prefix_of(iri: str) -> str | None:
    for prefix, ns in NAMESPACES.items():
        if iri.startswith(str(ns)):
            return prefix
    return None


def _local_name(iri: str, prefix: str) -> str:
    return iri.removeprefix(str(NAMESPACES[prefix]))


def collect_terms(graph: Graph) -> list[Term]:
    """SBP が定義する用語（``core`` / ``ent`` 名前空間）を種別付きで集める。

    標準オントロジー由来の用語は対象にしない（再定義していないため）。
    """
    kinds: list[tuple[URIRef, str]] = [
        (OWL.Class, "class"),
        (OWL.ObjectProperty, "property"),
        (OWL.DatatypeProperty, "property"),
        (OWL.AnnotationProperty, "property"),
        (SBP.FluentType, "fluent_type"),
        (SBP.EventType, "event_type"),
        (SBP.DataSource, "individual"),
        # 集約規則の個体（AggregateAll / AggregateAny）。実体間結合がここから向きを読む
        # ので、コードが参照できなければオントロジーに置いた意味が無い（不変条件 5）。
        (SBP.AggregationRule, "individual"),
        # アクション型と承認ロールの個体。意味契約（actions/*.yaml）とコードがこれを参照する。
        (SBP.ActionType, "individual"),
        (SBP.Role, "individual"),
        # 観測可能量（SOSA）。プローブの定義と Observation の記述からコードが参照する。
        (SOSA.ObservableProperty, "observable_property"),
    ]
    seen: dict[str, Term] = {}
    for rdf_type, kind in kinds:
        for subject in graph.subjects(RDF.type, rdf_type):
            if not isinstance(subject, URIRef):
                continue
            iri = str(subject)
            prefix = _prefix_of(iri)
            if prefix is None or iri in seen:
                continue
            seen[iri] = Term(
                iri=iri,
                prefix=prefix,
                local_name=_local_name(iri, prefix),
                kind=kind,
                label_ja=_text(graph, subject, RDFS.label, "ja"),
                label_en=_text(graph, subject, RDFS.label, "en"),
                definition=_text(graph, subject, SKOS.definition),
                layer=_text(graph, subject, SBP.layer),
                owner=_text(graph, subject, SBP.owner),
            )
    return sorted(seen.values(), key=lambda t: (t.prefix, t.local_name))


def event_types(graph: Graph) -> list[Term]:
    """事象型の用語だけを返す（JSON Schema の生成元）。"""
    return [t for t in collect_terms(graph) if t.kind == "event_type"]


def fluent_types(graph: Graph) -> list[Term]:
    """フルーエント型の用語だけを返す。"""
    return [t for t in collect_terms(graph) if t.kind == "fluent_type"]


def initiates_terminates(graph: Graph) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """``initiates`` / ``terminates`` の組を返す。

    各組は (事象型のローカル名, フルーエント型のローカル名)。
    """
    out: list[set[tuple[str, str]]] = [set(), set()]
    for index, predicate in enumerate((SBP.initiates, SBP.terminates)):
        for event, fluent in graph.subject_objects(predicate):
            e_prefix, f_prefix = _prefix_of(str(event)), _prefix_of(str(fluent))
            if e_prefix is None or f_prefix is None:
                continue
            out[index].add((_local_name(str(event), e_prefix), _local_name(str(fluent), f_prefix)))
    return out[0], out[1]
