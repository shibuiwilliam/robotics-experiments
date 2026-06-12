"""T7 SOP検索スイート（H4）— オントロジー誘導検索 vs 素のベクトルRAG。

クエリ: 「受注 X の品の{状況}手順はどの文書か」。正答は受注→SKU→appliesTo＋
situation で一意に決まる文書。
- onto-guided: 識別子スレッド＋文書オントロジーをSPARQL/SQLで辿る（決定的、
  オフラインで測定可能）。
- vector-rag: クエリ文と文書本文のテキスト埋め込みコサイン類似 top-1。
  本計測は OpenAI 埋め込み（live・キャッシュ記録）。stub埋め込みでは無意味。
"""

from __future__ import annotations

from orx.business.db import BusinessDB, WmsRecord
from orx.business.sop import SITUATIONS, SopDoc, doc_iri
from orx.common import iri
from orx.common.providers import TextEmbeddingClient
from orx.common.schemas import StrictModel
from orx.kg.world_graph import CURRENT_GRAPH, WorldGraph


class T7Query(StrictModel):
    qid: str
    text: str
    order_id: str
    situation: str
    truth_doc_id: str


class T7Answer(StrictModel):
    qid: str
    condition: str
    doc_id: str
    truth_doc_id: str
    correct: bool


def generate_queries(record: WmsRecord, docs: list[SopDoc]) -> list[T7Query]:
    by_sku_situation = {(d.applies_to_sku, d.situation): d.doc_id for d in docs}
    queries: list[T7Query] = []
    order_sku = {str(o["order_id"]): str(o["sku"]) for o in record.orders}
    for inst in record.instructions:
        oid = inst["order_id"]
        sku = order_sku[oid]
        for situation, label in SITUATIONS.items():
            truth = by_sku_situation.get((sku, situation))
            if truth is None:
                continue
            queries.append(
                T7Query(
                    qid=f"{oid}-{situation}",
                    text=f"受注 {oid} の品の{label}の手順書はどれか。",
                    order_id=oid,
                    situation=situation,
                    truth_doc_id=truth,
                )
            )
    return queries


def retrieve_onto(graph: WorldGraph, db: BusinessDB, query: T7Query) -> str:
    """オントロジー誘導: 受注→SKU→appliesTo＋situation（GraphRAG的）。"""
    rows = db.query(f"SELECT sku FROM orders WHERE order_id = '{query.order_id}'")
    if not rows:
        return "unknown"
    sku = str(rows[0]["sku"])
    hits = graph.query(
        f"""
        PREFIX orx-biz: <https://orx.local/onto/biz#>
        SELECT ?d WHERE {{
          GRAPH <{CURRENT_GRAPH}> {{
            ?d orx-biz:appliesTo <{iri.entity("sku", sku)}> ;
               orx-biz:situation "{query.situation}" .
          }}
        }}
        """
    )
    if not hits:
        return "unknown"
    return iri.parse_entity(hits[0]["d"])[1]


def retrieve_vector(
    embedder: TextEmbeddingClient, docs: list[SopDoc], query: T7Query
) -> str:
    """素のベクトルRAG: クエリ文と文書本文のコサイン類似 top-1。"""
    doc_vecs = embedder.embed([d.body for d in docs])
    [q_vec] = embedder.embed([query.text])
    best_doc, best_score = "unknown", -2.0
    for doc, vec in zip(docs, doc_vecs, strict=True):
        score = sum(a * b for a, b in zip(q_vec, vec, strict=True))
        if score > best_score:
            best_doc, best_score = doc.doc_id, score
    return best_doc


def evaluate(
    condition: str,
    queries: list[T7Query],
    graph: WorldGraph,
    db: BusinessDB,
    docs: list[SopDoc],
    embedder: TextEmbeddingClient | None,
) -> list[T7Answer]:
    answers: list[T7Answer] = []
    for query in queries:
        if condition == "onto-guided":
            doc_id = retrieve_onto(graph, db, query)
        elif condition == "vector-rag":
            if embedder is None:
                raise ValueError("vector-rag には埋め込みクライアントが必要です")
            doc_id = retrieve_vector(embedder, docs, query)
        else:
            raise ValueError(f"T7の未知の条件: {condition!r}")
        answers.append(
            T7Answer(
                qid=query.qid,
                condition=condition,
                doc_id=doc_id,
                truth_doc_id=query.truth_doc_id,
                correct=doc_id == query.truth_doc_id,
            )
        )
    return answers


__all__ = ["T7Query", "T7Answer", "generate_queries", "retrieve_onto",
           "retrieve_vector", "evaluate", "doc_iri"]
