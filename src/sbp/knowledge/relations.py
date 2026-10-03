"""強い関係の読み出し（部分−全体、設計書 §8.1、M7 の運用タスク）。

実体間結合（loopy BP）の因子になる関係をオントロジーとインスタンスから引く。

**SPARQL 文字列をこの層に閉じる**（knowledge ルール）。``reasoning`` は ``PartWhole`` の列を
受け取るだけで、クエリの組み立てを知らない。

**読むのは 2 つの別の事実である。**

1. ``<part> sbp:partOf <whole>`` — インスタンスの部分−全体関係。
2. ``<FluentType> sbp:aggregatesAs sbp:AggregateAll|AggregateAny`` — フルーエント型ごとの
   集約規則。「全部分が成立して初めて全体が成立」か「いずれかで成立」か。

集約規則が**フルーエント型に付いている**のが要点である。同じ部分−全体関係でも、
``Running`` は all（全ポンプが動いていてラインが動いている）、``Faulted`` は any
（1 台でも故障すればラインは故障）のように向きが違う。関係だけ見て規則を決められない。

``partOf`` は推移的（``owl:TransitiveProperty``）だが、**ここでは 1 段だけを返す**。
推移閉包を展開して結合に入れると、同じ証拠が祖先へ複数の経路で伝わって二重に数えられる
（孫 → 子 → 親 と 孫 → 親 の両方が因子になる）。多段の伝播は BP の反復が担う仕事である。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.core import vocab
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sbp.knowledge.store import GraphStore

__all__ = [
    "AggregationByFluent",
    "PartWholeEdge",
    "aggregation_rules",
    "group_by_whole",
    "part_whole_edges",
]

log = get_logger(__name__)

type PartWholeEdge = tuple[str, str]
"""``(部分 IRI, 全体 IRI)``。1 段だけの関係。"""

type AggregationByFluent = dict[str, str]
"""フルーエント型 IRI → 集約規則 IRI（``AggregateAll`` / ``AggregateAny``）。"""


def part_whole_edges(store: GraphStore, *, limit: int = 10_000) -> list[PartWholeEdge]:
    """``partOf`` の辺を 1 段ぶん読む。

    推移閉包は展開しない（モジュールの docstring 参照）。

    Args:
        store: グラフストア。
        limit: 返す辺の上限。**上限を置くのは、辺数に比例して BP が重くなるため**。
            打ち切ったことはログに出す——黙って切ると「関係が無い」ように見える。

    Returns:
        ``(部分, 全体)`` の列。
    """
    # 名前付きグラフと既定グラフの両方を見る。オントロジーは名前付きグラフに投入され
    # （`scripts/seed.py`）、インスタンスの関係もアサーションのグラフに入るので、既定グラフ
    # だけを見ると**何も返らない**（実際にそれでテストが落ちて気付いた）。
    # DISTINCT を付けるのは、同じ三つ組が複数のグラフに現れうるため。
    sparql = f"""
    SELECT DISTINCT ?part ?whole WHERE {{
      {{ ?part <{vocab.core.partOf}> ?whole }}
      UNION
      {{ GRAPH ?g {{ ?part <{vocab.core.partOf}> ?whole }} }}
      FILTER(isIRI(?part) && isIRI(?whole))
    }}
    ORDER BY ?whole ?part
    LIMIT {limit + 1}
    """
    rows = store.query(sparql)
    truncated = len(rows) > limit
    edges = [
        (part, whole)
        for row in rows[:limit]
        if (part := row.get("part")) is not None and (whole := row.get("whole")) is not None
    ]
    if truncated:
        log.warning(
            "knowledge.part_whole_truncated",
            limit=limit,
            detail="辺が上限を超えた。結合の対象が欠けている（黙って切っていない）",
        )
    log.debug("knowledge.part_whole_read", edges=len(edges), truncated=truncated)
    return edges


def aggregation_rules(store: GraphStore) -> AggregationByFluent:
    """フルーエント型ごとの集約規則を読む。

    **規則が宣言されていないフルーエントは返さない。** 既定値（all など）を埋めると、
    宣言忘れが「全部分が成立して初めて」という強い仮定として静かに効く。呼び出し側は
    規則の無いフルーエントを結合に入れない、という判断ができる必要がある。

    Args:
        store: グラフストア。

    Returns:
        フルーエント型 IRI → 集約規則 IRI。
    """
    sparql = f"""
    SELECT DISTINCT ?fluent ?rule WHERE {{
      {{ ?fluent <{vocab.core.aggregatesAs}> ?rule }}
      UNION
      {{ GRAPH ?g {{ ?fluent <{vocab.core.aggregatesAs}> ?rule }} }}
      FILTER(isIRI(?fluent) && isIRI(?rule))
    }}
    """
    rules = {
        fluent: rule
        for row in store.query(sparql)
        if (fluent := row.get("fluent")) is not None and (rule := row.get("rule")) is not None
    }
    log.debug("knowledge.aggregation_rules_read", fluents=len(rules))
    return rules


def group_by_whole(
    edges: Iterable[PartWholeEdge], *, known_entities: Sequence[str] | None = None
) -> dict[str, list[str]]:
    """辺を全体ごとにまとめる。

    ``known_entities`` を渡すと、**部分と全体の両方がその集合にある辺だけ**を残す。
    ``propagate`` は結合が参照する実体すべての事前を要求するので、事前が無い実体を含む辺を
    渡すと例外になる——ワーカーは観測した実体の事前しか持たないため、ここで絞る必要がある。

    Args:
        edges: ``(部分, 全体)`` の列。
        known_entities: 事前を持っている実体。``None`` なら絞らない。

    Returns:
        全体 IRI → 部分 IRI の列（順序は安定）。
    """
    known = None if known_entities is None else set(known_entities)
    grouped: dict[str, list[str]] = {}
    for part, whole in edges:
        if known is not None and (part not in known or whole not in known):
            continue
        grouped.setdefault(whole, []).append(part)
    return grouped
