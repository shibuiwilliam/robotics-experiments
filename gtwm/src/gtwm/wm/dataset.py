"""`data/sim/<set>/` を読むデータローダ。フレーム間引きとシーケンス切出しを行う。

学習・評価の分割は時系列順（エピソードID順の後半を評価に回す）。ランダム分割はしない。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np
import torch
from torch import Tensor
from torch.utils.data import Dataset

from gtwm.utils.paths import data_dir


@dataclass
class EpisodeMeta:
    episode_dir: Path
    episode_id: str
    cameras: list[str]
    n_frames: int
    log_hz: float = 10.0


def list_episodes(set_name: str) -> list[EpisodeMeta]:
    """`data/sim/<set>/*/meta.json` をエピソードID順（＝時系列順）に並べて返す。"""
    root = data_dir() / "sim" / set_name
    episodes: list[EpisodeMeta] = []
    for ep_dir in sorted(root.iterdir()):
        meta_path = ep_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        n_frames = int(round(meta["duration_s"] * meta["log_hz"]))
        episodes.append(
            EpisodeMeta(
                episode_dir=ep_dir,
                episode_id=meta["episode_id"],
                cameras=meta["cameras"],
                n_frames=n_frames,
                log_hz=float(meta["log_hz"]),
            )
        )
    episodes.sort(key=lambda e: e.episode_id)
    return episodes


def chronological_split(
    episodes: list[EpisodeMeta], eval_fraction: float = 0.2
) -> tuple[list[EpisodeMeta], list[EpisodeMeta]]:
    """エピソードID順の後半を評価に回す（同一エピソードを学習・評価の両方に入れない）。"""
    if len(episodes) < 2:
        return episodes, []
    n_eval = max(1, int(round(len(episodes) * eval_fraction)))
    return episodes[:-n_eval], episodes[-n_eval:]


def read_frames_with_retry(
    path: Path, indices: list[int], retries: int = 3, delay_s: float = 1.0
) -> list[Any]:
    """`path` の動画から `indices` のフレームを読む（`imageio.get_reader` を都度開き直す）。

    高負荷下で `OSError: Could not load meta information` が一時的に発生することが
    実際に観測された（2026-09-14、EXP-04 本実行で複数回発生。ファイル自体は
    `ffprobe` で正常と確認済み＝一時的な資源枯渇が原因で、ファイル破損ではない）。
    ffmpeg サブプロセスは `imageio.get_reader()` 自体ではなく、最初の
    `reader.get_data()` 呼出時に遅延初期化されるため（imageio-ffmpeg の実装）、
    `get_reader()` だけを再試行しても意味が無い——open から get_data まで一連の
    操作全体を1つの再試行単位として、失敗時は reader を開き直す。"""
    last_exc: OSError | None = None
    for attempt in range(retries):
        reader = None
        try:
            reader = imageio.get_reader(path)
            frames = [reader.get_data(idx) for idx in indices]
            return frames
        except OSError as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(delay_s)
        finally:
            if reader is not None:
                reader.close()
    assert last_exc is not None
    raise last_exc


def _read_camera_frames(episode_dir: Path, cam_name: str, indices: list[int]) -> np.ndarray:
    """cam_<id>.mp4 から指定フレームインデックスを読む -> [T,H,W,3] uint8。"""
    cam_id = cam_name.split(":")[1]
    path = episode_dir / f"cam_{cam_id}.mp4"
    frames = read_frames_with_retry(path, indices)
    return np.stack(frames, axis=0)


class WMSequenceDataset(Dataset):
    """1サンプル = 1エピソード内の連続シーケンス（長さ seq_len、間引き frame_stride）。

    __getitem__ は frames: [T,C,3,H,W] float32(0..1) を返す（バッチ次元は DataLoader が付与）。
    """

    def __init__(self, episodes: list[EpisodeMeta], seq_len: int, frame_stride: int = 1):
        self.seq_len = seq_len
        self.frame_stride = frame_stride
        self._index: list[tuple[EpisodeMeta, int]] = []
        span = (seq_len - 1) * frame_stride + 1
        for ep in episodes:
            if ep.n_frames < span:
                continue
            n_windows = ep.n_frames - span + 1
            for start in range(0, n_windows, span):  # 非重複窓
                self._index.append((ep, start))

    def __len__(self) -> int:
        return len(self._index)

    def __getitem__(self, idx: int) -> Tensor:
        ep, start = self._index[idx]
        frame_indices = [start + i * self.frame_stride for i in range(self.seq_len)]

        per_cam = []
        for cam in ep.cameras:
            frames_u8 = _read_camera_frames(ep.episode_dir, cam, frame_indices)  # [T,H,W,3]
            per_cam.append(frames_u8)
        stacked = np.stack(per_cam, axis=1)  # [T,C,H,W,3]
        stacked = stacked.transpose(0, 1, 4, 2, 3).astype(np.float32) / 255.0  # [T,C,3,H,W]
        return torch.from_numpy(stacked)


def camera_names(episodes: list[EpisodeMeta]) -> list[str]:
    if not episodes:
        raise ValueError("エピソードが空です")
    return episodes[0].cameras


def read_single_frame(ep: EpisodeMeta, frame_idx: int) -> Tensor:
    """指定エピソードの1フレームを全カメラぶん読む -> [1,1,C,3,H,W] float32(0..1)（B=1,T=1）。

    接地層（session 05）のアンカー時刻教師あり学習のように、シーケンスではなく
    単一時刻のスロットだけが必要な用途向け。`encode_sequence` にそのまま渡せる。
    """
    per_cam = []
    for cam in ep.cameras:
        frame_u8 = _read_camera_frames(ep.episode_dir, cam, [frame_idx])  # [1,H,W,3]
        per_cam.append(frame_u8[0])
    stacked = np.stack(per_cam, axis=0)  # [C,H,W,3]
    stacked = stacked.transpose(0, 3, 1, 2).astype(np.float32) / 255.0  # [C,3,H,W]
    return torch.from_numpy(stacked).unsqueeze(0).unsqueeze(0)  # [1,1,C,3,H,W]


__all__ = [
    "EpisodeMeta",
    "list_episodes",
    "chronological_split",
    "WMSequenceDataset",
    "camera_names",
    "read_single_frame",
    "read_frames_with_retry",
]
