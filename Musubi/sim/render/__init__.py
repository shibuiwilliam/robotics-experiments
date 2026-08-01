"""Offscreen renderer — RGB / depth / segmentation, with geom→entity mapping and truth scoring.

Segmentation gives per-pixel ground-truth object identity, used to score perception (P4) and to
lift ER pixel points to world coordinates via depth (pixel→world, perception).
"""

from __future__ import annotations

from sim.render.renderer import RenderBundle, SceneRenderer

__all__ = ["SceneRenderer", "RenderBundle"]
