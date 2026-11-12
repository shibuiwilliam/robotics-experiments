"""接地対象の実体を知識グラフから引く（M8-02）。

接地ワーカーは「どの実体の潜在を読んでプローブに通すか」を決めなければならない。設定に
一覧を書くと、設備が増えたときに**設定を直し忘れた分だけ静かに接地されない**——不一致も
信念も出ないので、気付く手がかりが無い。知識グラフは設備の登録簿そのものなので、そこから引く。

**SPARQL 文字列はこの層に閉じる**（knowledge ルール）。``bridge`` と ``services`` は
IRI の列を受け取るだけである。

**部分クラスを展開する。** ``rdfs:subClassOf*`` のプロパティパスを使うので、``Asset`` を
指定すれば ``RotatingEquipment`` の個体も返る。OWL RL 推論を走らせずに型の階層を辿れる
（``ontology/imports/`` が未取得でも動く）。

**拠点は IRI の規約で絞る。** ``https://sbp.example/site/<拠点>/<種別>/<識別子>`` という
形がリポジトリ全体の規約で、拠点を表す述語は置いていない。規約に頼るのは弱い根拠なので、
絞り込みの結果が 0 件なら呼び出し側が気付けるよう件数をログに出す。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.knowledge.store import GraphStore

__all__ = ["entities_of_type", "most_specific_types", "site_prefix"]

log = get_logger(__name__)

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_RDFS_SUBCLASS_OF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


def site_prefix(site: str) -> str:
    """拠点の IRI 接頭辞の断片（``/site/<拠点>/``）。

    Args:
        site: 拠点識別子。
    """
    return f"/site/{site}/"


def entities_of_type(
    store: GraphStore,
    *,
    types: Sequence[str],
    site: str | None = None,
    limit: int = 10_000,
) -> list[str]:
    """指定した型（とその部分クラス）の個体 IRI を返す。

    Args:
        store: グラフストア。
        types: 型 IRI の列。**``vocab`` から渡す**（不変条件 5: IRI をコードに直書きしない）。
        site: 拠点識別子。渡すと IRI の規約（``/site/<拠点>/``）で絞る。
        limit: 返す件数の上限。超えたら警告を出す——黙って切ると「設備がこれだけしかない」
            ように見える。

    Returns:
        個体 IRI の列（辞書順、重複なし）。

    Raises:
        ValueError: ``types`` が空の場合。型を指定しない問い合わせは全個体を返すことになり、
            プローブを持たない実体にも推論を走らせる。
    """
    if not types:
        raise ValueError(
            "entities_of_type requires at least one type; an unfiltered query would return "
            "every individual and ground entities that have no probe"
        )
    type_values = " ".join(f"<{iri}>" for iri in types)
    site_filter = (
        "" if site is None else f'\n      FILTER(CONTAINS(STR(?entity), "{site_prefix(site)}"))'
    )
    # 名前付きグラフと既定グラフの両方を見る。オントロジーは名前付きグラフに投入され、
    # インスタンスはアサーションのグラフに入るので、既定グラフだけを見ると何も返らない
    # （`sbp.knowledge.relations` と同じ理由。所見 45）。
    #
    # **型の宣言と部分クラスの階層を別の段として書く。** `a/rdfs:subClassOf*` と 1 本の
    # プロパティパスで書くと、パス全体が同じグラフ内で閉じることを要求されるため、
    # 「インスタンスは信念グラフ・スキーマはオントロジーのグラフ」という実際の配置では
    # 1 件も返らない（テストで判明）。段を分けると、各段が別のグラフで成立してよい。
    #
    # `subClassOf*` の 0 段は項の同一性なので、型が直接宣言されている場合もこの形で拾える
    # （既定グラフが空でも成立する）。
    sparql = f"""
    SELECT DISTINCT ?entity WHERE {{
      VALUES ?type {{ {type_values} }}
      {{
        {{ ?entity <{_RDF_TYPE}> ?class }}
        UNION
        {{ GRAPH ?instance_graph {{ ?entity <{_RDF_TYPE}> ?class }} }}
      }}
      {{
        {{ ?class <{_RDFS_SUBCLASS_OF}>* ?type }}
        UNION
        {{ GRAPH ?schema_graph {{ ?class <{_RDFS_SUBCLASS_OF}>* ?type }} }}
      }}
      FILTER(isIRI(?entity)){site_filter}
    }}
    ORDER BY ?entity
    LIMIT {limit + 1}
    """
    rows = store.query(sparql)
    truncated = len(rows) > limit
    found = [entity for row in rows[:limit] if (entity := row.get("entity")) is not None]
    if truncated:
        log.warning(
            "knowledge.entities_truncated",
            limit=limit,
            detail="型に一致する個体が上限を超えた。接地対象が欠けている（黙って切っていない）",
        )
    log.info(
        "knowledge.entities_read",
        types=list(types),
        site=site,
        found=len(found),
        truncated=truncated,
    )
    return found


def most_specific_types(
    store: GraphStore,
    *,
    types: Sequence[str],
    site: str | None = None,
    limit: int = 10_000,
) -> dict[str, str]:
    """個体ごとに、指定した型のうち最も具体的なものを返す（M8-30）。

    照合の深刻度は実体の ObjectType で業務価値（``value/*.yaml``）を引く。登録は ``Pump`` と
    上位の ``RotatingEquipment`` のように階層の複数の段にありうるので、**個体が当たる登録型の
    うち、他の登録型の上位でないもの**を選ぶ。部分クラスは :func:`entities_of_type` と同じく
    展開するので、登録の無い型の個体は登録のある最も近い上位型に落ちる。

    どの登録型の下にも無い個体は返さない——既定値（基準額の半分）を当てるのは呼び出し側の
    レジストリの役目で、ここで既定を作ると二重定義になる。

    Args:
        store: グラフストア。
        types: 候補の型 IRI の列（業務価値が登録されている型）。
        site: 拠点（``None`` なら全拠点）。
        limit: 読む ``(個体, 型)`` の組の上限。超えたら警告を出す。

    Returns:
        個体 IRI → 型 IRI。比較できない型（互いに上位でない）に同時に当たる個体は、
        辞書順で最初の型を採る（周期ごとに結果が揺れないため）。

    Raises:
        ValueError: ``types`` が空の場合。
    """
    if not types:
        raise ValueError("most_specific_types requires at least one type")
    candidates = _registered_types_by_entity(store, types=types, site=site, limit=limit)
    broader = _broader_pairs(store, types=types)
    resolved: dict[str, str] = {}
    for entity, matched in candidates.items():
        # 他の候補の上位型に当たるものを落とす（Pump があれば RotatingEquipment を捨てる）
        narrowest = sorted(
            candidate
            for candidate in matched
            if not any((other, candidate) in broader for other in matched if other != candidate)
        )
        resolved[entity] = narrowest[0]
    log.info("knowledge.most_specific_types_read", site=site, entities=len(resolved))
    return resolved


def _registered_types_by_entity(
    store: GraphStore, *, types: Sequence[str], site: str | None, limit: int
) -> dict[str, set[str]]:
    """個体 → 当たる候補型の集合（部分クラスを展開する）。"""
    type_values = " ".join(f"<{iri}>" for iri in types)
    site_filter = (
        "" if site is None else f'\n      FILTER(CONTAINS(STR(?entity), "{site_prefix(site)}"))'
    )
    # 型の宣言と部分クラスの階層を別の段で書く理由は `entities_of_type` と同じ（所見 57）
    sparql = f"""
    SELECT DISTINCT ?entity ?type WHERE {{
      VALUES ?type {{ {type_values} }}
      {{
        {{ ?entity <{_RDF_TYPE}> ?class }}
        UNION
        {{ GRAPH ?instance_graph {{ ?entity <{_RDF_TYPE}> ?class }} }}
      }}
      {{
        {{ ?class <{_RDFS_SUBCLASS_OF}>* ?type }}
        UNION
        {{ GRAPH ?schema_graph {{ ?class <{_RDFS_SUBCLASS_OF}>* ?type }} }}
      }}
      FILTER(isIRI(?entity)){site_filter}
    }}
    ORDER BY ?entity ?type
    LIMIT {limit + 1}
    """
    rows = store.query(sparql)
    if len(rows) > limit:
        log.warning(
            "knowledge.entity_types_truncated",
            limit=limit,
            detail="(個体, 型) の組が上限を超えた。業務価値の引き当てが欠けている",
        )
    found: dict[str, set[str]] = {}
    for row in rows[:limit]:
        entity, matched = row.get("entity"), row.get("type")
        if entity is not None and matched is not None:
            found.setdefault(entity, set()).add(matched)
    return found


def _broader_pairs(store: GraphStore, *, types: Sequence[str]) -> set[tuple[str, str]]:
    """候補型どうしの ``(部分型, 上位型)`` の組（``rdfs:subClassOf+``）。"""
    type_values = " ".join(f"<{iri}>" for iri in types)
    sparql = f"""
    SELECT DISTINCT ?narrow ?broad WHERE {{
      VALUES ?narrow {{ {type_values} }}
      VALUES ?broad {{ {type_values} }}
      {{
        {{ ?narrow <{_RDFS_SUBCLASS_OF}>+ ?broad }}
        UNION
        {{ GRAPH ?schema_graph {{ ?narrow <{_RDFS_SUBCLASS_OF}>+ ?broad }} }}
      }}
      FILTER(?narrow != ?broad)
    }}
    """
    pairs: set[tuple[str, str]] = set()
    for row in store.query(sparql):
        narrow, broad = row.get("narrow"), row.get("broad")
        if narrow is not None and broad is not None:
            pairs.add((narrow, broad))
    return pairs
