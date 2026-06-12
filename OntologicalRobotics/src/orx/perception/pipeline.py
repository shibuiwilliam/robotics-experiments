"""知覚パイプライン: 観測リフティング → 切り出し → 埋め込み → 知覚イベント。"""

from __future__ import annotations

import math

import numpy as np

from orx.common.schemas import PerceptionEvent, RawObservation
from orx.perception.embedder import VisualEmbedder
from orx.perception.lifting import VendorMapping, lift
from orx.sim.world import CameraView

CROP_HALF = 24  # 切り出し半幅 [px]


def project_to_pixel(
    view: CameraView, position: tuple[float, float, float]
) -> tuple[int, int] | None:
    """世界座標→画素座標（カメラは -Z 視線、+Y 上）。視野外は None。"""
    p = np.asarray(position, dtype=float)
    p_cam = view.xmat.T @ (p - view.pos)
    if p_cam[2] > -1e-6:
        return None
    focal = 0.5 * view.size / math.tan(math.radians(view.fovy_deg) / 2)
    u = view.size / 2 + focal * (p_cam[0] / -p_cam[2])
    v = view.size / 2 - focal * (p_cam[1] / -p_cam[2])
    if not (0 <= u < view.size and 0 <= v < view.size):
        return None
    return int(u), int(v)


def crop_around(image: np.ndarray, u: int, v: int, half: int = CROP_HALF) -> np.ndarray:
    y0, y1 = max(0, v - half), min(image.shape[0], v + half)
    x0, x1 = max(0, u - half), min(image.shape[1], u + half)
    return image[y0:y1, x0:x1]


class PerceptionPipeline:
    """1ロボット分のリフティングと埋め込み付与を行う。"""

    def __init__(self, mapping: VendorMapping, embedder: VisualEmbedder | None) -> None:
        self.mapping = mapping
        self.embedder = embedder

    def process(
        self,
        obs: RawObservation,
        image: np.ndarray | None = None,
        view: CameraView | None = None,
    ) -> PerceptionEvent:
        detections = lift(obs, self.mapping)
        if self.embedder is not None and image is not None and view is not None:
            crops: list[np.ndarray] = []
            for det in detections:
                pixel = project_to_pixel(view, det.position)
                if pixel is None:
                    crops.append(np.zeros((0, 0, 3), dtype=np.uint8))
                else:
                    crops.append(crop_around(image, *pixel))
            embeddings = self.embedder.embed_crops(crops)
            detections = [
                det.model_copy(update={"embedding": emb})
                for det, emb in zip(detections, embeddings, strict=True)
            ]
        return PerceptionEvent(
            event_id=f"{obs.robot_id}-{obs.seq:06d}",
            robot_id=obs.robot_id,
            sim_time=obs.sim_time,
            detections=detections,
            oracle_truth_ids=list(obs.oracle_truth_ids),
        )
