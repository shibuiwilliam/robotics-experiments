"""MuJoCo Menagerie — model path resolution and registry.

Provides a registry of available robot models and resolves
model names to MJCF file paths.
"""

from __future__ import annotations

import os
from pathlib import Path

_SCENES_DIR = Path(__file__).parent.parent / "scenes"

# Registry of known models: name -> relative path from scenes dir
_MODEL_REGISTRY: dict[str, str] = {
    "panda": "panda_minimal.xml",
    "drone": "drone_minimal.xml",
    "dual_panda": "dual_panda.xml",
}


def get_model_path(model_name: str) -> Path:
    """Resolve a model name to an MJCF file path.

    Checks the MENAGERIE_DIR environment variable first,
    then falls back to the built-in scenes directory.

    Args:
        model_name: Short name of the model (e.g., "panda", "drone").

    Returns:
        Absolute path to the MJCF XML file.

    Raises:
        FileNotFoundError: If the model is not found.
    """
    # Check env override
    env_dir = os.environ.get("MENAGERIE_DIR")
    if env_dir:
        path = Path(env_dir) / f"{model_name}.xml"
        if path.exists():
            return path

    # Check registry
    if model_name in _MODEL_REGISTRY:
        path = _SCENES_DIR / _MODEL_REGISTRY[model_name]
        if path.exists():
            return path

    # Direct file check
    path = _SCENES_DIR / f"{model_name}.xml"
    if path.exists():
        return path

    msg = f"Model '{model_name}' not found in menagerie or scenes directory"
    raise FileNotFoundError(msg)


def list_available_models() -> list[str]:
    """List all available model names.

    Returns:
        Sorted list of model names that have existing XML files.
    """
    available: list[str] = []
    for name, rel_path in _MODEL_REGISTRY.items():
        if (_SCENES_DIR / rel_path).exists():
            available.append(name)
    return sorted(available)


def register_model(name: str, xml_path: str) -> None:
    """Register a new model in the runtime registry.

    Args:
        name: Short name for the model.
        xml_path: Relative path from scenes directory, or absolute path.
    """
    _MODEL_REGISTRY[name] = xml_path
