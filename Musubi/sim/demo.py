"""``make demo S=<name>`` — record a GIF of the E0 relocate rollout (deterministic, offline).

Renders the lift-bot picking a pallet in receiving and carrying it to shipping, from the overhead
camera, and writes a GIF. Works headless with the offscreen renderer (no viewer / mjpython needed);
the Makefile invokes it under mjpython so the same entry point also works when a viewer is wanted.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np

from sim.render import SceneRenderer
from sim.world import World

_OUT_DIR = Path(__file__).resolve().parents[1] / "demos"


def _move_capture(
    world: World,
    renderer: SceneRenderer,
    frames: list[Any],
    x: float,
    y: float,
    tol: float = 0.1,
    max_steps: int = 9000,
    every: int = 120,
) -> None:
    world.set_base_target(x, y)
    target = np.array([x, y])
    for i in range(max_steps):
        world.step(1)
        if i % every == 0:
            frames.append(renderer.rgb("overhead"))
        if float(np.linalg.norm(world.body_pos("lift_bot")[:2] - target)) <= tol:
            break
    frames.append(renderer.rgb("overhead"))


def record_demo(scenario: str = "e0_smoke", seed: int = 0, out: Path | None = None) -> Path:
    world = World(seed=seed)
    renderer = SceneRenderer(world, height=240, width=320)
    frames: list[Any] = [renderer.rgb("overhead")]

    pallet = "pallet_1"
    p = world.body_pos(pallet)
    bot = world.body_pos("lift_bot")
    # standoff approach, pick, carry to shipping, place
    dx, dy = bot[0] - p[0], bot[1] - p[1]
    norm = (dx * dx + dy * dy) ** 0.5 or 1.0
    _move_capture(
        world, renderer, frames, float(p[0] + dx / norm * 0.6), float(p[1] + dy / norm * 0.6)
    )
    world.attach(pallet)
    _move_capture(world, renderer, frames, 1.2, -1.2)  # shipping
    world.detach(pallet)
    bot_now = world.body_pos("lift_bot")
    world.set_body_xy(pallet, float(bot_now[0]), float(bot_now[1]), z=0.06)
    world.step(60)
    frames.append(renderer.rgb("overhead"))
    renderer.close()

    out = out or (_OUT_DIR / f"{scenario}.gif")
    out.parent.mkdir(parents=True, exist_ok=True)
    imageio.mimsave(out, frames, duration=0.12, loop=0)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(prog="sim.demo")
    parser.add_argument("--scenario", default="e0_smoke")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out = record_demo(args.scenario, args.seed)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
