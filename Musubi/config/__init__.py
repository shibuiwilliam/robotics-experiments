"""Configuration package — single access point to config/registry.yaml.

Nothing in Musubi hardcodes model IDs, prices, budgets, or thresholds (CLAUDE.md §0-2).
Everything reads them from here.
"""

from __future__ import annotations

from config.registry import Registry, load_registry

__all__ = ["Registry", "load_registry"]
