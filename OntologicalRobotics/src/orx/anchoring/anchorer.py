"""C5 アンカリング — 個体化と同一性解決（P1: D1スコア融合版）。

解決規則（D1: ゲート付きスコア融合、重み・閾値はコンフィグ）:
  1. 記号ID（バーコード）一致 → 決定的に同一個体（登録簿）。
     既知個体が**別の**識別子を持つ場合は分裂（新規個体を発行）。
  2. ID無し検出 → 動きを考慮した時空間ゲート（gate = gate_radius + v_max·Δt）を
     通過した候補に対し、空間スコアと埋め込みコサイン類似の重み付き和。
     最良スコアが theta_merge 以上ならマッチ、未満なら新規個体。
  3. 同一イベント内で同じ個体に2検出はマッチさせない（排他）。

同一性は `orx-upper:anchoredTo`（確信度付き）の改訂可能な主張として出力する。
このモジュールは event の真値フィールドを読まない（採点は oracle/exp の仕事）。
"""

from __future__ import annotations

import math

from orx.common import iri
from orx.common.config import AnchoringParams, ZoneConfig
from orx.common.geometry import zone_of
from orx.common.schemas import (
    AnchorRecord,
    Claim,
    Detection,
    PerceptionEvent,
    StrictModel,
    Term,
    Vec3,
)
from orx.common.seeding import SeedTree, deterministic_id


class AnchorResult(StrictModel):
    claims: list[Claim]
    records: list[AnchorRecord]
    assignments: list[str]  # 検出添字 → 個体IRI


class _EntityState:
    """個体の最終観測状態（マッチング材料）。等速モデルの速度推定を持つ。"""

    def __init__(
        self,
        position: Vec3,
        last_seen: float,
        embedding: list[float] | None,
        symbol: str | None,
        velocity: Vec3 = (0.0, 0.0, 0.0),
    ) -> None:
        self.position = position
        self.last_seen = last_seen
        self.embedding = embedding
        self.symbol = symbol
        self.velocity = velocity

    def predicted_position(self, now: float) -> Vec3:
        dt = max(0.0, now - self.last_seen)
        return (
            self.position[0] + self.velocity[0] * dt,
            self.position[1] + self.velocity[1] * dt,
            self.position[2] + self.velocity[2] * dt,
        )


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return max(-1.0, min(1.0, dot))  # 埋め込みは単位ノルム前提


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
        self._entities: dict[str, _EntityState] = {}
        self._typed_entities: set[str] = set()
        self._identified_entities: set[str] = set()
        self._tracks: dict[tuple[str, str], str] = {}
        self._track_seq: dict[str, int] = {}
        self._anchored_tracks: set[str] = set()

    # ----------------------------------------------------------------- public

    def process(self, event: PerceptionEvent) -> AnchorResult:
        claims: list[Claim] = []
        records: list[AnchorRecord] = []
        agent = iri.entity("agent", f"anchoring-{event.robot_id}")
        resolution = self._assign_event(event)

        for det in event.detections:
            entity, score, decision = resolution[det.index]
            state = self._entities.get(entity)
            new_embedding = det.embedding if det.embedding is not None else (
                state.embedding if state else None
            )
            symbol = det.symbol_id or (state.symbol if state else None)
            velocity = self._update_velocity(state, det.position, event.sim_time)
            self._entities[entity] = _EntityState(
                det.position, event.sim_time, new_embedding, symbol, velocity
            )

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
            claims.extend(
                self._emit_claims(det, entity, track, agent, event.sim_time, score)
            )
        assignments = [resolution[det.index][0] for det in event.detections]
        return AnchorResult(claims=claims, records=records, assignments=assignments)

    # ------------------------------------------------------------- resolution

    def _assign_event(
        self, event: PerceptionEvent
    ) -> dict[int, tuple[str, float, str]]:
        """イベント内の全検出を一括解決する。

        記号ID検出を先に（登録簿は決定的）、残りのID無し検出は全 (検出, 候補)
        ペアのスコア降順で大域貪欲割当（添字順の偏りを排除）。
        """
        now = event.sim_time
        resolution: dict[int, tuple[str, float, str]] = {}
        used: set[str] = set()

        if not self.params.enabled:
            for det in event.detections:
                resolution[det.index] = (self._new_entity(), 1.0, "new")
            return resolution

        idless: list[Detection] = []
        for det in event.detections:
            if det.symbol_id is None:
                idless.append(det)
                continue
            known = self._symbol_to_entity.get(det.symbol_id)
            if known is not None and known not in used:
                resolution[det.index] = (known, 1.0, "match")
                used.add(known)
                continue
            if known is None:
                # 空間候補が未識別ならそれを識別子で確定、識別子衝突なら分裂
                candidate = self._best_spatial(det.position, det.embedding, now, used)
                if candidate is not None and candidate[1] not in self._identified_entities:
                    score, entity = candidate
                    self._symbol_to_entity[det.symbol_id] = entity
                    resolution[det.index] = (entity, score, "match")
                    used.add(entity)
                    continue
                entity = self._new_entity()
                self._symbol_to_entity[det.symbol_id] = entity
            else:
                # 登録簿の個体が同イベントで使用済み → 重複検出。新規扱い。
                entity = self._new_entity()
            resolution[det.index] = (entity, 1.0, "new")
            used.add(entity)

        pairs: list[tuple[float, int, str]] = []
        for det in idless:
            for score, entity in self._candidates(det.position, det.embedding, now, used):
                pairs.append((score, det.index, entity))
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
        assigned: set[int] = set()
        for score, det_index, entity in pairs:
            if det_index in assigned or entity in used:
                continue
            resolution[det_index] = (entity, score, "match")
            assigned.add(det_index)
            used.add(entity)
        for det in idless:
            if det.index not in assigned:
                entity = self._new_entity()
                resolution[det.index] = (entity, 1.0, "new")
                used.add(entity)
        return resolution

    def _candidates(
        self,
        position: Vec3,
        embedding: list[float] | None,
        now: float,
        used: set[str],
    ) -> list[tuple[float, str]]:
        """θ以上の全候補 (score, entity)。"""
        out: list[tuple[float, str]] = []
        for entity in sorted(self._entities):
            if entity in used:
                continue
            state = self._entities[entity]
            score = self._spatial_score(position, state, now)
            if embedding is not None and state.embedding is not None:
                cos = max(0.0, _cosine(embedding, state.embedding))
                score *= (1.0 - self.params.w_embedding) + self.params.w_embedding * cos
            if score >= self.params.theta_merge:
                out.append((score, entity))
        return out

    def _update_velocity(
        self, state: _EntityState | None, position: Vec3, now: float
    ) -> Vec3:
        """等速モデルの速度推定（EMA、v_max でクランプ）。"""
        if state is None:
            return (0.0, 0.0, 0.0)
        dt = now - state.last_seen
        if dt <= 1e-6:
            return state.velocity
        ema = self.params.velocity_ema
        raw = tuple((p - q) / dt for p, q in zip(position, state.position, strict=True))
        blended = tuple(
            (1.0 - ema) * v + ema * r for v, r in zip(state.velocity, raw, strict=True)
        )
        speed = math.sqrt(sum(v * v for v in blended))
        if speed > self.params.v_max:
            blended = tuple(v * self.params.v_max / speed for v in blended)
        return (blended[0], blended[1], blended[2])

    def _spatial_score(self, position: Vec3, state: _EntityState, now: float) -> float:
        """静止仮説と搬送仮説（等速予測）の最大スコア（D1）。"""
        p = self.params
        dt = max(0.0, now - state.last_seen)
        # 静止仮説: 最終観測位置周りの時間成長ガウス。密度正規化で大σにペナルティ
        d_static = math.dist(position, state.position)
        sigma_eff = p.spatial_sigma * (1.0 + dt / p.sigma_growth_tau)
        s_stationary = math.exp(-(d_static**2) / (2 * sigma_eff**2)) * (
            p.spatial_sigma / sigma_eff
        )
        # 搬送仮説: 等速予測位置周りのガウス（予測誤差は時間と共に成長）
        d_transit = math.dist(position, state.predicted_position(now))
        sigma_tr = p.transit_sigma_base + p.transit_sigma_rate * dt
        s_transit = p.transit_score * math.exp(-(d_transit**2) / (2 * sigma_tr**2))
        return max(s_stationary, s_transit)

    def _best_spatial(
        self,
        position: Vec3,
        embedding: list[float] | None,
        now: float,
        used: set[str],
    ) -> tuple[float, str] | None:
        candidates = self._candidates(position, embedding, now, used)
        if not candidates:
            return None
        return max(candidates, key=lambda c: (c[0], c[1]))

    # ----------------------------------------------------------------- claims

    def _emit_claims(
        self,
        det: Detection,
        entity: str,
        track: str,
        agent: str,
        now: float,
        score: float,
    ) -> list[Claim]:
        claims: list[Claim] = []
        if track not in self._anchored_tracks:
            self._anchored_tracks.add(track)
            conf = self.params.id_confidence if det.symbol_id else max(
                0.5, round(0.9 * score, 6)
            )
            claims.append(
                self._claim(track, iri.upper("anchoredTo"), Term(kind="iri", value=entity),
                            agent, conf, now, ttl=None)
            )
        if entity not in self._typed_entities:
            self._typed_entities.add(entity)
            claims.append(
                self._claim(entity, iri.RDF_TYPE, Term(kind="iri", value=iri.upper("Box")),
                            agent, det.confidence, now, ttl=None)
            )
        if det.symbol_id and entity not in self._identified_entities:
            self._identified_entities.add(entity)
            claims.append(
                self._claim(entity, iri.upper("hasIdentifier"),
                            Term(kind="literal", value=det.symbol_id),
                            agent, self.params.id_confidence, now, ttl=None)
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
                            agent, det.confidence, now, ttl=self.claim_ttl_s)
            )
        zone = zone_of(self.zones, det.position)
        if zone is not None:
            claims.append(
                self._claim(entity, iri.st("inZone"),
                            Term(kind="iri", value=iri.entity("zone", zone)),
                            agent, det.confidence, now, ttl=self.claim_ttl_s)
            )
        return claims

    # ---------------------------------------------------------------- helpers

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
