"""多視点統合：カメラ毎のスロットを床面座標に投影して統合する（world_model.md）。

カメラ内部・外部パラメータは MJCF（`sim/assets/warehouse.xml`）から読む。
Fusion 自体は契約（Encoder/SlotModule/Conditioner/Dynamics/Probe/Consistency）の
対象外のため、シグネチャはここで自由に設計している。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np
import torch
from torch import Tensor, nn

from gtwm.utils.paths import warehouse_xml_path


@dataclass
class CameraParams:
    """1台のカメラの内部・外部パラメータ。"""

    name: str
    pos: np.ndarray  # (3,) ワールド座標
    rot: np.ndarray  # (3,3) ワールド回転行列（列がカメラのローカル軸）
    fovy_deg: float
    width: int
    height: int


def load_camera_params(
    mjcf_path: Path | None = None, width: int = 128, height: int = 128
) -> dict[str, CameraParams]:
    """MJCF からカメラの姿勢・画角を読む（`sim/render.py` の投影規約と一致させる）。"""
    path = mjcf_path or warehouse_xml_path()
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    out: dict[str, CameraParams] = {}
    for i in range(model.ncam):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_CAMERA, i)
        if name is None:
            continue
        out[name] = CameraParams(
            name=name,
            pos=np.asarray(data.cam_xpos[i]).copy(),
            rot=np.asarray(data.cam_xmat[i]).reshape(3, 3).copy(),
            fovy_deg=float(model.cam_fovy[i]),
            width=width,
            height=height,
        )
    return out


def project_pixel_to_floor(
    cam: CameraParams, px: float, py: float, floor_z: float = 0.0
) -> np.ndarray:
    """ピクセル座標から床面（z=floor_z）へのレイキャスト（`sim/render.py:project_point` の逆演算）。

    MuJoCo のカメラ規約：ローカル -Z が視線方向、+X が右、+Y が上。
    """
    f = cam.height / (2.0 * math.tan(math.radians(cam.fovy_deg) / 2.0))
    x_local = (px - cam.width / 2.0) / f
    y_local = (cam.height / 2.0 - py) / f
    dir_local = np.array([x_local, y_local, -1.0])
    dir_world = cam.rot @ dir_local
    norm = np.linalg.norm(dir_world)
    if norm < 1e-9:
        return cam.pos.copy()
    dir_world = dir_world / norm
    if abs(dir_world[2]) < 1e-9:
        return cam.pos.copy()
    t = (floor_z - cam.pos[2]) / dir_world[2]
    return cam.pos + t * dir_world


class FusionModule(nn.Module):
    """カメラ毎のスロット集合を、床面座標埋め込みで条件付けたクロスアテンション
    プーリングにより K 個の統合スロットへ統合する。

    - 各カメラのスロットから正規化ピクセル位置を回帰する `position_head`（[0,1]^2）。
    - `project_pixel_to_floor` で床面座標へ変換し、`floor_embed` で埋め込みに変換して
      スロット表現に加算する（カメラ外部パラメータによる条件付け）。
    - 全カメラのスロット集合 [B,T,C*K,D] を、学習可能な K 個のクエリでアテンション
      プーリングし、統合スロット [B,T,K,D] を得る（Slot Attention と同型の縮約）。
    """

    def __init__(self, n_slots: int, slot_dim: int, n_heads: int = 4):
        super().__init__()
        self.n_slots = n_slots
        self.slot_dim = slot_dim
        self.position_head = nn.Linear(slot_dim, 2)
        self.floor_embed = nn.Linear(3, slot_dim)
        self.fuse_query = nn.Parameter(torch.randn(n_slots, slot_dim))
        self.attn = nn.MultiheadAttention(slot_dim, num_heads=n_heads, batch_first=True)
        self.norm = nn.LayerNorm(slot_dim)

    def forward(
        self, slots_per_cam: dict[str, Tensor], cam_params: dict[str, CameraParams]
    ) -> tuple[Tensor, dict[str, Tensor]]:
        """slots_per_cam: {cam_name: [B,T,K,D]} -> fused [B,T,K,D], floor_positions:{cam:[B,T,K,3]}.

        引数の slots_per_cam はカメラ毎のスロット集合、戻り値は統合スロットと
        各カメラの床面座標推定値。
        """
        names = list(slots_per_cam.keys())
        example = slots_per_cam[names[0]]
        b, t, k_in, d = example.shape

        floor_positions: dict[str, Tensor] = {}
        conditioned: list[Tensor] = []
        for name in names:
            slots = slots_per_cam[name]
            cam = cam_params[name]
            norm_px = torch.sigmoid(self.position_head(slots))  # [B,T,K,2] in [0,1]
            px = norm_px[..., 0] * cam.width
            py = norm_px[..., 1] * cam.height

            floor_xyz = _project_pixel_batch(cam, px, py)
            floor_positions[name] = floor_xyz

            floor_feat = self.floor_embed(floor_xyz)
            conditioned.append(slots + floor_feat)

        union = torch.cat(conditioned, dim=2)  # [B,T,C*K,D]
        union_flat = union.reshape(b * t, union.shape[2], d)

        query = self.fuse_query.unsqueeze(0).expand(b * t, -1, -1)
        fused_flat, _ = self.attn(query, union_flat, union_flat)
        fused_flat = self.norm(fused_flat + query)
        fused = fused_flat.reshape(b, t, self.n_slots, d)
        return fused, floor_positions


def _project_pixel_batch(cam: CameraParams, px: Tensor, py: Tensor) -> Tensor:
    """バッチ化された `project_pixel_to_floor`（微分不可、幾何変換のみ）。"""
    f = cam.height / (2.0 * math.tan(math.radians(cam.fovy_deg) / 2.0))
    x_local = (px - cam.width / 2.0) / f
    y_local = (cam.height / 2.0 - py) / f
    ones = torch.full_like(x_local, -1.0)
    dir_local = torch.stack([x_local, y_local, ones], dim=-1)  # [...,3]

    rot = torch.as_tensor(cam.rot, dtype=dir_local.dtype, device=dir_local.device)
    dir_world = torch.einsum("ij,...j->...i", rot, dir_local)
    dir_world = dir_world / dir_world.norm(dim=-1, keepdim=True).clamp_min(1e-9)

    pos = torch.as_tensor(cam.pos, dtype=dir_local.dtype, device=dir_local.device)
    denom = dir_world[..., 2]
    denom = torch.where(denom.abs() < 1e-9, torch.full_like(denom, 1e-9), denom)
    t = (0.0 - pos[2]) / denom
    return pos + t.unsqueeze(-1) * dir_world


__all__ = ["CameraParams", "load_camera_params", "project_pixel_to_floor", "FusionModule"]
