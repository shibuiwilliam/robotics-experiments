"""IRI生成の唯一の窓口（CLAUDE.md §4: 文字列連結でIRIを作らない）。

名前空間（PROJECT.md §6）:
  オントロジー: https://orx.local/onto/{upper|cap|st|prov|biz}#
  個体:        https://orx.local/id/{type}/{key}
"""

from __future__ import annotations

import re

_ONTO_ROOT = "https://orx.local/onto/"
_ID_ROOT = "https://orx.local/id/"

NS_UPPER = _ONTO_ROOT + "upper#"
NS_CAP = _ONTO_ROOT + "cap#"
NS_ST = _ONTO_ROOT + "st#"
NS_PROV = _ONTO_ROOT + "prov#"
NS_BIZ = _ONTO_ROOT + "biz#"
NS_NORM = _ONTO_ROOT + "norm#"

XSD_DOUBLE = "http://www.w3.org/2001/XMLSchema#double"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

_TERM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def _term(ns: str, name: str) -> str:
    if not _TERM_RE.match(name):
        raise ValueError(f"invalid ontology term: {name!r}")
    return ns + name


def upper(name: str) -> str:
    """orx-upper: 上位オントロジー語彙のIRI。"""
    return _term(NS_UPPER, name)


def cap(name: str) -> str:
    """orx-cap: 能力・行為語彙のIRI。"""
    return _term(NS_CAP, name)


def st(name: str) -> str:
    """orx-st: 時空間・事象語彙のIRI。"""
    return _term(NS_ST, name)


def prov(name: str) -> str:
    """orx-prov: 主体・来歴語彙のIRI。"""
    return _term(NS_PROV, name)


def biz(name: str) -> str:
    """orx-biz: 業務・文書語彙のIRI。"""
    return _term(NS_BIZ, name)


def norm(name: str) -> str:
    """orx-norm: 規範（deontic）語彙のIRI。"""
    return _term(NS_NORM, name)


def entity(type_: str, key: str) -> str:
    """個体IRI: https://orx.local/id/{type}/{key}"""
    if not _KEY_RE.match(type_) or not _KEY_RE.match(key):
        raise ValueError(f"invalid entity type/key: {type_!r}/{key!r}")
    return f"{_ID_ROOT}{type_}/{key}"


def claim(claim_id: str) -> str:
    """クレームのnamed graph IRI（D3方式）。"""
    return entity("claim", claim_id)


def parse_entity(iri: str) -> tuple[str, str]:
    """個体IRIを (type, key) に逆引きする。"""
    if not iri.startswith(_ID_ROOT):
        raise ValueError(f"not an orx entity IRI: {iri!r}")
    type_, _, key = iri[len(_ID_ROOT) :].partition("/")
    if not type_ or not key:
        raise ValueError(f"malformed entity IRI: {iri!r}")
    return type_, key
