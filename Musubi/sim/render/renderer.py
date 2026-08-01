"""Offscreen renderer implementation (mujoco.Renderer)."""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from sim.world import World


@dataclass(frozen=True)
class RenderBundle:
    """A single camera capture: RGB, metric depth, and per-pixel entity IRI (or None)."""

    rgb: np.ndarray  # (H, W, 3) uint8
    depth: np.ndarray  # (H, W) float32, metres
    segmentation: np.ndarray  # (H, W) int, geom id (-1 = background)
    camera: str


class SceneRenderer:
    """Renders a :class:`World` offscreen. One renderer per (world, resolution)."""

    def __init__(self, world: World, height: int = 240, width: int = 320) -> None:
        self._world = world
        self._h, self._w = height, width
        self._renderer = mujoco.Renderer(world.model, height=height, width=width)
        # geom id -> body name -> entity IRI
        model = world.model
        self._geom_entity: dict[int, str] = {}
        for gid in range(model.ngeom):
            bid = int(model.geom_bodyid[gid])
            body = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
            if body and body.startswith("pallet_"):
                self._geom_entity[gid] = world.entity_iri(body)

    # -- captures ------------------------------------------------------------
    def rgb(self, camera: str = "overhead") -> np.ndarray:
        self._renderer.disable_depth_rendering()
        self._renderer.disable_segmentation_rendering()
        self._renderer.update_scene(self._world.data, camera=camera)
        return self._renderer.render().copy()

    def depth(self, camera: str = "overhead") -> np.ndarray:
        self._renderer.enable_depth_rendering()
        self._renderer.update_scene(self._world.data, camera=camera)
        out = self._renderer.render().copy()
        self._renderer.disable_depth_rendering()
        return out

    def segmentation(self, camera: str = "overhead") -> np.ndarray:
        self._renderer.enable_segmentation_rendering()
        self._renderer.update_scene(self._world.data, camera=camera)
        seg = self._renderer.render().copy()
        self._renderer.disable_segmentation_rendering()
        return seg[:, :, 0]  # geom id channel

    def capture(self, camera: str = "overhead") -> RenderBundle:
        return RenderBundle(
            rgb=self.rgb(camera),
            depth=self.depth(camera),
            segmentation=self.segmentation(camera),
            camera=camera,
        )

    # -- ground truth from segmentation --------------------------------------
    def visible_entities(self, camera: str = "overhead", min_pixels: int = 15) -> set[str]:
        """Entity IRIs visible in the segmentation (≥ ``min_pixels`` pixels)."""
        seg = self.segmentation(camera)
        gids, counts = np.unique(seg, return_counts=True)
        out: set[str] = set()
        for gid, count in zip(gids.tolist(), counts.tolist(), strict=True):
            if count >= min_pixels and gid in self._geom_entity:
                out.add(self._geom_entity[gid])
        return out

    def entity_centroid_px(
        self, entity_iri: str, camera: str = "overhead"
    ) -> tuple[int, int] | None:
        """The pixel centroid (row, col) of an entity's segmentation mask, or None if absent."""
        seg = self.segmentation(camera)
        gid = next((g for g, e in self._geom_entity.items() if e == entity_iri), None)
        if gid is None:
            return None
        mask = np.argwhere(seg == gid)
        if mask.size == 0:
            return None
        row, col = mask.mean(axis=0)
        return int(round(row)), int(round(col))

    def score_segmentation(self, camera: str = "overhead") -> dict[str, float]:
        """Precision/recall of segmentation-visible pallets vs god-view visible pallets."""
        truth = {s.iri for s in self._world.ground_truth().values() if s.visible}
        detected = self.visible_entities(camera)
        tp = len(truth & detected)
        precision = tp / len(detected) if detected else 1.0
        recall = tp / len(truth) if truth else 1.0
        return {"precision": precision, "recall": recall, "tp": float(tp)}

    def close(self) -> None:
        self._renderer.close()
