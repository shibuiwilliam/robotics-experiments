"""Sim runner — standalone simulation command."""

from __future__ import annotations

from mws.core.logging import get_logger
from mws.sim.factory import create_world_from_config
from mws.sim.sensors import extract_observation_atoms

logger = get_logger(__name__)


def run_simulation(config_path: str, seed: int = 0, steps: int = 100) -> None:
    """Run a MuJoCo simulation and log observation atoms."""
    world = create_world_from_config(config_path, seed=seed)
    logger.info("Simulation started", config=config_path, seed=seed, steps=steps)

    world.step(steps)
    atoms = extract_observation_atoms(world, seed=seed)

    logger.info(
        "Simulation complete",
        sim_time=world.time,
        n_observations=len(atoms),
    )
    for atom in atoms[:5]:
        logger.info("Observation", summary=atom.text_summary[:80])
