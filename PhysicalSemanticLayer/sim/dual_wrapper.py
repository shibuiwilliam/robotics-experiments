"""Multi-robot MuJoCo simulation wrapper.

Wraps a scene with multiple named robots (prefixed sensors/joints).
Each robot is accessed by its prefix (e.g., 'a_', 'b_').
"""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np
from numpy.typing import NDArray

from sim.wrapper import SCENES_DIR


class MultiRobotSim:
    """Simulation with multiple named robots.

    Args:
        scene_xml: Path to MJCF XML file.
        robot_prefixes: List of sensor/joint prefixes (e.g., ['a_', 'b_']).
        n_joints_per_robot: Number of joints per robot.
        seed: Random seed.
    """

    def __init__(
        self,
        scene_xml: str | Path | None = None,
        robot_prefixes: list[str] | None = None,
        n_joints_per_robot: int = 7,
        seed: int = 42,
    ) -> None:
        if scene_xml is None:
            scene_xml = SCENES_DIR / "dual_panda.xml"
        if robot_prefixes is None:
            robot_prefixes = ["a_", "b_"]

        self._model = mujoco.MjModel.from_xml_path(str(scene_xml))
        self._data = mujoco.MjData(self._model)
        self._rng = np.random.default_rng(seed)
        self._prefixes = robot_prefixes
        self._n_joints = n_joints_per_robot

        mujoco.mj_resetData(self._model, self._data)
        mujoco.mj_forward(self._model, self._data)

    @property
    def model(self) -> mujoco.MjModel:
        return self._model

    @property
    def data(self) -> mujoco.MjData:
        return self._data

    @property
    def dt(self) -> float:
        return float(self._model.opt.timestep)

    @property
    def time(self) -> float:
        return float(self._data.time)

    @property
    def robot_prefixes(self) -> list[str]:
        return list(self._prefixes)

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            mujoco.mj_step(self._model, self._data)

    def _sensor_value(self, name: str) -> float:
        sid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_SENSOR, name)
        adr = self._model.sensor_adr[sid]
        return float(self._data.sensordata[adr])

    def _sensor_array(self, name: str, dim: int) -> NDArray[np.float64]:
        sid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_SENSOR, name)
        adr = self._model.sensor_adr[sid]
        return np.array(self._data.sensordata[adr : adr + dim], dtype=np.float64)

    def get_robot_state(self, prefix: str) -> dict[str, object]:
        """Read sensor data for one robot by its prefix.

        Args:
            prefix: e.g., 'a_' or 'b_'.

        Returns:
            Native state dict compatible with PandaAdapter.to_ir().
        """
        jpos = np.array([self._sensor_value(f"{prefix}jpos_{i}") for i in range(self._n_joints)])
        jvel = np.array([self._sensor_value(f"{prefix}jvel_{i}") for i in range(self._n_joints)])
        ee_pos = self._sensor_array(f"{prefix}ee_pos", 3)
        ee_quat = self._sensor_array(f"{prefix}ee_quat", 4)

        return {
            "joint_positions": jpos,
            "joint_velocities": jvel,
            "ee_position": ee_pos,
            "ee_quaternion": ee_quat,
            "time": self.time,
        }

    def get_joint_limits(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Get joint limits for the first robot (same limits for identical robots)."""
        n = self._n_joints
        lower = np.zeros(n)
        upper = np.zeros(n)
        prefix = self._prefixes[0]
        for i in range(n):
            jname = f"{prefix}joint_{i}"
            jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, jname)
            if jid >= 0 and self._model.jnt_limited[jid]:
                lower[i] = self._model.jnt_range[jid, 0]
                upper[i] = self._model.jnt_range[jid, 1]
            else:
                lower[i] = -np.inf
                upper[i] = np.inf
        return lower, upper
