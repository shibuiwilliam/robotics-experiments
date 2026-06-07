"""MuJoCo smoke test — load Panda, step 100, print qpos.

Usage: python -m sim.smoke
"""

from __future__ import annotations

from sim.wrapper import MuJoCoSim


def main() -> None:
    print("=== MuJoCo Smoke Test ===")
    sim = MuJoCoSim(seed=42)
    print(f"Model loaded: {sim.model.nq} qpos, {sim.model.nv} qvel, {sim.n_joints} joints")
    print(f"Timestep: {sim.dt}s")

    sim.step(100)
    print(f"After 100 steps (t={sim.time:.3f}s):")
    print(f"  Joint positions: {sim.get_joint_positions()}")
    print(f"  Joint velocities: {sim.get_joint_velocities()}")

    ee_pos, ee_quat = sim.get_ee_pose()
    print(f"  EE position: {ee_pos}")
    print(f"  EE quaternion: {ee_quat}")
    print("=== PASS ===")


if __name__ == "__main__":
    main()
