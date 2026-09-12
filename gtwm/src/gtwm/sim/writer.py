"""エピソードのデータ書出：`data/sim/<set>/<episode_id>/` への出力。

出力ファイル：cam_<id>.mp4, masks.npz, depth.npz, poses.parquet, occlusion.npz,
events.parquet, meta.json（sim.md のデータ書出仕様）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np
import pandas as pd

from gtwm.sim.render import CameraFrame

MASK_BACKGROUND = 0  # geom id + 1 したものを格納する。0 は背景。


class EpisodeWriter:
    """1エピソードぶんのファイルをストリーミングで書き出す。"""

    def __init__(
        self,
        output_dir: Path,
        cam_names: list[str],
        n_log: int,
        n_occlusion_entities: int,
        height: int,
        width: int,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cam_names = cam_names
        self.n_log = n_log
        self.height = height
        self.width = width

        self._video_writers = {
            cam: imageio.get_writer(
                self.output_dir / f"cam_{cam.split(':')[1]}.mp4",
                fps=10,
                codec="libx264",
                quality=None,
                output_params=["-crf", "18"],
            )
            for cam in cam_names
        }
        self._masks = {cam: np.zeros((n_log, height, width), dtype=np.uint16) for cam in cam_names}
        self._depth = {cam: np.zeros((n_log, height, width), dtype=np.float16) for cam in cam_names}
        self._occlusion = {
            cam: np.zeros((n_log, n_occlusion_entities), dtype=np.float32) for cam in cam_names
        }

    def add_frame(
        self, cam: str, t_idx: int, frame: CameraFrame, occlusion_row: np.ndarray
    ) -> None:
        self._video_writers[cam].append_data(frame.rgb)
        seg_store = np.where(frame.seg_id < 0, MASK_BACKGROUND, frame.seg_id + 1).astype(np.uint16)
        self._masks[cam][t_idx] = seg_store
        self._depth[cam][t_idx] = frame.depth.astype(np.float16)
        self._occlusion[cam][t_idx] = occlusion_row

    def close_and_write(
        self,
        poses_df: pd.DataFrame,
        events_df: pd.DataFrame,
        meta: dict[str, Any],
    ) -> None:
        for w in self._video_writers.values():
            w.close()

        masks_out: dict[str, np.ndarray] = {
            cam.split(":")[1]: arr for cam, arr in self._masks.items()
        }
        depth_out: dict[str, np.ndarray] = {
            cam.split(":")[1]: arr for cam, arr in self._depth.items()
        }
        occlusion_out: dict[str, np.ndarray] = {
            cam.split(":")[1]: arr for cam, arr in self._occlusion.items()
        }
        # numpy の型スタブは **dict 展開を誤って bool 引数と解釈するため無視する。
        np.savez_compressed(self.output_dir / "masks.npz", **masks_out)  # type: ignore[arg-type]
        np.savez_compressed(self.output_dir / "depth.npz", **depth_out)  # type: ignore[arg-type]
        np.savez_compressed(self.output_dir / "occlusion.npz", **occlusion_out)  # type: ignore[arg-type]
        poses_df.to_parquet(self.output_dir / "poses.parquet", index=False)
        events_df.to_parquet(self.output_dir / "events.parquet", index=False)
        (self.output_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
