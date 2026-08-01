"""P3 sim tests — determinism (bit-identical qpos), Invisible Hand, skills, segmentation scoring."""

from __future__ import annotations

import numpy as np
import pytest

from sim.invisible_hand import InvisibleHand
from sim.skills import MoveTo, Pick, Place, ScanTag, Skill, SkillMeta, SkillResult
from sim.world import World


def test_world_loads_and_ground_truth() -> None:
    w = World(seed=0)
    gt = w.ground_truth()
    # five registered pallets present + one hidden unknown pallet
    visible = [s for s in gt.values() if s.visible]
    assert len(visible) == 5
    assert all(s.zone == "receiving" for s in visible)  # all start in receiving
    unknown = [s for s in gt.values() if not s.visible]
    assert len(unknown) == 1 and not unknown[0].visible


def test_physics_is_deterministic_bit_identical() -> None:
    def rollout() -> np.ndarray:
        w = World(seed=0)
        w.set_base_target(0.6, -0.6)
        w.step(800)
        return w.qpos

    assert np.array_equal(rollout(), rollout())


def test_seeded_worlds_diverge_only_by_seed_not_run() -> None:
    a1 = World(seed=1)
    a1.set_base_target(0.5, 0.0)
    a1.step(300)
    a2 = World(seed=1)
    a2.set_base_target(0.5, 0.0)
    a2.step(300)
    assert np.array_equal(a1.qpos, a2.qpos)


def test_invisible_hand_perturbations_and_history() -> None:
    w = World(seed=0)
    hand = InvisibleHand(w)
    hand.move("pallet_1", 1.2, -1.2)  # to shipping
    hand.swap("pallet_2", "pallet_3")
    hand.remove("pallet_4")
    hand.degrade_tag("pallet_5")
    hand.spawn_unknown(0.3, 0.3)
    gt = w.ground_truth()
    assert gt["msb:entity/pallet_1"].zone == "shipping"
    assert not gt["msb:entity/pallet_4"].visible  # removed
    assert not w.tag_readable("pallet_5")
    assert gt["msb:entity/pallet_unknown_1"].visible  # spawned
    assert [p.op for p in hand.history] == [
        "move",
        "swap",
        "remove",
        "degrade_tag",
        "spawn_unknown",
    ]


def test_invisible_hand_churn_is_seeded() -> None:
    def churn_positions(seed: int) -> list[tuple[float, float, float]]:
        w = World(seed=seed)
        InvisibleHand(w).churn(count=3)
        return [s.position for s in w.ground_truth().values()]

    assert churn_positions(7) == churn_positions(7)  # same seed -> same churn
    assert churn_positions(7) != churn_positions(8)


def test_skills_move_pick_place_cycle() -> None:
    w = World(seed=0)
    # bring a pallet to the bot, pick it, carry to shipping, place it.
    bot = w.body_pos("lift_bot")
    InvisibleHand(w).move("pallet_1", float(bot[0]), float(bot[1]))
    assert Pick("pallet_1").execute(w).success
    assert w.is_carried("pallet_1")
    assert MoveTo(1.2, -1.2, max_steps=8000).execute(w).success
    assert Place("pallet_1").execute(w).success
    assert w.ground_truth()["msb:entity/pallet_1"].zone == "shipping"


def test_scan_tag_fails_after_degrade() -> None:
    w = World(seed=0)
    assert ScanTag("pallet_1").execute(w).success
    InvisibleHand(w).degrade_tag("pallet_1")
    assert not ScanTag("pallet_1").execute(w).success


def test_fault_injection_is_deterministic() -> None:
    class FlakyScan(Skill):
        meta = SkillMeta(
            action_type="perceive.flaky", reversibility="reversible", base_failure_prob=0.5
        )

        def _run(self, world: World) -> SkillResult:
            return SkillResult(True)

    def outcomes(seed: int) -> list[bool]:
        w = World(seed=seed)
        return [FlakyScan().execute(w).success for _ in range(10)]

    assert outcomes(3) == outcomes(3)  # seeded -> reproducible failures
    assert any(outcomes(3)) and not all(outcomes(3))  # some fail, some pass


# ------------------------------------------------------------------ rendering (GL required)
@pytest.mark.slow
def test_segmentation_scoring_recovers_visible_pallets() -> None:
    from sim.render import SceneRenderer

    w = World(seed=0)
    try:
        r = SceneRenderer(w, height=240, width=320)
    except Exception as exc:  # pragma: no cover - environment without GL
        pytest.skip(f"offscreen GL unavailable: {exc}")
    score = r.score_segmentation("overhead")
    assert score["recall"] == pytest.approx(1.0)  # all visible pallets segmented
    assert score["precision"] == pytest.approx(1.0)
    r.close()
