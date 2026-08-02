"""Invisible Hand implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sim.world import World

_UNKNOWN = "pallet_unknown_1"


@dataclass(frozen=True)
class Perturbation:
    """A record of a planted divergence — the ground truth an oracle checks against."""

    op: str
    targets: tuple[str, ...]
    detail: dict[str, Any] = field(default_factory=dict)


class InvisibleHand:
    """Applies seeded perturbations to the sim world. All randomness is from the seeded RNG."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._rng = world.rng.generator("invisible_hand")
        self._history: list[Perturbation] = []

    @property
    def history(self) -> list[Perturbation]:
        return list(self._history)

    def _record(self, p: Perturbation) -> Perturbation:
        self._history.append(p)
        return p

    # -- operations ----------------------------------------------------------
    def move(self, pallet: str, x: float, y: float) -> Perturbation:
        """Teleport a pallet — the ledger still records its old location."""
        old = tuple(round(float(v), 4) for v in self._world.body_pos(pallet))
        self._world.set_body_xy(pallet, x, y, z=0.06)
        return self._record(
            Perturbation("move", (pallet,), {"from": old, "to": (round(x, 4), round(y, 4))})
        )

    def swap(self, p1: str, p2: str) -> Perturbation:
        """Exchange two pallets' positions — a classic identity-confusion trap (S5)."""
        a = self._world.body_pos(p1).copy()
        b = self._world.body_pos(p2).copy()
        self._world.set_body_xy(p1, float(b[0]), float(b[1]), z=0.06)
        self._world.set_body_xy(p2, float(a[0]), float(a[1]), z=0.06)
        return self._record(Perturbation("swap", (p1, p2)))

    def remove(self, pallet: str) -> Perturbation:
        """Make a pallet vanish (teleport below floor) — ledger still says it's present."""
        self._world.set_body_xy(pallet, 0.0, 0.0, z=-1.0)
        return self._record(Perturbation("remove", (pallet,)))

    def degrade_tag(self, pallet: str) -> Perturbation:
        """Make a pallet's tag unreadable — identity must fall back to other anchors."""
        self._world.set_tag_readable(pallet, False)
        return self._record(Perturbation("degrade_tag", (pallet,)))

    def spawn_unknown(self, x: float, y: float) -> Perturbation:
        """Reveal an untagged, unregistered pallet (not in any ledger)."""
        self._world.set_body_xy(_UNKNOWN, x, y, z=0.06)
        return self._record(
            Perturbation("spawn_unknown", (_UNKNOWN,), {"at": (round(x, 4), round(y, 4))})
        )

    def churn(self, count: int = 3, jitter: float = 0.15) -> Perturbation:
        """Apply small random moves to ``count`` random pallets (background noise)."""
        pallets = [p for p in self._world.pallet_bodies() if "unknown" not in p]
        chosen = list(self._rng.choice(pallets, size=min(count, len(pallets)), replace=False))
        for p in chosen:
            pos = self._world.body_pos(p)
            dx, dy = self._rng.uniform(-jitter, jitter, size=2)
            self._world.set_body_xy(p, float(pos[0] + dx), float(pos[1] + dy), z=0.06)
        return self._record(Perturbation("churn", tuple(chosen), {"jitter": jitter}))

    # -- scheduled application ----------------------------------------------
    #: DSL annotation keys that are metadata, not op arguments.
    _META_KEYS = ("at", "class")

    def apply(self, op: str, **kwargs: Any) -> Perturbation:
        """Apply an operation by name (used by the Scenario DSL).

        DSL timeline metadata (``at``, ``class``) is stripped from the op args and recorded in the
        Perturbation detail. NOTE: scheduling at a positive ``at`` (during the run) is not yet
        modeled — all perturbations apply at build time (correct for divergences planted at ``-Ns``;
        mid-run scheduling is a documented TODO).
        """
        method = getattr(self, op, None)
        if method is None or op.startswith("_"):
            raise ValueError(f"unknown invisible-hand op: {op!r}")
        meta = {k: kwargs.pop(k) for k in self._META_KEYS if k in kwargs}
        result: Perturbation = method(**kwargs)
        if meta:
            replaced = Perturbation(result.op, result.targets, {**result.detail, **meta})
            self._history[-1] = replaced
            return replaced
        return result

    def apply_schedule(self, schedule: list[dict[str, Any]]) -> list[Perturbation]:
        """Apply a list of ``{op: ..., **kwargs}`` perturbations in order."""
        out: list[Perturbation] = []
        for item in schedule:
            params = dict(item)
            op = params.pop("op")
            out.append(self.apply(op, **params))
        return out
