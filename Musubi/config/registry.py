"""Typed, cached loader for ``config/registry.yaml``.

The registry is the single source of variable values (model IDs, prices, budgets, dial
defaults, thresholds). Access it via :func:`load_registry`; never re-parse the YAML elsewhere.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent / "registry.yaml"
_MISSING = object()


class Registry:
    """Read-only view over the parsed registry with dotted-key access."""

    def __init__(self, data: dict[str, Any], source: Path) -> None:
        self._data = data
        self._source = source

    @property
    def source(self) -> Path:
        return self._source

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Fetch a nested value by dotted path, e.g. ``models.er.id``."""
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted_key: str) -> Any:
        """Like :meth:`get` but raises if the key is absent (no silent defaults)."""
        value = self.get(dotted_key, _MISSING)
        if value is _MISSING:
            raise KeyError(f"registry key not found: {dotted_key!r} (in {self._source})")
        return value

    # -- convenience accessors used across the codebase --------------------
    def model_id(self, family: str) -> str:
        return str(self.require(f"models.{family}.id"))

    def vcr_mode(self) -> str:
        """Effective VCR mode: env override (MUSUBI_VCR_MODE) wins over registry default."""
        env = os.environ.get("MUSUBI_VCR_MODE")
        if env:
            return env
        return str(self.require("vcr.default_mode"))

    def default_seed(self) -> int:
        return int(self.require("determinism.default_seed"))


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> Registry:
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"registry root must be a mapping, got {type(data).__name__}")
    return Registry(data, path)


def load_registry(path: str | Path | None = None) -> Registry:
    """Load (and cache) the registry. Defaults to ``config/registry.yaml``."""
    resolved = Path(path).resolve() if path is not None else _DEFAULT_PATH
    return _load_cached(str(resolved))
