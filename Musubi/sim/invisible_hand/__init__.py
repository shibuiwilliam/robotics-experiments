"""The Invisible Hand — manufactures reproducible ledger/reality divergence (PROJECT.md §8).

Operations (move / swap / remove / degrade_tag / spawn_unknown / churn) perturb the sim (reality)
so it diverges from the external ledger (WMS). Every perturbation is seeded (sub-stream
``invisible_hand``) and returns a record — the *planted ground truth* an oracle scores against.
"""

from __future__ import annotations

from sim.invisible_hand.hand import InvisibleHand, Perturbation

__all__ = ["InvisibleHand", "Perturbation"]
