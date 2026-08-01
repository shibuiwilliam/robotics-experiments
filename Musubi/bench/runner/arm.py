"""Ablation arms A0–A4 (PROJECT.md §8: Ablation Ladder).

Each arm progressively enables ontology machinery so E7 can measure the staircase effect (G3):
  A0 bare-coupling · A1 +semantic envelopes · A2 +Claims/mediation · A3 +norms/safety · A4 full.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Arm:
    name: str
    use_envelopes: bool  # A1+: bus speaks JSON-LD envelopes
    use_claims: bool  # A2+: perception→Claims + belief mediation
    use_norms: bool  # A3+: verification gate (norms, reversibility)
    use_explain: bool  # A4:  accountability chains / trace completeness

    @staticmethod
    def from_name(name: str) -> Arm:
        ladder = {
            "A0": (False, False, False, False),
            "A1": (True, False, False, False),
            "A2": (True, True, False, False),
            "A3": (True, True, True, False),
            "A4": (True, True, True, True),
        }
        if name not in ladder:
            raise ValueError(f"unknown arm {name!r}; expected one of {sorted(ladder)}")
        env, claims, norms, explain = ladder[name]
        return Arm(name, env, claims, norms, explain)
