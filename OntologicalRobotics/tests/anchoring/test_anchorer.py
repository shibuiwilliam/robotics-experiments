"""C5 アンカリング骨格のテスト。"""

from orx.anchoring.anchorer import Anchorer
from orx.common import iri
from orx.common.config import AnchoringParams, ZoneConfig
from orx.common.schemas import Detection, PerceptionEvent
from orx.common.seeding import SeedTree

ZONES = [
    ZoneConfig(name="shelf_a", center=(0.0, 0.6, 0.45), size=(1.6, 0.5, 0.35)),
    ZoneConfig(name="dock", center=(0.0, -0.8, 0.15), size=(1.2, 0.6, 0.30)),
]


def make_anchorer(**param_overrides: object) -> Anchorer:
    params = AnchoringParams(**param_overrides) if param_overrides else AnchoringParams()
    return Anchorer(params, ZONES, claim_ttl_s=5.0, seeds=SeedTree(7))


def event(robot: str, t: float, dets: list[Detection]) -> PerceptionEvent:
    return PerceptionEvent(
        event_id=f"{robot}-{int(t * 10):06d}", robot_id=robot, sim_time=t, detections=dets
    )


def det(index: int, pos: tuple[float, float, float], symbol: str | None = None) -> Detection:
    return Detection(sensor_id="cam", index=index, position=pos, symbol_id=symbol, confidence=0.95)


def test_symbol_id_binds_deterministically() -> None:
    anchorer = make_anchorer()
    r1 = anchorer.process(event("arm_a", 1.0, [det(0, (0.0, 0.6, 0.5), "BC-001")]))
    r2 = anchorer.process(event("arm_a", 1.5, [det(0, (0.05, 0.62, 0.5), "BC-001")]))
    assert r1.assignments[0] == r2.assignments[0]
    assert r2.records[0].decision == "match"


def test_spatial_gate_matches_nearby_idless() -> None:
    anchorer = make_anchorer()
    r1 = anchorer.process(event("arm_a", 1.0, [det(0, (0.0, 0.6, 0.5))]))
    r2 = anchorer.process(event("arm_a", 1.5, [det(0, (0.02, 0.61, 0.5))]))
    assert r1.assignments[0] == r2.assignments[0]


def test_far_idless_creates_new_entity() -> None:
    anchorer = make_anchorer()
    r1 = anchorer.process(event("arm_a", 1.0, [det(0, (0.0, 0.6, 0.5))]))
    r2 = anchorer.process(event("arm_a", 1.5, [det(0, (0.9, -0.8, 0.2))]))
    assert r1.assignments[0] != r2.assignments[0]
    assert r2.records[0].decision == "new"


def test_claims_carry_required_metadata_and_zone() -> None:
    anchorer = make_anchorer()
    result = anchorer.process(event("arm_a", 1.0, [det(0, (0.0, 0.6, 0.5), "BC-001")]))
    predicates = {c.predicate for c in result.claims}
    assert iri.upper("anchoredTo") in predicates
    assert iri.RDF_TYPE in predicates
    assert iri.upper("hasIdentifier") in predicates
    assert iri.st("inZone") in predicates
    zone_claim = next(c for c in result.claims if c.predicate == iri.st("inZone"))
    assert zone_claim.object.value == iri.entity("zone", "shelf_a")
    assert zone_claim.valid_until is not None  # 観測由来はTTL付き
    anchored = next(c for c in result.claims if c.predicate == iri.upper("anchoredTo"))
    assert anchored.valid_until is None
    assert anchored.subject.startswith("https://orx.local/id/track/")


def test_no_sameas_emitted() -> None:
    anchorer = make_anchorer()
    result = anchorer.process(event("arm_a", 1.0, [det(0, (0.0, 0.6, 0.5), "BC-001")]))
    assert all("sameAs" not in c.predicate for c in result.claims)


def test_deterministic_across_instances() -> None:
    events = [
        event("arm_a", 1.0, [det(0, (0.0, 0.6, 0.5), "BC-001"), det(1, (0.3, 0.55, 0.5))]),
        event("arm_a", 1.5, [det(0, (0.0, 0.6, 0.5), "BC-001"), det(1, (0.31, 0.56, 0.5))]),
    ]
    a1, a2 = make_anchorer(), make_anchorer()
    out1 = [a1.process(e) for e in events]
    out2 = [a2.process(e) for e in events]
    assert out1 == out2  # 同一シード＋同一入力 → 同一クレームID列（リプレイ同一性の基礎）


def test_identity_disabled_ablation() -> None:
    anchorer = make_anchorer(enabled=False)
    r1 = anchorer.process(event("arm_a", 1.0, [det(0, (0.0, 0.6, 0.5), "BC-001")]))
    r2 = anchorer.process(event("arm_a", 1.5, [det(0, (0.0, 0.6, 0.5), "BC-001")]))
    assert r1.assignments[0] != r2.assignments[0]  # 検出毎に新個体
