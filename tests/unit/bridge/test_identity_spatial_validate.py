"""同一性束縛・空間束縛・検証の単体テスト（設計書 §5.4、§5.5、§5.7）。

重点:

1. **個人識別の禁止が型レベルで効く** — Person のトラックに外観埋め込みを載せられない、
   Person を業務 ID に束縛できない（設計書 §5.4、§11.1）。
2. **根拠の無い束縛を作らない** — 証拠が無い active な主張は構築できない。
3. **検証は拒否ではなく証拠生成** — Warning は登録を止めず不一致候補になる（設計書 §3.9）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sbp.bridge.identity import (
    EvidenceKind,
    IdentityClaim,
    Track,
    TrackKind,
    bind_tracks,
    match_probability,
    should_revoke,
)
from sbp.bridge.spatial import (
    PositionBelief,
    Zone,
    containment_hierarchy,
    containment_probability,
    most_likely_zone,
)
from sbp.bridge.validate import Disposition, ValidationFinding, validate_graph
from sbp.core.config import BridgeSettings
from sbp.core.errors import BridgeError, IdentityBindingError, ShapeViolation

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
PALLET_A = "https://sbp.example/site/siteA/carrier/PAL-001"
PALLET_B = "https://sbp.example/site/siteA/carrier/PAL-002"


def _track(**overrides: object) -> Track:
    defaults: dict[str, object] = {
        "kind": TrackKind.CARRIER,
        "first_seen": T0,
        "last_seen": T0 + timedelta(minutes=5),
        "appearance_embedding": (1.0, 0.0, 0.0),
        "zone": "https://sbp.example/site/siteA/zone/dock-3",
    }
    return Track(**(defaults | overrides))  # type: ignore[arg-type]


class TestPersonPrivacy:
    """個人識別の禁止（設計書 §5.4、§11.1、不変条件 9 の精神）。"""

    def test_person_track_cannot_carry_an_appearance_embedding(self) -> None:
        """外観埋め込みがあれば再識別できてしまうので、型で禁じる。"""
        with pytest.raises(ValueError, match="re-identification"):
            Track(
                kind=TrackKind.PERSON,
                first_seen=T0,
                last_seen=T0 + timedelta(minutes=1),
                appearance_embedding=(0.1, 0.2),
            )

    def test_person_track_without_embedding_is_fine(self) -> None:
        """位置と時刻だけなら保持してよい（役割・人数・位置のみ）。"""
        track = Track(
            kind=TrackKind.PERSON,
            first_seen=T0,
            last_seen=T0 + timedelta(minutes=1),
            position_mean=(10.0, 5.0),
            position_cov=(1.0, 1.0),
        )
        assert track.appearance_embedding is None

    def test_person_tracks_are_never_bound_to_business_identities(self) -> None:
        """Person の同一性束縛は個人識別に当たるため行わない。"""
        person = Track(kind=TrackKind.PERSON, first_seen=T0, last_seen=T0 + timedelta(minutes=1))
        with pytest.raises(IdentityBindingError, match="re-identification"):
            bind_tracks([person], ["https://sbp.example/site/siteA/person/op-1"], at=T0)


class TestIdentityClaim:
    def test_active_claim_requires_evidence(self) -> None:
        """根拠の無い束縛は監査できない。"""
        with pytest.raises(ValueError, match="at least one piece of evidence"):
            IdentityClaim(
                track_id=_track().track_id, entity=PALLET_A, confidence=0.9, valid_from=T0
            )

    def test_revocation_requires_a_reason(self) -> None:
        """撤回理由は不一致（類型 4）と学習信号に流れるため必須。"""
        with pytest.raises(ValueError, match="must record why"):
            IdentityClaim(
                track_id=_track().track_id,
                entity=PALLET_A,
                evidence=((EvidenceKind.ANCHOR, 0.9),),
                confidence=0.3,
                valid_from=T0,
                status="revoked",
            )

    def test_revoke_returns_a_new_claim_and_keeps_the_original(self) -> None:
        """撤回は追記であり元の主張を書き換えない（不変条件 2 と同じ考え方）。"""
        claim = IdentityClaim(
            track_id=_track().track_id,
            entity=PALLET_A,
            evidence=((EvidenceKind.ANCHOR, 0.9),),
            confidence=0.9,
            valid_from=T0,
        )
        revoked = claim.revoke(at=T0 + timedelta(hours=1), reason="アンカー観測が矛盾した")
        assert claim.is_active, "元の主張が書き換わっている"
        assert not revoked.is_active
        assert revoked.valid_to == T0 + timedelta(hours=1)
        assert revoked.revocation_reason

    def test_should_revoke_uses_the_configured_threshold(self) -> None:
        claim = IdentityClaim(
            track_id=_track().track_id,
            entity=PALLET_A,
            evidence=((EvidenceKind.ANCHOR, 0.9),),
            confidence=0.9,
            valid_from=T0,
        )
        threshold = BridgeSettings().identity_revoke_threshold
        assert should_revoke(claim, new_confidence=threshold - 0.01, threshold=threshold)
        assert not should_revoke(claim, new_confidence=threshold + 0.01, threshold=threshold)

    def test_unidentified_iri_is_used_before_binding(self) -> None:
        """Track はまず UnidentifiedPhysicalObject として存在する（設計書 §5.4）。"""
        assert _track().unidentified_iri.startswith("urn:sbp:unidentified:")


class TestMatchProbability:
    def test_matching_appearance_raises_the_probability(self) -> None:
        probability, evidence = match_probability(
            _track(), entity_embedding=(1.0, 0.0, 0.0), business_prior=0.5
        )
        assert probability > 0.9
        assert any(k is EvidenceKind.APPEARANCE for k, _ in evidence)

    def test_opposite_appearance_lowers_the_probability(self) -> None:
        probability, _ = match_probability(
            _track(), entity_embedding=(-1.0, 0.0, 0.0), business_prior=0.5
        )
        assert probability < 0.1

    def test_zone_agreement_raises_the_probability(self) -> None:
        with_zone, _ = match_probability(
            _track(appearance_embedding=None),
            expected_zone="https://sbp.example/site/siteA/zone/dock-3",
        )
        wrong_zone, _ = match_probability(
            _track(appearance_embedding=None),
            expected_zone="https://sbp.example/site/siteA/zone/dock-9",
        )
        assert with_zone > wrong_zone

    def test_no_evidence_leaves_the_probability_near_the_prior(self) -> None:
        """証拠が無ければ束縛の閾値を越えない（根拠なしで束縛しない）。"""
        probability, evidence = match_probability(
            _track(appearance_embedding=None, zone=None), business_prior=0.5
        )
        assert probability == pytest.approx(0.5, abs=0.01)
        assert [k for k, _ in evidence] == [EvidenceKind.BUSINESS_CONTEXT]


class TestBindTracks:
    def test_binds_the_best_match(self) -> None:
        tracks = [
            _track(appearance_embedding=(1.0, 0.0, 0.0)),
            _track(appearance_embedding=(0.0, 1.0, 0.0)),
        ]
        result = bind_tracks(
            tracks,
            [PALLET_A, PALLET_B],
            at=T0,
            entity_embeddings={PALLET_A: (1.0, 0.0, 0.0), PALLET_B: (0.0, 1.0, 0.0)},
        )
        assert len(result.claims) == 2
        mapping = {c.track_id: c.entity for c in result.claims}
        assert mapping[tracks[0].track_id] == PALLET_A
        assert mapping[tracks[1].track_id] == PALLET_B

    def test_weak_matches_are_left_unbound(self) -> None:
        """確信度が閾値未満なら束縛しない（未同定のまま残す）。"""
        tracks = [_track(appearance_embedding=(-1.0, 0.0, 0.0))]
        result = bind_tracks(
            tracks,
            [PALLET_A],
            at=T0,
            entity_embeddings={PALLET_A: (1.0, 0.0, 0.0)},
            min_confidence=0.5,
        )
        assert result.claims == ()
        assert len(result.unbound_tracks) == 1
        assert result.unmatched_entities == (PALLET_A,)

    def test_unmatched_entity_is_reported(self) -> None:
        """物理に見えない実体は存在不一致（類型 1）の材料になる。"""
        result = bind_tracks([], [PALLET_A], at=T0)
        assert result.unmatched_entities == (PALLET_A,)

    def test_unbound_track_is_reported(self) -> None:
        """業務が知らない物（未同定）も残る。"""
        result = bind_tracks([_track()], [], at=T0)
        assert len(result.unbound_tracks) == 1

    def test_more_tracks_than_entities(self) -> None:
        """トラックが多いときも割当は 1 対 1 に収まる。"""
        tracks = [_track(appearance_embedding=(1.0, 0.0, 0.0)) for _ in range(3)]
        result = bind_tracks(
            tracks, [PALLET_A], at=T0, entity_embeddings={PALLET_A: (1.0, 0.0, 0.0)}
        )
        assert len(result.claims) == 1
        assert len(result.unbound_tracks) == 2


class TestSpatialBinding:
    def _square(self, iri: str, x0: float, y0: float, size: float, **kw: object) -> Zone:
        return Zone(
            iri=iri,
            polygon=((x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)),
            **kw,  # type: ignore[arg-type]
        )

    def test_position_at_the_centre_is_almost_certainly_inside(self) -> None:
        zone = self._square("https://sbp.example/site/siteA/zone/a", 0.0, 0.0, 10.0)
        position = PositionBelief(mean=(5.0, 5.0), cov_diag=(0.25, 0.25))
        assert containment_probability(position, zone, seed=0) > 0.99

    def test_position_far_outside_is_almost_certainly_outside(self) -> None:
        zone = self._square("https://sbp.example/site/siteA/zone/a", 0.0, 0.0, 10.0)
        position = PositionBelief(mean=(50.0, 50.0), cov_diag=(0.25, 0.25))
        assert containment_probability(position, zone, seed=0) < 0.01

    def test_position_on_the_boundary_is_uncertain(self) -> None:
        """境界付近は 0.5 付近になる。断定しないのが要点（設計書 §5.5）。"""
        zone = self._square("https://sbp.example/site/siteA/zone/a", 0.0, 0.0, 10.0)
        position = PositionBelief(mean=(10.0, 5.0), cov_diag=(1.0, 1.0))
        probability = containment_probability(position, zone, seed=0)
        assert 0.3 < probability < 0.7, f"境界で断定している: {probability}"

    def test_containment_is_deterministic_for_a_fixed_seed(self) -> None:
        """乱数を使う関数は seed で決定的（CLAUDE.md 数値規約）。"""
        zone = self._square("https://sbp.example/site/siteA/zone/a", 0.0, 0.0, 10.0)
        position = PositionBelief(mean=(9.5, 5.0), cov_diag=(1.0, 1.0))
        first = containment_probability(position, zone, seed=42)
        second = containment_probability(position, zone, seed=42)
        assert first == second

    def test_hierarchy_propagates_containment_upwards(self) -> None:
        """棚に含まれるなら区画にも含まれる（包含推移。設計書 §5.5）。"""
        building = self._square("urn:zone:building", 0.0, 0.0, 100.0)
        area = self._square("urn:zone:area", 0.0, 0.0, 20.0, contained_in="urn:zone:building")
        shelf = self._square("urn:zone:shelf", 1.0, 1.0, 2.0, contained_in="urn:zone:area")

        position = PositionBelief(mean=(2.0, 2.0), cov_diag=(0.04, 0.04))
        containment = containment_hierarchy(position, [building, area, shelf], seed=0)
        assert containment["urn:zone:shelf"] > 0.9
        assert containment["urn:zone:area"] >= containment["urn:zone:shelf"]
        assert containment["urn:zone:building"] >= containment["urn:zone:area"]

    def test_most_likely_zone_returns_none_when_uncertain(self) -> None:
        """どこにあるか言えないときは ``None``（適当に最大値を返さない）。"""
        assert most_likely_zone({"urn:zone:a": 0.3, "urn:zone:b": 0.2}) is None
        assert most_likely_zone({"urn:zone:a": 0.8, "urn:zone:b": 0.1}) == "urn:zone:a"

    def test_degenerate_zone_is_rejected(self) -> None:
        with pytest.raises(BridgeError, match="at least 3 vertices"):
            Zone(iri="urn:zone:bad", polygon=((0.0, 0.0), (1.0, 1.0)))

    def test_zero_variance_position_is_rejected(self) -> None:
        """分散 0 の位置は包含を断定にしてしまう。"""
        with pytest.raises(BridgeError, match="variance must be positive"):
            PositionBelief(mean=(0.0, 0.0), cov_diag=(0.0, 1.0))


@pytest.fixture(scope="module")
def shapes() -> str:
    """``ontology/shapes/core-shapes.ttl`` の本文。"""
    return (REPO_ROOT / "ontology" / "shapes" / "core-shapes.ttl").read_text(encoding="utf-8")


class TestValidation:
    """検証は拒否ではなく証拠生成（設計書 §3.9、§5.7）。"""

    def test_structural_violation_is_quarantined(self, shapes: str) -> None:
        """資産に場所が無いのは構造制約違反 → 隔離（登録しない）。"""
        data = """
        @prefix sbp: <https://sbp.example/core#> .
        @prefix ent: <https://sbp.example/ent#> .
        <urn:test:asset/P-999> a sbp:Asset .
        """
        outcome = validate_graph(data, shapes)
        assert outcome.disposition is Disposition.QUARANTINE
        assert outcome.violations, "構造制約違反が検出されていない"
        with pytest.raises(ShapeViolation, match="quarantine"):
            outcome.raise_if_quarantined()

    def test_conforming_data_is_accepted(self, shapes: str) -> None:
        data = """
        @prefix sbp: <https://sbp.example/core#> .
        <urn:test:asset/P-101> a sbp:Asset ;
            sbp:locatedIn <urn:test:zone/line-3> ;
            sbp:assetId "P-101" .
        <urn:test:zone/line-3> a sbp:Location .
        """
        outcome = validate_graph(data, shapes)
        assert outcome.disposition is Disposition.ACCEPT
        outcome.raise_if_quarantined()  # 例外にならない

    def test_asymmetric_exclusivity_is_a_warning_not_a_violation(self, shapes: str) -> None:
        """排他宣言の片側だけは Warning（登録は止めない）。"""
        data = """
        @prefix sbp: <https://sbp.example/core#> .
        @prefix ent: <https://sbp.example/ent#> .
        ent:FluentA a sbp:FluentType ;
            sbp:subjectType sbp:Asset ;
            sbp:mutuallyExclusiveWith ent:FluentB .
        ent:FluentB a sbp:FluentType ;
            sbp:subjectType sbp:Asset .
        """
        outcome = validate_graph(data, shapes)
        assert outcome.warnings, "非対称な排他宣言が Warning として出ていない"
        assert outcome.disposition is Disposition.ACCEPT_WITH_DISCREPANCY
        # Warning では隔離しない（登録して不一致候補にする）
        outcome.raise_if_quarantined()

    def test_finding_disposition_maps_severity_correctly(self) -> None:

        assert (
            ValidationFinding(
                severity="violation", focus_node="n", source_shape="s", message="m"
            ).disposition
            is Disposition.QUARANTINE
        )
        assert (
            ValidationFinding(
                severity="warning", focus_node="n", source_shape="s", message="m"
            ).disposition
            is Disposition.ACCEPT_WITH_DISCREPANCY
        )
        assert (
            ValidationFinding(
                severity="info", focus_node="n", source_shape="s", message="m"
            ).disposition
            is Disposition.ACCEPT
        )

    def test_probe_status_must_be_one_of_three(self, shapes: str) -> None:
        """プローブの身分は candidate / active / deprecated のみ。"""
        data = """
        @prefix sbp: <https://sbp.example/core#> .
        <urn:test:probe/p1> a sbp:Probe ; sbp:probeStatus "promoted" .
        """
        outcome = validate_graph(data, shapes)
        assert outcome.disposition is Disposition.QUARANTINE

    def test_discrepancy_kind_must_be_one_to_five(self, shapes: str) -> None:
        """不一致は五類型のいずれか。"""
        data = """
        @prefix sbp: <https://sbp.example/core#> .
        <urn:test:disc/d1> a sbp:Discrepancy ;
            sbp:discrepancyKind 9 ;
            sbp:concerns <urn:test:asset/P-101> .
        """
        outcome = validate_graph(data, shapes)
        assert outcome.disposition is Disposition.QUARANTINE
