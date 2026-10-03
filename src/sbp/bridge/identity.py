"""同一性束縛（設計書 §5.4、bridge-worldmodel ルール）。

物理トラック（知覚が作る時空間軌跡）と業務 ID を結ぶ。**Track はまず
``UnidentifiedPhysicalObject`` として登録する**のが要点である——業務が知らない物を表現できる
ようにするためで、これが存在不一致（類型 1）の片側になる。

束縛は割当問題として解く。コストは ``-log P(match | 外観類似, 時空間整合, 業務事前)``。
``scipy.optimize.linear_sum_assignment`` を使い、多重仮説（JPDA/MHT）が必要になったら ADR。

**禁止事項**（設計書 §5.4、不変条件 9 の精神）:

- Person の Track を個人識別に用いない（役割・人数・位置のみ）。
- 外観埋め込みを Person に紐づけて保存しない。

これは型レベルで強制する。``Track`` は ``kind`` を持ち、``person`` の Track に外観埋め込みを
載せようとすると構築に失敗する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Self
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import IdentityBindingError
from sbp.core.logging import get_logger
from sbp.core.time import Interval, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = [
    "BindingResult",
    "EvidenceKind",
    "IdentityClaim",
    "Track",
    "TrackKind",
    "bind_tracks",
]

log = get_logger(__name__)

_UNRESOLVED_PREFIX: Final = "urn:sbp:unidentified:"
"""未同定物理個体の IRI 接頭辞。業務 ID に束縛されるまでこの形で存在する。"""


class TrackKind(StrEnum):
    """トラックが追っている対象の種別。

    ``PERSON`` を区別するのは、個人識別の禁止を型レベルで効かせるためである。
    """

    ASSET = "asset"
    MATERIAL = "material"
    CARRIER = "carrier"
    PERSON = "person"


class EvidenceKind(StrEnum):
    """束縛の証拠の種類（設計書 §5.4 のアンカー種別に対応）。"""

    APPEARANCE = "appearance"
    """外観類似（固定カメラ由来）。"""
    SPATIOTEMPORAL = "spatiotemporal"
    """時空間連続性。"""
    ANCHOR = "anchor"
    """アンカー観測（RFID/BLE リーダ、ゲート）。"""
    BUSINESS_CONTEXT = "business_context"
    """業務文脈（配車計画など）。"""


class Track(BaseModel):
    """知覚が生成する時空間トラック（設計書 §3.3、§5.4）。

    Attributes:
        track_id: トラックの識別子。
        kind: 追っている対象の種別。
        first_seen: 最初に観測した時刻。
        last_seen: 最後に観測した時刻。
        position_mean: 位置の平均（施設座標系）。
        position_cov: 位置の共分散（対角成分のみ）。
        appearance_embedding: 外観埋め込み。**``person`` では持てない**（個人識別の禁止）。
        zone: 観測されたゾーンの IRI（分かっている場合）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    track_id: UUID = Field(default_factory=uuid4)
    kind: TrackKind
    first_seen: datetime
    last_seen: datetime
    position_mean: tuple[float, float] | None = None
    position_cov: tuple[float, float] | None = None
    appearance_embedding: tuple[float, ...] | None = None
    zone: str | None = None

    @model_validator(mode="after")
    def _person_tracks_carry_no_appearance(self) -> Self:
        """Person のトラックに外観埋め込みを持たせない（設計書 §5.4、§11.1）。

        外観埋め込みがあれば個人の再識別ができてしまう。Person について保持してよいのは
        役割・人数・位置だけであり、それを型レベルで守る。
        """
        if self.kind is TrackKind.PERSON and self.appearance_embedding is not None:
            raise ValueError(
                "a person track must not carry an appearance embedding: it would enable "
                "re-identification. Only role, count, and position may be retained "
                "(design §5.4, §11.1)."
            )
        return self

    @model_validator(mode="after")
    def _interval_is_valid(self) -> Self:
        """観測区間が右開の非空区間であることを検証する。"""
        Interval(self.first_seen, self.last_seen)
        return self

    @property
    def unidentified_iri(self) -> str:
        """業務 ID に束縛される前の IRI（``UnidentifiedPhysicalObject``）。"""
        return f"{_UNRESOLVED_PREFIX}{self.track_id}"

    @property
    def observed_interval(self) -> Interval:
        """観測区間。"""
        return Interval(self.first_seen, self.last_seen)


class IdentityClaim(BaseModel):
    """物理トラックと業務 ID の束縛主張（設計書 §3.3、付録 A.4）。

    **撤回可能**であることが本質である。確信度が閾値を下回ったら撤回し、不一致（類型 4）を
    起票する。撤回できない束縛は「間違った同一性で信念を積み上げる」ことになる。

    Attributes:
        claim_id: 主張の識別子。
        track_id: トラックの識別子。
        entity: 束縛先の業務実体 IRI。
        evidence: 証拠（種別・スコア・参照）。
        confidence: 較正済みの束縛確信度。
        valid_from: 束縛の有効開始。
        valid_to: 束縛の有効終了（継続中は ``None``）。
        status: ``active`` または ``revoked``。
        revocation_reason: 撤回理由（撤回時は必須）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID = Field(default_factory=uuid4)
    track_id: UUID
    entity: str
    evidence: tuple[tuple[EvidenceKind, float], ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: datetime
    valid_to: datetime | None = None
    status: str = "active"
    revocation_reason: str | None = None

    @model_validator(mode="after")
    def _revocation_needs_a_reason(self) -> Self:
        """撤回には理由が必要（来歴に残すため）。"""
        if self.status == "revoked" and not self.revocation_reason:
            raise ValueError(
                "a revoked identity claim must record why; the reason feeds the discrepancy "
                "(kind 4) and the learning signal"
            )
        return self

    @model_validator(mode="after")
    def _evidence_is_required_for_active_claims(self) -> Self:
        """根拠の無い束縛を active にしない。"""
        if self.status == "active" and not self.evidence:
            raise ValueError(
                "an active identity claim must carry at least one piece of evidence; "
                "binding without evidence cannot be audited (design §5.4)"
            )
        return self

    @property
    def is_active(self) -> bool:
        """有効な束縛かどうか。"""
        return self.status == "active"

    def revoke(self, *, at: datetime, reason: str) -> IdentityClaim:
        """束縛を撤回した新しい主張を返す。

        撤回は追記であり、元の主張を書き換えない（不変条件 2 と同じ考え方）。呼び出し側は
        **必ず不一致（類型 4）を起票する**こと。

        Args:
            at: 撤回時刻。
            reason: 撤回理由。

        Returns:
            撤回済みの主張。
        """
        if not reason:
            raise IdentityBindingError("revocation requires a reason")
        return self.model_copy(
            update={
                "status": "revoked",
                "revocation_reason": reason,
                "valid_to": ensure_utc(at),
            }
        )


@dataclass(frozen=True, slots=True)
class BindingResult:
    """束縛の結果。

    Attributes:
        claims: 成立した束縛主張。
        unbound_tracks: どの実体にも束縛されなかったトラック（``UnidentifiedPhysicalObject``
            として残る。業務が知らない物の可能性）。
        unmatched_entities: どのトラックにも束縛されなかった実体（物理に見えない物の可能性。
            存在不一致 類型 1 の材料）。
    """

    claims: tuple[IdentityClaim, ...]
    unbound_tracks: tuple[Track, ...]
    unmatched_entities: tuple[str, ...]


def _appearance_similarity(a: Sequence[float] | None, b: Sequence[float] | None) -> float | None:
    """外観埋め込みのコサイン類似度（どちらか無ければ ``None``）。"""
    if a is None or b is None:
        return None
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denominator == 0.0:
        return None
    return float(np.clip(np.dot(va, vb) / denominator, -1.0, 1.0))


def _spatiotemporal_score(track: Track, expected_zone: str | None) -> float | None:
    """時空間整合のスコア（ゾーンが一致すれば高い）。"""
    if expected_zone is None or track.zone is None:
        return None
    return 1.0 if track.zone == expected_zone else 0.0


def match_probability(
    track: Track,
    *,
    entity_embedding: Sequence[float] | None = None,
    expected_zone: str | None = None,
    business_prior: float = 0.5,
) -> tuple[float, tuple[tuple[EvidenceKind, float], ...]]:
    """トラックと実体が同一である確率と、その根拠を返す。

    証拠を独立とみなして対数オッズで合成する。使える証拠が無ければ業務事前だけになる——
    そのときの確率は 0.5 付近に留まり、束縛の閾値を越えない。「根拠が無いのに束縛する」
    ことを防ぐ設計である。

    Args:
        track: 対象トラック。
        entity_embedding: 実体側の外観埋め込み（マスタや過去観測から）。
        expected_zone: 業務情報から期待されるゾーン。
        business_prior: 業務文脈による事前確率。

    Returns:
        ``(確率, 証拠の列)``。
    """
    evidence: list[tuple[EvidenceKind, float]] = []
    log_odds = math.log(business_prior / (1.0 - business_prior)) if 0 < business_prior < 1 else 0.0
    if 0.0 < business_prior < 1.0:
        evidence.append((EvidenceKind.BUSINESS_CONTEXT, business_prior))

    similarity = _appearance_similarity(track.appearance_embedding, entity_embedding)
    if similarity is not None:
        evidence.append((EvidenceKind.APPEARANCE, similarity))
        # 類似度 1 で強く支持、0 で中立、-1 で強く否定
        log_odds += 3.0 * similarity

    spatial = _spatiotemporal_score(track, expected_zone)
    if spatial is not None:
        evidence.append((EvidenceKind.SPATIOTEMPORAL, spatial))
        log_odds += 2.0 * (2.0 * spatial - 1.0)

    probability = 1.0 / (1.0 + math.exp(-log_odds))
    return float(np.clip(probability, 1e-6, 1.0 - 1e-6)), tuple(evidence)


def bind_tracks(
    tracks: Sequence[Track],
    entities: Sequence[str],
    *,
    at: datetime,
    entity_embeddings: dict[str, Sequence[float]] | None = None,
    expected_zones: dict[str, str] | None = None,
    business_priors: dict[str, float] | None = None,
    min_confidence: float = 0.5,
) -> BindingResult:
    """トラック集合と実体集合を束縛する（割当問題、設計書 §5.4）。

    コストは ``-log P(match)``。``scipy.optimize.linear_sum_assignment`` で最適割当を求め、
    確信度が ``min_confidence`` 未満の割当は採用しない（根拠の弱い束縛を作らない）。

    Args:
        tracks: 束縛するトラック。
        entities: 候補実体の IRI。
        at: 束縛を行う時刻（主張の ``valid_from``）。
        entity_embeddings: 実体 IRI → 外観埋め込み。
        expected_zones: 実体 IRI → 期待ゾーン。
        business_priors: 実体 IRI → 業務事前確率。
        min_confidence: これ未満の確信度では束縛しない。

    Returns:
        束縛の結果（成立した主張、未束縛トラック、未マッチ実体）。

    Raises:
        IdentityBindingError: Person のトラックを束縛しようとした場合。
            Person の同一性束縛は個人識別に当たるため行わない（設計書 §5.4）。
    """
    from scipy.optimize import linear_sum_assignment  # noqa: PLC0415 — 重い依存は遅延 import

    for track in tracks:
        if track.kind is TrackKind.PERSON:
            raise IdentityBindingError(
                "person tracks are never bound to business identities: that is "
                "re-identification. Keep role, count, and position only (design §5.4)."
            )

    if not tracks or not entities:
        return BindingResult(
            claims=(), unbound_tracks=tuple(tracks), unmatched_entities=tuple(entities)
        )

    embeddings = entity_embeddings or {}
    zones = expected_zones or {}
    priors = business_priors or {}

    probabilities = np.zeros((len(tracks), len(entities)), dtype=np.float64)
    evidences: dict[tuple[int, int], tuple[tuple[EvidenceKind, float], ...]] = {}
    for i, track in enumerate(tracks):
        for j, entity in enumerate(entities):
            probability, evidence = match_probability(
                track,
                entity_embedding=embeddings.get(entity),
                expected_zone=zones.get(entity),
                business_prior=priors.get(entity, 0.5),
            )
            probabilities[i, j] = probability
            evidences[(i, j)] = evidence

    cost = -np.log(probabilities)
    rows, cols = linear_sum_assignment(cost)

    claims: list[IdentityClaim] = []
    bound_tracks: set[int] = set()
    bound_entities: set[int] = set()
    for row, col in zip(rows, cols, strict=True):
        # numpy の添字をそのまま使うと型が曖昧になるので int に落とす
        i, j = int(row), int(col)
        confidence = float(probabilities[i, j])
        if confidence < min_confidence:
            continue
        claims.append(
            IdentityClaim(
                track_id=tracks[i].track_id,
                entity=entities[j],
                evidence=evidences[(i, j)],
                confidence=confidence,
                valid_from=ensure_utc(at),
            )
        )
        bound_tracks.add(i)
        bound_entities.add(j)

    log.info(
        "bridge.tracks_bound",
        tracks=len(tracks),
        entities=len(entities),
        bound=len(claims),
        unbound_tracks=len(tracks) - len(bound_tracks),
        unmatched_entities=len(entities) - len(bound_entities),
    )
    return BindingResult(
        claims=tuple(claims),
        unbound_tracks=tuple(t for i, t in enumerate(tracks) if i not in bound_tracks),
        unmatched_entities=tuple(e for j, e in enumerate(entities) if j not in bound_entities),
    )


def should_revoke(claim: IdentityClaim, *, new_confidence: float, threshold: float) -> bool:
    """新しい証拠のもとで束縛を撤回すべきか。

    Args:
        claim: 現在の主張。
        new_confidence: 再計算した確信度。
        threshold: 撤回の閾値（``BridgeSettings.identity_revoke_threshold``）。
    """
    return claim.is_active and new_confidence < threshold
