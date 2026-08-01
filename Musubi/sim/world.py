"""MusubiWorld — a deterministic MuJoCo micro-warehouse wrapper.

Owns the model/data, drives the sim clock (time comes from the sim, NFR-DETERM), maps bodies to
entity IRIs, and exposes the god-view ground truth (research-only; the system never sees it).
Object handling is idealized adhesion: a carried pallet is slaved to the lift-bot each step
(PROJECT.md §3.2 — no dexterous grasp).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from core.clock import SimClock
from core.ids import mint
from core.rng import RngRegistry

_DEFAULT_WORLD = Path(__file__).resolve().parent / "worlds" / "micro_warehouse.xml"

#: Axis-aligned zone footprints on the floor: name -> (cx, cy, half).
ZONES: dict[str, tuple[float, float, float]] = {
    "receiving": (-1.2, 1.2, 0.6),
    "quarantine": (1.2, 1.2, 0.6),
    "shipping": (1.2, -1.2, 0.6),
}


@dataclass(frozen=True)
class EntityState:
    """God-view state of a sim entity (ground truth for scoring)."""

    iri: str
    body: str
    position: tuple[float, float, float]
    zone: str | None
    visible: bool
    tagged: bool


class World:
    """A loaded MuJoCo world with deterministic stepping and entity/ground-truth access."""

    def __init__(self, seed: int = 0, world_path: Path | None = None) -> None:
        self._path = world_path or _DEFAULT_WORLD
        self.model = mujoco.MjModel.from_xml_path(str(self._path))
        self.data = mujoco.MjData(self.model)
        self.clock = SimClock()
        self.rng = RngRegistry(seed)
        self._seed = seed
        self._pallets = self._discover_pallets()
        self._carried: dict[str, str] = {}  # pallet body -> bot body
        self._tag_ok: dict[str, bool] = dict.fromkeys(self._pallets, True)
        bot_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "lift_bot")
        self._bot_home = np.array(
            self.model.body_pos[bot_id][:2], dtype=float
        )  # slide-joint origin
        mujoco.mj_forward(self.model, self.data)

    # -- setup ---------------------------------------------------------------
    def _discover_pallets(self) -> list[str]:
        names = []
        for i in range(self.model.nbody):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, i)
            if name and name.startswith("pallet_"):
                names.append(name)
        return names

    def reset(self) -> None:
        mujoco.mj_resetData(self.model, self.data)
        self.clock = SimClock()
        self.rng = RngRegistry(self._seed)
        self._carried.clear()
        self._tag_ok = dict.fromkeys(self._pallets, True)
        mujoco.mj_forward(self.model, self.data)

    # -- ids -----------------------------------------------------------------
    def entity_iri(self, body: str) -> str:
        return mint("entity", body)

    def pallet_bodies(self) -> list[str]:
        return list(self._pallets)

    def body_pos(self, body: str) -> np.ndarray:
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
        return np.array(self.data.xpos[bid], dtype=float)

    def _body_qpos_adr(self, body: str) -> int:
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
        jid = self.model.body_jntadr[bid]
        return int(self.model.jnt_qposadr[jid])

    def _body_dof_adr(self, body: str) -> int:
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
        return int(self.model.body_dofadr[bid])

    def _body_geom_ids(self, body: str) -> list[int]:
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
        return [g for g in range(self.model.ngeom) if int(self.model.geom_bodyid[g]) == bid]

    def set_body_xy(self, body: str, x: float, y: float, z: float | None = None) -> None:
        """Teleport a free body's position (used by the Invisible Hand and idealized carry)."""
        adr = self._body_qpos_adr(body)
        self.data.qpos[adr] = x
        self.data.qpos[adr + 1] = y
        if z is not None:
            self.data.qpos[adr + 2] = z
        mujoco.mj_forward(self.model, self.data)

    # -- stepping ------------------------------------------------------------
    def set_base_target(self, x: float, y: float) -> None:
        """Command the lift-bot base to world position (x, y).

        The base slide actuators command joint *displacement* from the bot's home position, so we
        offset the world target by the home origin.
        """
        self.data.ctrl[0] = x - self._bot_home[0]
        self.data.ctrl[1] = y - self._bot_home[1]

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
            self._apply_carry()
        self.clock.set(round(float(self.data.time), 9))

    def _apply_carry(self) -> None:
        if not self._carried:
            return
        for pallet, bot in self._carried.items():
            bot_pos = self.body_pos(bot)
            dof = self._body_dof_adr(pallet)
            self.data.qvel[dof : dof + 6] = 0.0  # keep the carried pallet inert
            self.set_body_xy(pallet, float(bot_pos[0]), float(bot_pos[1]), z=0.26)

    # -- idealized handling --------------------------------------------------
    def attach(self, pallet: str, bot: str = "lift_bot") -> None:
        """Idealized adhesion: slave the pallet to the bot and disable its collisions."""
        self._carried[pallet] = bot
        for gid in self._body_geom_ids(pallet):
            self.model.geom_contype[gid] = 0
            self.model.geom_conaffinity[gid] = 0

    def detach(self, pallet: str) -> None:
        self._carried.pop(pallet, None)
        for gid in self._body_geom_ids(pallet):
            self.model.geom_contype[gid] = 1
            self.model.geom_conaffinity[gid] = 1

    def is_carried(self, pallet: str) -> bool:
        return pallet in self._carried

    # -- tags & visibility (Invisible Hand hooks) ----------------------------
    def set_tag_readable(self, pallet: str, readable: bool) -> None:
        self._tag_ok[pallet] = readable

    def tag_readable(self, pallet: str) -> bool:
        return self._tag_ok.get(pallet, True)

    def is_visible(self, body: str) -> bool:
        return float(self.body_pos(body)[2]) > -0.2  # below-floor bodies are hidden

    # -- ground truth (god view; research only) ------------------------------
    def zone_of(self, x: float, y: float) -> str | None:
        for name, (cx, cy, half) in ZONES.items():
            if abs(x - cx) <= half and abs(y - cy) <= half:
                return name
        return None

    def ground_truth(self) -> dict[str, EntityState]:
        out: dict[str, EntityState] = {}
        for body in self._pallets:
            pos = self.body_pos(body)
            x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
            visible = z > -0.2
            out[self.entity_iri(body)] = EntityState(
                iri=self.entity_iri(body),
                body=body,
                position=(x, y, z),
                zone=self.zone_of(x, y) if visible else None,
                visible=visible,
                tagged=self._tag_ok.get(body, True) and "unknown" not in body,
            )
        return out

    @property
    def qpos(self) -> np.ndarray:
        return self.data.qpos.copy()

    @property
    def time(self) -> float:
        return float(self.data.time)
