"""World factory — create MuJoCo worlds from config."""

from __future__ import annotations

from pathlib import Path

import yaml

from mws.core.logging import get_logger
from mws.sim.world import MuJoCoWorld

logger = get_logger(__name__)


def create_world_from_config(config_path: str, seed: int = 0) -> MuJoCoWorld:
    """Create a MuJoCo world from a YAML config file.

    Falls back to the built-in minimal world if XML is not found.
    """
    config_file = Path(config_path)
    xml_path = ""

    if config_file.exists():
        with open(config_file) as f:
            config = yaml.safe_load(f)
        xml_path = config.get("world", {}).get("mujoco_xml", "")
        seed = config.get("world", {}).get("seed", seed)
        logger.info("Creating world from config", config=config_path, xml=xml_path)
    else:
        logger.warning("Config not found, using defaults", path=config_path)

    return MuJoCoWorld(xml_path=xml_path, seed=seed)
