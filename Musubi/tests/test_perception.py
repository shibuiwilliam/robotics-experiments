"""P4 perception tests — oracle dial (offline), pixel→world, ER path via a fake client."""

from __future__ import annotations

import numpy as np
import pytest

from perception import Dial, ERPoint, Perception, normalized_yx_to_pixel
from perception.oracle import OraclePerception
from sim.invisible_hand import InvisibleHand
from sim.world import World


def test_oracle_detects_all_visible_entities() -> None:
    w = World(seed=0)
    claims = OraclePerception(w).detect()
    identified = {str(c.subject) for c in claims if str(c.subject).startswith("msb:entity/")}
    truth = {s.iri for s in w.ground_truth().values() if s.visible}
    assert identified == truth  # dropout=0, tag_err=0 by default -> perfect recall + identity


def test_oracle_is_deterministic() -> None:
    def positions(seed: int) -> list[str]:
        w = World(seed=seed)
        return [str(c.objectValue) for c in OraclePerception(w).detect()]

    assert positions(0) == positions(0)


def test_oracle_degraded_tag_yields_unidentified_track() -> None:
    w = World(seed=0)
    InvisibleHand(w).degrade_tag("pallet_1")
    claims = OraclePerception(w).detect()
    subjects = {str(c.subject) for c in claims}
    # pallet_1 can no longer be identified -> not in identified entity IRIs
    assert "msb:entity/pallet_1" not in {s for s in subjects if s.startswith("msb:entity/")}
    # but something is still detected there (an anonymous tracked object)
    assert any(s.startswith("msb:tracked/") for s in subjects)


def test_oracle_unknown_spawn_is_untagged_track() -> None:
    w = World(seed=0)
    InvisibleHand(w).spawn_unknown(0.3, 0.3)
    claims = OraclePerception(w).detect()
    # the unknown pallet is untagged -> appears as a tracked object, never as an entity IRI
    assert "msb:entity/pallet_unknown_1" not in {str(c.subject) for c in claims}


def test_normalized_yx_mapping() -> None:
    assert normalized_yx_to_pixel(0, 0, 240, 320) == (0, 0)
    assert normalized_yx_to_pixel(1000, 1000, 240, 320) == (239, 319)
    assert normalized_yx_to_pixel(500, 500, 240, 320) == (120, 160)


def test_perception_facade_oracle_and_live_guard() -> None:
    w = World(seed=0)
    p = Perception(w, Dial.oracle)
    assert p.dial is Dial.oracle
    assert len(p.detect()) >= 5
    with pytest.raises(ValueError):
        Perception(w, Dial.live)  # live needs an er_client (behind clients/)


@pytest.mark.slow
def test_pixel_world_recovers_positions() -> None:
    from sim.render import SceneRenderer

    w = World(seed=0)
    w.step(1)
    try:
        r = SceneRenderer(w, height=240, width=320)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"offscreen GL unavailable: {exc}")
    from perception.pixel_world import unproject

    depth = r.depth("overhead")
    for body in ["pallet_1", "pallet_3", "pallet_5"]:
        centroid = r.entity_centroid_px(w.entity_iri(body), "overhead")
        assert centroid is not None
        row, col = centroid
        wx, wy, _ = unproject(w, "overhead", row, col, float(depth[row, col]), 240, 320)
        gt = w.body_pos(body)
        assert np.linalg.norm([wx - gt[0], wy - gt[1]]) < 0.06  # within 6 cm
    r.close()


@pytest.mark.slow
def test_er_perception_path_with_fake_client() -> None:
    """Full ER path offline: a fake client returns points at pallet centroids → world Claims."""
    from perception.er import ERPerception
    from sim.render import SceneRenderer

    w = World(seed=0)
    w.step(1)
    try:
        r = SceneRenderer(w, height=240, width=320)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"offscreen GL unavailable: {exc}")

    class FakeER:
        def detect_points(self, image: np.ndarray, query: str) -> list[ERPoint]:
            pts = []
            for body in w.pallet_bodies():
                c = r.entity_centroid_px(w.entity_iri(body), "overhead")
                if c is not None:
                    row, col = c
                    pts.append(ERPoint(y=row / 239 * 1000, x=col / 319 * 1000))
            return pts

    claims = ERPerception(w, FakeER(), r).detect("overhead")
    assert len(claims) == 5
    # each ER world Claim should land near some real pallet
    truth = [w.body_pos(b)[:2] for b in w.pallet_bodies() if w.is_visible(b)]
    for c in claims:
        x, y, _ = (float(v) for v in str(c.objectValue).split(","))
        assert min(np.linalg.norm([x - t[0], y - t[1]]) for t in truth) < 0.08
    r.close()
