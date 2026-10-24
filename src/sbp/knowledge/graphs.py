"""名前付きグラフの命名（設計書 §6.3、knowledge ルール）。

- 主信念: ``urn:sbp:g:<modality>:<site>:<ontology_version>``
- シナリオ: ``urn:sbp:scenario:<scenario_id>``（``hypothetical`` を隔離する。不変条件 3）
- スキーマ: ``urn:sbp:g:schema:ontology:<ontology_version>``（オントロジー本体）
- 台帳の投影: ``urn:sbp:g:master:<site>``（資産台帳の現在有効な文の素の三つ組。ADR-0016）

様相・拠点・オントロジー版で分割するのは、版を跨いだ as-of クエリと再接地を成立させるため。
"""

from __future__ import annotations

from sbp.core.errors import ModalityError
from sbp.core.modality import Modality

__all__ = ["SCENARIO_PREFIX", "belief_graph", "master_graph", "scenario_graph", "schema_graph"]

SCENARIO_PREFIX = "urn:sbp:scenario:"
"""シナリオグラフの接頭辞。主信念のグラフとは接頭辞で区別できるようにする。"""


def belief_graph(*, modality: Modality, site_id: str, ontology_version: str) -> str:
    """主信念グラフの IRI を返す。

    Args:
        modality: 様相。``hypothetical`` は主信念に入れられない（不変条件 3）。
        site_id: 拠点識別子。
        ontology_version: オントロジー版（semver）。

    Raises:
        ModalityError: ``hypothetical`` を渡した場合。シナリオは :func:`scenario_graph` を使う。
    """
    modality.assert_enters_main_belief()
    return f"urn:sbp:g:{modality.value}:{site_id}:{ontology_version}"


def scenario_graph(scenario_id: str) -> str:
    """シナリオグラフの IRI を返す（``hypothetical`` の隔離先）。

    Args:
        scenario_id: シナリオ識別子。
    """
    if not scenario_id or any(c.isspace() for c in scenario_id):
        raise ModalityError(f"invalid scenario id: {scenario_id!r}")
    if scenario_id.startswith(SCENARIO_PREFIX):
        return scenario_id
    return f"{SCENARIO_PREFIX}{scenario_id}"


def schema_graph(ontology_version: str) -> str:
    """オントロジー本体を格納するグラフの IRI を返す。

    Args:
        ontology_version: オントロジー版（semver）。
    """
    return f"urn:sbp:g:schema:ontology:{ontology_version}"


def master_graph(site_id: str) -> str:
    """資産台帳の投影を格納するグラフの IRI を返す（ADR-0016）。

    **文ではなく投影である。** 台帳の文（observed のアサーション）は主信念グラフにあり、ここには
    その現在有効な分の素の三つ組（``rdf:type`` / ``sbp:partOf``）だけを置く。同期のたびに作り直す。
    主信念グラフの命名（``urn:sbp:g:<modality>:<site>:<version>``）と違って拠点の後ろに ``:`` を
    置かないので、``AssertionStore.query`` の拠点スコープ（``:<site>:``）には入らない。

    Args:
        site_id: 拠点識別子。
    """
    return f"urn:sbp:g:master:{site_id}"


def is_scenario_graph(graph_iri: str) -> bool:
    """シナリオグラフかどうか。

    Args:
        graph_iri: 判定するグラフ IRI。
    """
    return graph_iri.startswith(SCENARIO_PREFIX)
