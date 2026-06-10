"""MuJoCo simulation wrapper for PSL-Bench.

Provides a thin, deterministic wrapper around MuJoCo for:
  - Loading scenes
  - Stepping physics
  - Reading ground-truth state (for eval/ ONLY)
  - Reading sensor data (what adapters see)

Frame convention: z-up, SI units throughout.
"""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np
from numpy.typing import NDArray

# Path to scenes directory
SCENES_DIR = Path(__file__).parent / "scenes"


class MuJoCoSim:
    """Deterministic MuJoCo simulation wrapper.

    Args:
        scene_xml: Path to the MJCF XML file.
        seed: Random seed for reproducibility.
    """

    def __init__(self, scene_xml: str | Path | None = None, seed: int = 42) -> None:
        if scene_xml is None:
            scene_xml = SCENES_DIR / "panda_minimal.xml"
        self._model = mujoco.MjModel.from_xml_path(str(scene_xml))
        self._data = mujoco.MjData(self._model)
        self._rng = np.random.default_rng(seed)

        # Reset to default
        mujoco.mj_resetData(self._model, self._data)
        mujoco.mj_forward(self._model, self._data)

    @property
    def model(self) -> mujoco.MjModel:
        """Access the MuJoCo model (read-only for adapters)."""
        return self._model

    @property
    def data(self) -> mujoco.MjData:
        """Access the MuJoCo data (read-only for adapters; eval may read ground truth)."""
        return self._data

    @property
    def dt(self) -> float:
        """Simulation timestep in seconds."""
        return float(self._model.opt.timestep)

    @property
    def time(self) -> float:
        """Current simulation time in seconds."""
        return float(self._data.time)

    @property
    def n_joints(self) -> int:
        """Number of arm joints with jpos_N sensors (not all scene joints)."""
        return self.count_joints()

    def count_joints(self, prefix: str = "") -> int:
        """Count joints that have {prefix}jpos_N sensors."""
        count = 0
        while True:
            sid = mujoco.mj_name2id(
                self._model, mujoco.mjtObj.mjOBJ_SENSOR, f"{prefix}jpos_{count}"
            )
            if sid < 0:
                break
            count += 1
        return count

    def step(self, n: int = 1) -> None:
        """Advance simulation by n timesteps.

        Args:
            n: Number of steps to take (default 1).
        """
        for _ in range(n):
            mujoco.mj_step(self._model, self._data)

    def set_control(self, ctrl: NDArray[np.float64]) -> None:
        """Set actuator control inputs.

        Args:
            ctrl: Control vector matching number of actuators.
        """
        np.copyto(self._data.ctrl, ctrl)

    def get_joint_positions(self, prefix: str = "") -> NDArray[np.float64]:
        """Read joint position sensor data (what adapter sees).

        Args:
            prefix: Sensor name prefix (e.g. "a_" for dual-arm scenes).

        Returns:
            Array of joint positions from sensors (rad for revolute).
        """
        n = self.count_joints(prefix)
        positions = np.zeros(n)
        for i in range(n):
            sensor_id = mujoco.mj_name2id(
                self._model, mujoco.mjtObj.mjOBJ_SENSOR, f"{prefix}jpos_{i}"
            )
            if sensor_id >= 0:
                adr = self._model.sensor_adr[sensor_id]
                positions[i] = self._data.sensordata[adr]
        return positions

    def get_joint_velocities(self, prefix: str = "") -> NDArray[np.float64]:
        """Read joint velocity sensor data.

        Args:
            prefix: Sensor name prefix (e.g. "a_" for dual-arm scenes).

        Returns:
            Array of joint velocities from sensors (rad/s for revolute).
        """
        n = self.count_joints(prefix)
        velocities = np.zeros(n)
        for i in range(n):
            sensor_id = mujoco.mj_name2id(
                self._model, mujoco.mjtObj.mjOBJ_SENSOR, f"{prefix}jvel_{i}"
            )
            if sensor_id >= 0:
                adr = self._model.sensor_adr[sensor_id]
                velocities[i] = self._data.sensordata[adr]
        return velocities

    def get_ee_pose(self, prefix: str = "") -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Read end-effector pose from sensors.

        Args:
            prefix: Sensor name prefix (e.g. "a_" for dual-arm scenes).

        Returns:
            (position_xyz, quaternion_wxyz) — both from sensor readings.
        """
        pos_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_SENSOR, f"{prefix}ee_pos")
        quat_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_SENSOR, f"{prefix}ee_quat")

        pos_adr = self._model.sensor_adr[pos_id]
        quat_adr = self._model.sensor_adr[quat_id]

        pos = self._data.sensordata[pos_adr : pos_adr + 3].copy()
        quat = self._data.sensordata[quat_adr : quat_adr + 4].copy()

        return pos, quat

    def get_joint_limits(
        self, prefix: str = ""
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Get joint position limits for arm joints (those with {prefix}jpos_N sensors).

        Args:
            prefix: Sensor name prefix (e.g. "a_" for dual-arm scenes).

        Returns:
            (lower_limits, upper_limits) arrays matching count_joints(prefix).
        """
        n = self.count_joints(prefix)
        lower = np.zeros(n)
        upper = np.zeros(n)
        for i in range(n):
            sensor_id = mujoco.mj_name2id(
                self._model, mujoco.mjtObj.mjOBJ_SENSOR, f"{prefix}jpos_{i}"
            )
            if sensor_id < 0:
                lower[i] = -np.inf
                upper[i] = np.inf
                continue
            # The sensor's objid points to the joint
            joint_id = self._model.sensor_objid[sensor_id]
            if self._model.jnt_limited[joint_id]:
                lower[i] = self._model.jnt_range[joint_id, 0]
                upper[i] = self._model.jnt_range[joint_id, 1]
            else:
                lower[i] = -np.inf
                upper[i] = np.inf
        return lower, upper

    def render(
        self, width: int = 256, height: int = 256, camera: str | None = None
    ) -> NDArray[np.uint8]:
        """Render an RGB image from a MuJoCo camera.

        Args:
            width: Image width in pixels.
            height: Image height in pixels.
            camera: Camera name. If None, renders without a named camera.

        Returns:
            (height, width, 3) uint8 RGB array.
        """
        renderer = mujoco.Renderer(self._model, height, width)
        if camera is not None:
            cam_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
            if cam_id >= 0:
                renderer.update_scene(self._data, camera=cam_id)
            else:
                renderer.update_scene(self._data)
        else:
            renderer.update_scene(self._data)
        image = renderer.render()
        renderer.close()
        return np.array(image, dtype=np.uint8)

    # ── Ground truth (for eval/ ONLY — never call from src/psl or agents) ──

    def ground_truth_qpos(self) -> NDArray[np.float64]:
        """Ground truth: raw qpos vector from MuJoCo state.

        WARNING: For evaluation ONLY. Never call from src/psl or agents.
        """
        return np.array(self._data.qpos, dtype=np.float64)

    def ground_truth_qvel(self) -> NDArray[np.float64]:
        """Ground truth: raw qvel vector from MuJoCo state.

        WARNING: For evaluation ONLY. Never call from src/psl or agents.
        """
        return np.array(self._data.qvel, dtype=np.float64)

    def ground_truth_body_xpos(self, body_name: str) -> NDArray[np.float64]:
        """Ground truth: body position in world frame.

        WARNING: For evaluation ONLY. Never call from src/psl or agents.
        """
        body_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        return np.array(self._data.xpos[body_id], dtype=np.float64)

    def ground_truth_site_xpos(self, site_name: str) -> NDArray[np.float64]:
        """Ground truth: site position in world frame.

        WARNING: For evaluation ONLY. Never call from src/psl or agents.
        """
        site_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_SITE, site_name)
        return np.array(self._data.site_xpos[site_id], dtype=np.float64)
