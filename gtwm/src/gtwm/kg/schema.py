"""`Belief`（世界モデル/wm/grounding 契約の出力）の pydantic モデルと RDF 変換。

world_model.md の接地層契約：Probe(slots) -> list[Belief]。

NOTE（RDF-star からの逸脱、要 ADR）：ontology.md / CLAUDE.md は信念を RDF-star の
埋め込み三つ組（`<< :s :p :o >> gt:confidence 0.93 ; ...`）で格納する前提だが、
pyproject.toml が固定する rdflib 7.6.0 には Turtle-star/SPARQL-star のパーサ・
プラグインが存在しない（`rdflib.plugin.plugins(kind=Parser)` に turtle-star 相当が無く、
`<< ... >>` 構文は N3 パーサが `BadSyntax` で拒否することを確認済み）。pyoxigraph は
RDF-star をネイティブサポートするが、`KGStore` は rdflib/Oxigraph の2実装で同じ
セマンティクスを保証する契約のため、ここでは標準 RDF 具象化
（reification: `_:b a rdf:Statement, gt:Belief ; rdf:subject s ; rdf:predicate p ;
rdf:object o ; gt:confidence ... .`）を両バックエンド共通の表現として採用する。
将来 RDF-star が必要になった場合は ADR で採否を判断すること。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import rdflib
from pydantic import BaseModel, Field
from rdflib import RDF, XSD, BNode, URIRef
from rdflib import Literal as RdfLiteral

GT = rdflib.Namespace("https://example.org/gtwm/gt#")

BeliefSource = Literal["wm", "anchor", "human", "record"]

_SOURCE_TO_IRI: dict[BeliefSource, URIRef] = {
    "wm": GT.WM,
    "anchor": GT.AnchorSource,
    "human": GT.Human,
    "record": GT.Record,
}


_PREFIXES: dict[str, Any] = {
    "gt": GT,
    "rdf": rdflib.RDF,
    "rdfs": rdflib.RDFS,
    "owl": rdflib.OWL,
    "xsd": rdflib.XSD,
}


def _is_literal(value: str) -> bool:
    """既知プレフィックスのCURIEでも完全なIRIでもなければリテラルとみなす。"""
    if value.startswith(("http://", "https://")):
        return False
    if ":" in value and value.split(":", 1)[0] in _PREFIXES:
        return False
    return True


def _to_typed_literal(value: str) -> RdfLiteral:
    """リテラル文字列を、数値として解釈できれば xsd:integer/xsd:double に、それ以外は
    プレーンな文字列リテラルにする（`gt:capacity` 等の数値プロパティが SPARQL の
    数値比較で使えるようにするため）。
    """
    try:
        return RdfLiteral(int(value), datatype=XSD.integer)
    except ValueError:
        pass
    try:
        return RdfLiteral(float(value), datatype=XSD.double)
    except ValueError:
        pass
    return RdfLiteral(value)


def _to_uriref(value: str) -> URIRef:
    """`gt:Foo` / `rdf:type` 等の CURIE、または完全な IRI 文字列を URIRef に変換する。"""
    if ":" in value and not value.startswith(("http://", "https://")):
        prefix, local = value.split(":", 1)
        if prefix in _PREFIXES:
            return _PREFIXES[prefix][local]
    return URIRef(value)


class Belief(BaseModel):
    """接地層（α）の出力。subject-predicate-object の主張に確信度・出所・時間を付与する。

    RDF-star では `<< subject predicate object >>` に対する埋め込み三つ組として
    confidence/source/validFrom/validTo/transactionTime/latentRef を付与する。
    """

    subject: str = Field(description="対象個体。`gt:Pallet_0042` 形式のCURIE。")
    predicate: str = Field(description="述語プロパティ。`gt:currentZone` 形式のCURIE。")
    object: str = Field(description="目的語。個体CURIEまたはリテラル文字列。")
    confidence: float = Field(ge=0.0, le=1.0)
    source: BeliefSource
    valid_from: datetime
    valid_to: datetime | None = None
    transaction_time: datetime = Field(description="システムがこの信念を記録した時刻。")
    latent_ref: str | None = Field(
        default=None, description="対応する gt:LatentRef 個体のCURIE（任意）。"
    )

    def to_reified_triples(self) -> list[tuple[URIRef | BNode, URIRef, Any]]:
        """標準 RDF 具象化（reification）によるトリプル列を返す（上記 NOTE 参照）。

        `_:b a rdf:Statement, gt:Belief ; rdf:subject s ; rdf:predicate p ;
        rdf:object o ; gt:confidence c ; gt:source src ; gt:validFrom vf ;
        [gt:validTo vt ;] gt:transactionTime tt [; gt:latentRef lr] .`
        """
        s = _to_uriref(self.subject)
        p = _to_uriref(self.predicate)
        o: URIRef | RdfLiteral = (
            _to_typed_literal(self.object) if _is_literal(self.object) else _to_uriref(self.object)
        )
        stmt = BNode()

        triples: list[tuple[URIRef | BNode, URIRef, Any]] = [
            (stmt, RDF.type, RDF.Statement),
            (stmt, RDF.type, GT.Belief),
            (stmt, RDF.subject, s),
            (stmt, RDF.predicate, p),
            (stmt, RDF.object, o),
            (stmt, GT.confidence, RdfLiteral(self.confidence, datatype=XSD.double)),
            (stmt, GT.source, _SOURCE_TO_IRI[self.source]),
            (stmt, GT.validFrom, RdfLiteral(self.valid_from.isoformat(), datatype=XSD.dateTime)),
            (
                stmt,
                GT.transactionTime,
                RdfLiteral(self.transaction_time.isoformat(), datatype=XSD.dateTime),
            ),
        ]
        if self.valid_to is not None:
            triples.append(
                (stmt, GT.validTo, RdfLiteral(self.valid_to.isoformat(), datatype=XSD.dateTime))
            )
        if self.latent_ref is not None:
            triples.append((stmt, GT.latentRef, _to_uriref(self.latent_ref)))
        return triples


__all__ = ["GT", "Belief", "BeliefSource"]
