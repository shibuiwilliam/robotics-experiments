"""資産台帳の投影グラフ（ADR-0016、設計書 §7.1 のマスタ）。

台帳は**アサーション + 投影**で持つ。台帳の各行が主張する文（``rdf:type`` / ``sbp:partOf``）は
observed の二時制アサーションとして主信念グラフに追記し（不変条件 1・2）、現在有効な分を素の三つ組に
して ``urn:sbp:g:master:<site>`` に置く。読み手（``entities_of_type`` / ``most_specific_types`` /
``part_whole_edges``）は素の三つ組を読むので、投影があれば変更なしで台帳の設備を返す。

**投影は文ではない。** 文から作り直せる派生物なので、同期のたびに置き換える（「状態はイベントの
投影」）。置き換えは不変条件 2（アサーションを UPDATE / DELETE しない）に当たらない。

**SPARQL 文字列はこの層に閉じる**（knowledge ルール）。``integration`` は三つ組の列を渡す
だけである。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sbp.core.logging import get_logger
from sbp.knowledge.graphs import master_graph

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.knowledge.store import GraphStore

__all__ = ["RDF_TYPE", "rebuild_master_projection"]

log = get_logger(__name__)

RDF_TYPE: Final = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
"""``rdf:type``。台帳の型の文の述語（W3C の語彙は ``vocab`` の生成対象に入っていない）。"""


def rebuild_master_projection(
    store: GraphStore, *, site_id: str, triples: Sequence[tuple[str, str, str]]
) -> int:
    """台帳の投影グラフを、渡した三つ組で作り直す。

    削除と追加を **1 つの更新要求**で送る。分けると、その間に読んだ接地ワーカーが空の台帳を見て
    「接地対象 0 件」になる。

    Args:
        store: グラフストア。
        site_id: 拠点識別子。
        triples: ``(主語 IRI, 述語 IRI, 目的語 IRI)`` の列（現在有効な台帳の文）。

    Returns:
        投影に置いた三つ組の数。
    """
    graph = master_graph(site_id)
    body = " .\n    ".join(f"<{s}> <{p}> <{o}>" for s, p, o in triples)
    insert = f"INSERT DATA {{ GRAPH <{graph}> {{\n    {body} .\n}} }}" if triples else ""
    drop = f"DROP SILENT GRAPH <{graph}>"
    store.update(f"{drop} ;\n{insert}" if insert else drop)
    log.info("knowledge.master_projection_rebuilt", site=site_id, triples=len(triples))
    return len(triples)
