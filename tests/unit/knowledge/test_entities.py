"""接地対象の実体の問い合わせのテスト（M8-02、ADR-0009）。

組み込み pyoxigraph を使う（サーバ不要、knowledge ルール）。

重点:

1. **部分クラスを展開する** — `Asset` を指定すれば `RotatingEquipment` の個体も返る。
   展開しないと、ドメイン型で登録された設備が静かに接地対象から漏れる。
2. **拠点で絞れる** — 1 プロセス 1 拠点なので、他拠点の実体を接地しない。
3. **型を指定しない問い合わせを拒否する** — 全個体を返すと、プローブを持たない実体にも
   推論が走る。
4. **名前付きグラフと既定グラフの両方を見る** — 既定グラフだけを見ると何も返らない（所見 45）。
"""

from __future__ import annotations

import pytest

from sbp.core import vocab
from sbp.knowledge.entities import entities_of_type, most_specific_types, site_prefix
from sbp.knowledge.store import EmbeddedGraphStore

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_SUBCLASS_OF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"

PUMP_A = "https://sbp.example/site/siteA/asset/P-101"
PUMP_B = "https://sbp.example/site/siteA/asset/P-102"
PUMP_OTHER_SITE = "https://sbp.example/site/siteB/asset/P-201"
CARRIER = "https://sbp.example/site/siteA/carrier/PAL-001"
GRAPH = "urn:sbp:test:entities"


def _store() -> EmbeddedGraphStore:
    """設備 2 台（うち 1 台はドメイン型）、他拠点 1 台、搬送具 1 台を入れたストア。

    ``RotatingEquipment rdfs:subClassOf Asset`` を**スキーマとして別グラフに**入れている
    ——実運用でもオントロジーとインスタンスは別のグラフに入る。
    """
    store = EmbeddedGraphStore()
    store.load_turtle(
        f"<{vocab.ent.RotatingEquipment}> <{RDFS_SUBCLASS_OF}> <{vocab.core.Asset}> .",
        graph="urn:sbp:test:schema",
    )
    store.load_turtle(
        f"""
        <{PUMP_A}> <{RDF_TYPE}> <{vocab.core.Asset}> .
        <{PUMP_B}> <{RDF_TYPE}> <{vocab.ent.RotatingEquipment}> .
        <{PUMP_OTHER_SITE}> <{RDF_TYPE}> <{vocab.core.Asset}> .
        <{CARRIER}> <{RDF_TYPE}> <{vocab.core.UnidentifiedPhysicalObject}> .
        """,
        graph=GRAPH,
    )
    return store


def test_direct_type_is_found() -> None:
    """型が直接宣言されている個体が返る。"""
    found = entities_of_type(_store(), types=(vocab.core.Asset,), site="siteA")
    assert PUMP_A in found


def test_subclass_instances_are_found() -> None:
    """部分クラスの個体も返る（``rdfs:subClassOf*`` を辿る）。

    辿らないと、ドメイン型（`RotatingEquipment`）で登録された設備が接地対象から漏れる。
    OWL RL 推論を走らせずに済むのが要点である（`ontology/imports/` が未取得でも動く）。
    """
    found = entities_of_type(_store(), types=(vocab.core.Asset,), site="siteA")
    assert PUMP_B in found, "RotatingEquipment は Asset の部分クラスなので含まれる"


def test_other_sites_are_excluded() -> None:
    """拠点で絞れる（1 プロセス 1 拠点）。"""
    found = entities_of_type(_store(), types=(vocab.core.Asset,), site="siteA")
    assert PUMP_OTHER_SITE not in found
    assert entities_of_type(_store(), types=(vocab.core.Asset,), site="siteB") == [PUMP_OTHER_SITE]


def test_without_site_all_sites_are_returned() -> None:
    """拠点を指定しなければ全拠点が返る（再接地のような全体処理のため）。"""
    found = entities_of_type(_store(), types=(vocab.core.Asset,))
    assert {PUMP_A, PUMP_B, PUMP_OTHER_SITE} <= set(found)


def test_unrelated_types_are_excluded() -> None:
    """指定していない型の個体は返らない。"""
    found = entities_of_type(_store(), types=(vocab.core.Asset,), site="siteA")
    assert CARRIER not in found


def test_results_are_sorted_and_unique() -> None:
    """辞書順・重複なし（複数グラフに同じ三つ組があっても 1 件）。"""
    found = entities_of_type(
        _store(), types=(vocab.core.Asset, vocab.ent.RotatingEquipment), site="siteA"
    )
    assert found == sorted(found)
    assert len(found) == len(set(found))


def test_empty_types_is_refused() -> None:
    """型を指定しない問い合わせを拒否する。

    全個体を返すと、プローブを持たない実体にも推論を走らせることになる。
    """
    with pytest.raises(ValueError, match="at least one type"):
        entities_of_type(_store(), types=())


def test_no_match_returns_empty() -> None:
    """一致が無ければ空（例外にしない）。呼び出し側が件数で判断する。"""
    assert entities_of_type(_store(), types=(vocab.core.Asset,), site="siteZ") == []


def test_limit_truncates_without_raising() -> None:
    """上限で打ち切る（警告は出すが例外にしない）。"""
    found = entities_of_type(_store(), types=(vocab.core.Asset,), site="siteA", limit=1)
    assert len(found) == 1


def test_site_prefix_shape() -> None:
    """拠点の接頭辞は IRI の規約どおり。"""
    assert site_prefix("siteA") == "/site/siteA/"


class TestMostSpecificTypes:
    """実体ごとに「指定した型のうち最も具体的なもの」を返す（M8-30）。

    照合の深刻度は実体の ObjectType で業務価値を引く（``value/*.yaml``）。登録は
    ``Pump`` と上位の ``RotatingEquipment`` の両方にあり、ポンプには ``Pump`` の値を、
    登録の無い回転機械には ``RotatingEquipment`` の値を当てたい。

    重点:

    1. **部分クラスを展開する** — 登録の無い型の個体も、登録のある上位型に落ちる。
    2. **最も具体的な型を選ぶ** — ポンプが ``RotatingEquipment`` の値で評価されない。
    3. **登録のある型の下に無い個体は返さない** — 呼び出し側の既定値（基準額の半分）に任せる。
    """

    COMPRESSOR_CLASS = "urn:sbp:test:class:Compressor"
    COMPRESSOR = "https://sbp.example/site/siteA/asset/C-1"
    LINE = "https://sbp.example/site/siteA/line/L-3"
    TYPES = (vocab.ent.Pump, vocab.ent.RotatingEquipment, vocab.ent.ProductionLine)

    def _store(self) -> EmbeddedGraphStore:
        store = EmbeddedGraphStore()
        store.load_turtle(
            f"""
            <{vocab.ent.RotatingEquipment}> <{RDFS_SUBCLASS_OF}> <{vocab.core.Asset}> .
            <{vocab.ent.Pump}> <{RDFS_SUBCLASS_OF}> <{vocab.ent.RotatingEquipment}> .
            <{self.COMPRESSOR_CLASS}> <{RDFS_SUBCLASS_OF}> <{vocab.ent.RotatingEquipment}> .
            """,
            graph="urn:sbp:test:schema",
        )
        store.load_turtle(
            f"""
            <{PUMP_A}> <{RDF_TYPE}> <{vocab.ent.Pump}> .
            <{self.COMPRESSOR}> <{RDF_TYPE}> <{self.COMPRESSOR_CLASS}> .
            <{self.LINE}> <{RDF_TYPE}> <{vocab.ent.ProductionLine}> .
            <{PUMP_OTHER_SITE}> <{RDF_TYPE}> <{vocab.ent.Pump}> .
            <{CARRIER}> <{RDF_TYPE}> <{vocab.core.UnidentifiedPhysicalObject}> .
            """,
            graph=GRAPH,
        )
        return store

    def test_the_most_specific_registered_type_wins(self) -> None:
        """ポンプは ``Pump``（上位の ``RotatingEquipment`` にも当たるが、具体的なほう）。"""
        found = most_specific_types(self._store(), types=self.TYPES, site="siteA")
        assert found[PUMP_A] == vocab.ent.Pump

    def test_an_unregistered_subclass_falls_back_to_its_registered_ancestor(self) -> None:
        found = most_specific_types(self._store(), types=self.TYPES, site="siteA")
        assert found[self.COMPRESSOR] == vocab.ent.RotatingEquipment

    def test_entities_outside_the_types_are_absent(self) -> None:
        """呼び出し側の既定値に任せる（ここで既定を作らない）。"""
        found = most_specific_types(self._store(), types=self.TYPES, site="siteA")
        assert CARRIER not in found
        assert found[self.LINE] == vocab.ent.ProductionLine

    def test_other_sites_are_excluded(self) -> None:
        found = most_specific_types(self._store(), types=self.TYPES, site="siteA")
        assert PUMP_OTHER_SITE not in found

    def test_the_order_of_the_types_does_not_matter(self) -> None:
        """上位型を先に渡しても結果は同じ（列の順で決めていない）。"""
        store = self._store()
        forward = most_specific_types(store, types=self.TYPES, site="siteA")
        backward = most_specific_types(store, types=tuple(reversed(self.TYPES)), site="siteA")
        assert forward == backward

    def test_empty_types_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one type"):
            most_specific_types(self._store(), types=())
