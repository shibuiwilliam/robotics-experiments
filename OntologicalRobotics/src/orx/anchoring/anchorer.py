"""C5 アンカリング骨格（P0）— 個体化と同一性解決。

P0の解決規則:
  1. 記号ID（バーコード）一致 → 決定的に同一個体へ束ねる
  2. ID無し → 既知個体への空間最近傍ゲート（gate_radius 内）で確率的マッチ
  3. どちらも不成立 → 新規個体を発行

同一性は `orx-upper:anchoredTo`（確信度付き）の改訂可能な主張として出力する。
owl:sameAs は使わない（PROJECT.md §5.2-3）。本格的なスコア融合・マージ/分裂は
P1 (D1) で拡張する。

このモジュールは event.oracle_truth_ids を**読まない**（真値はoracle専用）。
"""

from __future__ import annotations

import math

from orx.common import iri
from orx.common.config import AnchoringParams, ZoneConfig
from orx.common.geometry import zone_of
from orx.common.schemas import AnchorRecord, Claim, PerceptionEvent, StrictModel, Term, Vec3
from orx.common.seeding import SeedTree, deterministic_id


class AnchorResult(StrictModel):
    claims: list[Claim]
    records: list[AnchorRecord]
    assignments: list[str]  # 検出添字 → 個体IRI（oracle採点の対応付けに使う）


class Anchorer:
    def __init__(
        self,
        params: AnchoringParams,
        zones: list[ZoneConfig],
        claim_ttl_s: float,
        seeds: SeedTree,
    ) -> None:
        self.params = params
        self.zones = zones
        self.claim_ttl_s = claim_ttl_s
        self._rng = seeds.child("anchoring").rng()
        self._symbol_to_entity: dict[str, str] = {}
        self._entity_pos: dict[str, Vec3] = {}
        self._typed_entities: set[str] = set()
        self._identified_entities: set[str] = set()
        self._tracks: dict[tuple[str, str], str] = {}  # (robot, entity) -> track IRI
        self._track_seq: dict[str, int] = {}
        self._anchored_tracks: set[str] = set()

    # ----------------------------------------------------------------- public

    def process(self, event: PerceptionEvent) -> AnchorResult:
        claims: list[Claim] = []
        records: list[AnchorRecord] = []
        assignments: list[str] = []
        agent = iri.entity("agent", f"anchoring-{event.robot_id}")
        used_entities: set[str] = set()

        for det in event.detections:
            entity, score, decision = self._resolve(det.symbol_id, det.position, used_entities)
            used_entities.add(entity)
            assignments.append(entity)
            self._entity_pos[entity] = det.position

            track = self._track_for(event.robot_id, entity)
            records.append(
                AnchorRecord(
                    track_id=track,
                    entity_iri=entity,
                    score=round(score, 6),
                    sim_time=event.sim_time,
                    decision=decision,
                )
            )

            if track not in self._anchored_tracks:
                self._anchored_tracks.add(track)
                anchor_conf = self.params.id_confidence if det.symbol_id else max(
                    0.5, round(0.9 * score, 6)
                )
                claims.append(
                    self._claim(track, iri.upper("anchoredTo"), Term(kind="iri", value=entity),
                                agent, anchor_conf, event.sim_time, ttl=None)
                )
            if entity not in self._typed_entities:
                self._typed_entities.add(entity)
                claims.append(
                    self._claim(entity, iri.RDF_TYPE, Term(kind="iri", value=iri.upper("Box")),
                                agent, det.confidence, event.sim_time, ttl=None)
                )
            if det.symbol_id and entity not in self._identified_entities:
                self._identified_entities.add(entity)
                claims.append(
                    self._claim(entity, iri.upper("hasIdentifier"),
                                Term(kind="literal", value=det.symbol_id),
                                agent, self.params.id_confidence, event.sim_time, ttl=None)
                )

            for pred, value in (
                ("posX", det.position[0]),
                ("posY", det.position[1]),
                ("posZ", det.position[2]),
            ):
                claims.append(
                    self._claim(entity, iri.st(pred),
                                Term(kind="literal", value=repr(float(value)),
                                     datatype=iri.XSD_DOUBLE),
                                agent, det.confidence, event.sim_time, ttl=self.claim_ttl_s)
                )
            zone = zone_of(self.zones, det.position)
            if zone is not None:
                claims.append(
                    self._claim(entity, iri.st("inZone"),
                                Term(kind="iri", value=iri.entity("zone", zone)),
                                agent, det.confidence, event.sim_time, ttl=self.claim_ttl_s)
                )

        return AnchorResult(claims=claims, records=records, assignments=assignments)

    # ---------------------------------------------------------------- private

    def _resolve(
        self, symbol_id: str | None, position: Vec3, used: set[str]
    ) -> tuple[str, float, str]:
        if not self.params.enabled:
            return self._new_entity(), 1.0, "new"
        if symbol_id is not None:
            known = self._symbol_to_entity.get(symbol_id)
            if known is not None:
                return known, 1.0, "match"
            entity = self._new_entity()
            self._symbol_to_entity[symbol_id] = entity
            return entity, 1.0, "new"
        best: tuple[float, str] | None = None
        for entity, pos in sorted(self._entity_pos.items()):
            if entity in used or entity in self._identified_entities:
                # 識別子付き個体への無ID空間マッチは保守的に避ける（P0）
                continue
            d = math.dist(position, pos)
            if d > self.params.gate_radius:
                continue
            score = math.exp(-(d * d) / (2 * self.params.spatial_sigma**2))
            if best is None or score > best[0]:
                best = (score, entity)
        if best is not None:
            return best[1], best[0], "match"
        return self._new_entity(), 1.0, "new"

    def _new_entity(self) -> str:
        return iri.entity("object", deterministic_id(self._rng))

    def _track_for(self, robot_id: str, entity: str) -> str:
        key = (robot_id, entity)
        track = self._tracks.get(key)
        if track is None:
            seq = self._track_seq.get(robot_id, 0)
            self._track_seq[robot_id] = seq + 1
            track = iri.entity("track", f"{robot_id}-{seq:04d}")
            self._tracks[key] = track
        return track

    def _claim(
        self,
        subject: str,
        predicate: str,
        obj: Term,
        agent: str,
        confidence: float,
        observed_at: float,
        ttl: float | None,
    ) -> Claim:
        return Claim(
            claim_id=deterministic_id(self._rng),
            subject=subject,
            predicate=predicate,
            object=obj,
            asserted_by=agent,
            confidence=min(1.0, max(0.0, confidence)),
            observed_at=observed_at,
            valid_until=None if ttl is None else observed_at + ttl,
        )
