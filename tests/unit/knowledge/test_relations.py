"""強い関係の読み出しのテスト（M7 の運用タスク）。

組み込み pyoxigraph を使う（サーバ不要、knowledge ルール）。

重点:

1. **推移閉包を展開しない** — 展開すると同じ証拠が祖先へ複数の経路で伝わって二重に数えられる。
2. **集約規則が無いフルーエントを返さない** — 既定値を埋めると宣言忘れが強い仮定として効く。
3. **事前を持っていない実体を含む辺を落とせる** — ``propagate`` は全実体の事前を要求する。
4. **打ち切りを黙ってやらない** — 上限超過は警告に出す。
"""

from __future__ import annotations

from sbp.core import vocab
from sbp.knowledge.relations import (
    aggregation_rules,
    group_by_whole,
    part_whole_edges,
)
from sbp.knowledge.store import EmbeddedGraphStore

LINE = "https://sbp.example/site/siteA/asset/LINE-1"
PUMP_A = "https://sbp.example/site/siteA/asset/P-101"
PUMP_B = "https://sbp.example/site/siteA/asset/P-102"
BEARING = "https://sbp.example/site/siteA/asset/B-1"
GRAPH = "urn:sbp:test:relations"


def _store() -> EmbeddedGraphStore:
    """部分−全体と集約規則を入れたストア。

    ``B-1 partOf P-101 partOf LINE-1`` の 2 段にしてあるのは、推移閉包を展開していないことを
    確かめるため。
    """
    store = EmbeddedGraphStore()
    turtle = f"""
    <{PUMP_A}> <{vocab.core.partOf}> <{LINE}> .
    <{PUMP_B}> <{vocab.core.partOf}> <{LINE}> .
    <{BEARING}> <{vocab.core.partOf}> <{PUMP_A}> .
    <{vocab.ent.Running}> <{vocab.core.aggregatesAs}> <{vocab.core.AggregateAll}> .
    <{vocab.ent.Faulted}> <{vocab.core.aggregatesAs}> <{vocab.core.AggregateAny}> .
    """
    store.load_turtle(turtle, graph=GRAPH)
    return store


class TestPartWholeEdges:
    """``partOf`` の読み出し。"""

    def test_edges_are_read(self) -> None:
        """宣言した辺がすべて返る。"""
        edges = part_whole_edges(_store())
        assert (PUMP_A, LINE) in edges
        assert (PUMP_B, LINE) in edges
        assert (BEARING, PUMP_A) in edges

    def test_the_transitive_closure_is_not_expanded(self) -> None:
        """``B-1 partOf LINE-1`` は返らない（1 段だけ）。

        ``partOf`` は ``owl:TransitiveProperty`` だが、閉包を結合に入れると同じ証拠が
        **孫 → 子 → 親 と 孫 → 親 の両方の経路で伝わって二重に数えられる**。多段の伝播は
        BP の反復が担う仕事である。
        """
        edges = part_whole_edges(_store())
        assert (BEARING, LINE) not in edges
        assert len(edges) == 3

    def test_the_order_is_stable(self) -> None:
        """並びが安定している（結合の構築が実行ごとに変わらない）。"""
        store = _store()
        assert part_whole_edges(store) == part_whole_edges(store)

    def test_an_empty_graph_yields_no_edges(self) -> None:
        """関係が無ければ空（例外にしない）。"""
        assert part_whole_edges(EmbeddedGraphStore()) == []

    def test_the_limit_is_respected(self) -> None:
        """上限で打ち切る（辺数に比例して BP が重くなる）。"""
        edges = part_whole_edges(_store(), limit=2)
        assert len(edges) == 2


class TestAggregationRules:
    """集約規則の読み出し。"""

    def test_rules_are_read_per_fluent(self) -> None:
        """フルーエント型ごとに規則が付く。

        同じ部分−全体関係でも ``Running`` は all（全ポンプが動いてラインが動く）、
        ``Faulted`` は any（1 台でも故障すればラインは故障）——**関係だけ見て規則は決まらない**。
        """
        rules = aggregation_rules(_store())
        assert rules[str(vocab.ent.Running)] == str(vocab.core.AggregateAll)
        assert rules[str(vocab.ent.Faulted)] == str(vocab.core.AggregateAny)

    def test_undeclared_fluents_are_absent(self) -> None:
        """規則が無いフルーエントは返らない。**既定値を埋めない。**

        埋めると、宣言忘れが「全部分が成立して初めて」という強い仮定として静かに効く。
        """
        rules = aggregation_rules(_store())
        assert str(vocab.ent.UnderMaintenance) not in rules

    def test_an_empty_graph_yields_no_rules(self) -> None:
        """規則が無ければ空。"""
        assert aggregation_rules(EmbeddedGraphStore()) == {}


class TestGroupByWhole:
    """全体ごとのまとめと絞り込み。"""

    def test_parts_are_grouped(self) -> None:
        """同じ全体の部分がまとまる。"""
        grouped = group_by_whole(part_whole_edges(_store()))
        assert sorted(grouped[LINE]) == sorted([PUMP_A, PUMP_B])
        assert grouped[PUMP_A] == [BEARING]

    def test_edges_with_unknown_entities_are_dropped(self) -> None:
        """事前を持っていない実体を含む辺は落ちる。

        ``propagate`` は結合が参照する実体すべての事前を要求するので、ここで絞らないと
        例外になる。ワーカーは観測した実体の事前しか持たない。
        """
        grouped = group_by_whole(part_whole_edges(_store()), known_entities=[LINE, PUMP_A])
        assert grouped == {LINE: [PUMP_A]}

    def test_an_empty_known_set_drops_everything(self) -> None:
        """事前が何も無ければ結合も無い（空集合を「絞らない」と解釈しない）。"""
        assert group_by_whole(part_whole_edges(_store()), known_entities=[]) == {}

    def test_none_means_no_filtering(self) -> None:
        """``None`` は「絞らない」。空集合との違いを保つ。"""
        assert len(group_by_whole(part_whole_edges(_store()), known_entities=None)) == 2
