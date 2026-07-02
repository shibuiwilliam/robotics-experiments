"""C10 — アクション書戻し orchestration（キネティック層・IMPROVEMENT.md §3.5）。

アクションレシートを世界グラフへ来歴付きで書き戻し（effect / custody / action-execution）、
監査ストリームへ記録する再利用部品。決定的 S8 リファレンスと（K2 の）LLM 閉ループの双方が使う。

不変条件2: 書込は `WorldGraph.assert_claim` のみ（CLAUDE.md §3.2）。Claim 構築は
`orx.kg.action_claims`（純関数）。id は seeded RNG 由来でリプレイ同一性を保つ。

反実仮想リプレイとの整合（IMPROVEMENT.md §3.1）: 閉ループは条件ごとに物理履歴が分岐するため
「1 記録→多重リプレイ」は不適用。各条件は独立ロールアウトし、seed-paired 検定で比較する
（`loop="closed"`）。本モジュールは記録器であり、その注記は `exp.scope.loop_note` が出す。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import ActionRequestRecord, CustodyRecord
from orx.common.seeding import SeedTree, deterministic_id
from orx.kg.action_claims import (
    action_execution_claims,
    custody_step_claims,
    effect_claim,
)
from orx.kg.world_graph import WorldGraph
from orx.replay.io import RunWriter
from orx.skills.action import ActionReceipt


class WriteBack:
    """レシート → 来歴付き Claim ＋ 監査ストリームへの書戻し。

    custody を持つ（applied かつ効果適用済みの）対象を追跡し、監査完全性の算出に使う。
    `object_iri_of` は barcode → 個体 IRI（既定: id/object/{barcode}）。
    """

    def __init__(
        self,
        graph: WorldGraph,
        seeds: SeedTree,
        writer: RunWriter | None = None,
        object_iri_of=None,
        emit_custody: bool = True,
    ) -> None:
        self._graph = graph
        self._rng = seeds.child("act-writeback").rng()
        self._writer = writer
        self._object_iri_of = object_iri_of or (lambda bc: iri.entity("object", bc))
        self._emit_custody = emit_custody  # False = 来歴語彙を持たない条件（custody を残せない）
        self._step_index = 0
        self.custodied: set[str] = set()  # custody を記録した object_id
        self.exec_iris: list[str] = []

    def _new_id(self) -> str:
        return deterministic_id(self._rng)

    def record(self, receipt: ActionReceipt, prior_zone: str | None) -> str:
        """1 レシートを書き戻す。返り値は ActionExecution の IRI。"""
        robot = receipt.request.robot_id
        agent = iri.entity("agent", robot)
        robot_iri = iri.entity("robot", robot)
        object_iri = self._object_iri_of(receipt.object_id)

        exec_iri, claims = action_execution_claims(
            self._new_id,
            robot_iri,
            object_iri,
            receipt.request.dest_zone,
            status=receipt.status,
            observed_at=receipt.at_time,
            asserted_by=agent,
        )
        for c in claims:
            self._graph.assert_claim(c)
            if self._writer:
                self._writer.append_claim(c)
        self.exec_iris.append(exec_iri)

        if self._writer:
            self._writer.append_action(
                ActionRequestRecord(
                    action_id=receipt.action_id,
                    robot_id=robot,
                    skill=receipt.request.skill,
                    target_barcode=receipt.object_id,
                    target_position=receipt.request.target_position,
                    dest_zone=receipt.request.dest_zone,
                    at_time=receipt.at_time,
                )
            )
            self._writer.append_action_receipt(receipt.to_record())

        if receipt.status == "applied" and receipt.effect_applied:
            self._graph.assert_claim(
                effect_claim(
                    self._new_id,
                    object_iri,
                    receipt.request.dest_zone,
                    asserted_by=agent,
                    observed_at=receipt.at_time,
                )
            )
            if not self._emit_custody:
                self._step_index += 1
                return exec_iri
            for c in custody_step_claims(
                self._new_id,
                object_iri,
                receipt.request.dest_zone,
                step_index=self._step_index,
                asserted_by=agent,
                observed_at=receipt.at_time,
            ):
                self._graph.assert_claim(c)
            self.custodied.add(receipt.object_id)
            if self._writer:
                self._writer.append_custody(
                    CustodyRecord(
                        object_id=receipt.object_id,
                        zone=receipt.request.dest_zone,
                        step_index=self._step_index,
                        by_robot=robot,
                        at_time=receipt.at_time,
                    )
                )
            self._step_index += 1
        return exec_iri


def audit_completeness(write_back: WriteBack, expected_objects: list[str]) -> float:
    """期待される（成功した move）対象のうち custody 連鎖が記録された割合。

    OR-full は全 applied 対象に custody を残す（=1.0）。ベースラインは custody を残さない
    （来歴語彙を持たない）ため < 1.0 になる（H8・監査可能性）。
    """
    if not expected_objects:
        return 1.0
    have = sum(1 for o in expected_objects if o in write_back.custodied)
    return round(have / len(expected_objects), 6)


def reconstruct_custody(graph: WorldGraph, at_time: float) -> dict[str, list[str]]:
    """世界グラフから custody 連鎖を SPARQL で再構成する（CQ・監査クエリ）。

    返り値: object_iri → [zone,...]（stepIndex 昇順）。OR-full の custody が読めることの確認。
    """
    graph.refresh_current_graph(at_time)
    rows = graph.query(
        f"""
        PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX orx-norm: <{iri.NS_NORM}>
        SELECT ?obj ?zone ?idx WHERE {{
          GRAPH <{_current_graph()}> {{
            ?step rdf:type orx-norm:CustodyStep ;
                  orx-norm:custodyOf ?obj ;
                  orx-norm:atZone ?zone ;
                  orx-norm:stepIndex ?idx .
          }}
        }}
        """
    )
    chains: dict[str, list[tuple[int, str]]] = {}
    for r in rows:
        chains.setdefault(r["obj"], []).append((int(r["idx"]), r["zone"]))
    return {obj: [z for _i, z in sorted(steps)] for obj, steps in chains.items()}


def _current_graph() -> str:
    from orx.kg.world_graph import CURRENT_GRAPH

    return CURRENT_GRAPH
