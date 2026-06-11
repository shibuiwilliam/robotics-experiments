"""MuJoCo world wrapper — load XML, step, read state."""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np

from mws.core.logging import get_logger

logger = get_logger(__name__)


class MuJoCoWorld:
    """Wraps a MuJoCo simulation world.

    Provides stepping, state reading, and sensor extraction.
    """

    def __init__(self, xml_path: str | Path, seed: int = 0, qpos_noise: float = 0.0) -> None:
        self._xml_path = Path(xml_path)
        self._seed = seed
        self._rng = np.random.default_rng(seed)
        self._qpos_noise = qpos_noise

        if self._xml_path.exists():
            self._model = mujoco.MjModel.from_xml_path(str(self._xml_path))
        else:
            # Fallback: create a minimal world for testing
            logger.warning("XML not found, creating minimal world", path=str(xml_path))
            self._model = mujoco.MjModel.from_xml_string(_MINIMAL_XML)

        self._data = mujoco.MjData(self._model)
        self._step_count = 0
        self.apply_seed_perturbation()

    def apply_seed_perturbation(self) -> None:
        """Apply a seeded initial-state perturbation to qpos.

        MuJoCo's `mj_step` is itself deterministic, so without this the `seed`
        only affects numpy-side sampling and every seed yields the same rollout.
        When ``qpos_noise > 0`` this perturbs the initial generalized
        coordinates by ``N(0, qpos_noise)`` drawn from the seeded RNG, so the
        seed genuinely (and reproducibly) influences the physical rollout.
        Defaults to 0.0 (no perturbation) to preserve existing behaviour.
        """
        if self._qpos_noise <= 0.0 or self._model.nq == 0:
            return
        perturb = self._rng.normal(0.0, self._qpos_noise, size=self._model.nq)
        self._data.qpos[:] = self._data.qpos + perturb
        mujoco.mj_forward(self._model, self._data)

    def step(self, n: int = 1) -> None:
        """Advance simulation by n steps."""
        for _ in range(n):
            mujoco.mj_step(self._model, self._data)
            self._step_count += 1

    @property
    def time(self) -> float:
        """Current simulation time in seconds."""
        return self._data.time

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def nq(self) -> int:
        """Number of generalized coordinates."""
        return self._model.nq

    @property
    def nv(self) -> int:
        """Number of generalized velocities."""
        return self._model.nv

    def get_qpos(self) -> np.ndarray:
        """Get generalized positions."""
        return self._data.qpos.copy()

    def get_qvel(self) -> np.ndarray:
        """Get generalized velocities."""
        return self._data.qvel.copy()

    def get_body_positions(self) -> dict[str, np.ndarray]:
        """Get named body positions."""
        positions = {}
        for i in range(self._model.nbody):
            name = self._model.body(i).name
            if name:
                positions[name] = self._data.body(name).xpos.copy()
        return positions

    def get_sensor_data(self) -> dict[str, np.ndarray]:
        """Get all sensor data as a dict of name -> values."""
        sensors = {}
        for i in range(self._model.nsensor):
            name = self._model.sensor(i).name
            adr = self._model.sensor(i).adr[0]
            dim = self._model.sensor(i).dim[0]
            sensors[name] = self._data.sensordata[adr : adr + dim].copy()
        return sensors

    def reset(self) -> None:
        """Reset simulation to initial state."""
        mujoco.mj_resetData(self._model, self._data)
        self._step_count = 0

    @property
    def model(self) -> mujoco.MjModel:
        return self._model

    @property
    def data(self) -> mujoco.MjData:
        return self._data


_MINIMAL_XML = """
<mujoco model="warehouse">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 3"/>
    <geom type="plane" size="10 10 0.1"/>
    <body name="conveyor_motor" pos="2 0 0.5">
      <joint name="motor_joint" type="hinge" axis="0 0 1"/>
      <geom type="cylinder" size="0.2 0.3" rgba="0.8 0.3 0.3 1"/>
      <site name="motor_temp_sensor" pos="0 0 0.3"/>
    </body>
    <body name="shelf_unit" pos="-2 3 1">
      <geom type="box" size="1 0.3 1" rgba="0.6 0.6 0.8 1"/>
    </body>
    <body name="maintenance_robot" pos="0 -2 0.3">
      <joint name="robot_x" type="slide" axis="1 0 0"/>
      <joint name="robot_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.15 0.3" rgba="0.3 0.8 0.3 1"/>
    </body>
  </worldbody>
  <sensor>
    <jointpos name="motor_angle" joint="motor_joint"/>
    <jointvel name="motor_velocity" joint="motor_joint"/>
  </sensor>
</mujoco>
"""
