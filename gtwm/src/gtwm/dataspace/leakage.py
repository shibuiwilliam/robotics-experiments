"""漏洩評価（H8、poc_plan.md 3.2/6.2、付録A「漏洩評価」）。

拠点間で「もし潜在表現そのものを共有したら」という反実仮想に対する安全性評価。実際の
`dataspace.connector` は信念・予測のみを運び、潜在は運ばない（`models.py` の
`extra="forbid"` で型的に禁止済み、`tests/unit/test_dataspace_models.py` で検証）。
この評価は「禁止して正解だった」ことを裏付けるための攻撃シミュレーションである。

(a) 復元攻撃：フレーム全体の融合潜在（Kスロットの平均）→ 縮小画像を予測する小型線形
    デコーダを攻撃者が学習し、真の縮小画像との SSIM を測る（`eval.metrics.reconstruction_ssim`）。
(b) 人物再識別攻撃：`gt:Worker_*` に同定されたスロット潜在から作業者IDを分類する小型線形
    分類器を学習し、Top-1 精度をチャンス率（1/作業者数）と比較する（`eval.metrics.reid_top1`）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import Tensor, nn

from gtwm.eval.metrics import reconstruction_ssim, reid_top1
from gtwm.grounding.ground_run import encode_single_frame_slots
from gtwm.grounding.identity import TrackedEntity, resolve_identities
from gtwm.grounding.train_probes import train_probes
from gtwm.utils.device import get_device
from gtwm.wm.dataset import EpisodeMeta, list_episodes, read_single_frame
from gtwm.wm.fusion import CameraParams
from gtwm.wm.train import WMModules

_RECON_SIZE = 16  # 復元先の縮小画像サイズ（16x16x3）。攻撃者コストを smoke で抑えるための簡略化。


@dataclass
class LeakageResult:
    reconstruction_ssim: float
    reid_top1_accuracy: float
    reid_chance_rate: float
    n_reconstruction_samples: int
    n_reid_samples: int


def _downsample(frame_chw: np.ndarray, size: int) -> np.ndarray:
    """[3,H,W] の画像を最近傍法で [size,size,3] に縮小する（scikit-image 非依存）。"""
    _, h, w = frame_chw.shape
    rows = (np.linspace(0, h - 1, size)).astype(int)
    cols = (np.linspace(0, w - 1, size)).astype(int)
    small = frame_chw[:, rows][:, :, cols]  # [3,size,size]
    return small.transpose(1, 2, 0)  # [size,size,3]


def _collect_samples(
    episodes: list[EpisodeMeta],
    modules: WMModules,
    cam_names: list[str],
    cam_params: dict[str, CameraParams],
    stride: int,
) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray], list[int]]:
    """(scene_latents, recon_targets, worker_latents, worker_labels) を集める。"""
    device = get_device()
    scene_latents: list[np.ndarray] = []
    recon_targets: list[np.ndarray] = []
    worker_latents: list[np.ndarray] = []
    worker_labels: list[int] = []
    worker_ids = ["gt:Worker_0001", "gt:Worker_0002", "gt:Worker_0003"]

    for ep in episodes:
        poses = pd.read_parquet(ep.episode_dir / "poses.parquet")
        for frame_idx in range(0, ep.n_frames, stride):
            t_s = frame_idx / ep.log_hz
            frame = read_single_frame(ep, frame_idx).to(device)
            with torch.no_grad():
                slots, _type_logits = encode_single_frame_slots(
                    modules, frame, cam_names, cam_params
                )
            scene_latent = slots.mean(dim=0).cpu().numpy()  # [D]（フレーム全体を代表する潜在）
            scene_latents.append(scene_latent)
            recon_targets.append(_downsample(frame[0, 0, 0].cpu().numpy(), _RECON_SIZE))

            atol = 0.5 / ep.log_hz
            pose_rows = poses[np.isclose(poses["t"].to_numpy(), t_s, atol=atol)]
            entities = [
                TrackedEntity(
                    entity_gt_id=str(row.entity_gt_id),
                    predicted_xy=np.array([row.x, row.y]),
                    appearance=np.zeros(1),
                    last_seen_step=frame_idx,
                )
                for row in pose_rows.itertuples()
            ]
            if not entities:
                continue
            slot_positions = np.zeros(
                (slots.shape[0], 2)
            )  # 位置ヘッド無しで簡略化（型は再利用しない）
            slot_appearance = np.zeros((slots.shape[0], 1))
            assignment = resolve_identities(
                slot_positions, slot_appearance, entities, distance_gate=1e9, appearance_weight=0.0
            )
            for slot_idx, entity_gt_id in assignment.slot_to_entity.items():
                if entity_gt_id in worker_ids:
                    worker_latents.append(slots[slot_idx].cpu().numpy())
                    worker_labels.append(worker_ids.index(entity_gt_id))

    return scene_latents, recon_targets, worker_latents, worker_labels


class _LinearDecoder(nn.Module):
    def __init__(self, in_dim: int, out_dim: int):
        super().__init__()
        self.fc = nn.Linear(in_dim, out_dim)

    def forward(self, x: Tensor) -> Tensor:
        return self.fc(x)


def _train_linear_attacker(x: np.ndarray, y: np.ndarray, n_steps: int = 200) -> _LinearDecoder:
    device = get_device()
    xt = torch.from_numpy(x).float().to(device)
    yt = torch.from_numpy(y).float().to(device)
    model = _LinearDecoder(xt.shape[1], yt.shape[1]).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-2)
    for _ in range(n_steps):
        opt.zero_grad()
        pred = model(xt)
        loss = nn.functional.mse_loss(pred, yt)
        loss.backward()
        opt.step()
    return model


def _train_linear_classifier(
    x: np.ndarray, y: np.ndarray, n_classes: int, n_steps: int = 200
) -> nn.Module:
    device = get_device()
    xt = torch.from_numpy(x).float().to(device)
    yt = torch.from_numpy(y).long().to(device)
    model = nn.Linear(xt.shape[1], n_classes).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-2)
    for _ in range(n_steps):
        opt.zero_grad()
        logits = model(xt)
        loss = nn.functional.cross_entropy(logits, yt)
        loss.backward()
        opt.step()
    return model


def run_leakage_evaluation(
    set_name: str, probe_config: str, frame_stride: int = 10, train_frac: float = 0.7
) -> LeakageResult:
    """`set_name` のエピソード群に対する復元攻撃・再識別攻撃を実行する。"""
    probe, modules, cam_names, cam_params, _result = train_probes(probe_config)
    probe.eval()
    modules.slot_module.eval()
    modules.fusion.eval()

    episodes = list_episodes(set_name)
    scene_latents, recon_targets, worker_latents, worker_labels = _collect_samples(
        episodes, modules, cam_names, cam_params, stride=frame_stride
    )

    device = get_device()
    n_scene = len(scene_latents)
    split = max(1, int(n_scene * train_frac))
    x_scene = np.stack(scene_latents)
    y_scene = np.stack(recon_targets).reshape(n_scene, -1) / 255.0
    decoder = _train_linear_attacker(x_scene[:split], y_scene[:split])
    with torch.no_grad():
        eval_in = torch.from_numpy(x_scene[split:]).float().to(device)
        pred_eval = decoder(eval_in).cpu().numpy()
    ssim = (
        reconstruction_ssim(pred_eval, y_scene[split:]) if pred_eval.shape[0] > 0 else float("nan")
    )

    n_worker = len(worker_latents)
    reid_acc = float("nan")
    chance = 1.0 / 3.0
    if n_worker >= 6:  # 学習・評価に最低限必要な件数
        w_split = max(1, int(n_worker * train_frac))
        x_w = np.stack(worker_latents)
        y_w = np.array(worker_labels)
        clf = _train_linear_classifier(x_w[:w_split], y_w[:w_split], n_classes=3)
        with torch.no_grad():
            w_eval_in = torch.from_numpy(x_w[w_split:]).float().to(device)
            preds = clf(w_eval_in).argmax(dim=-1).cpu().numpy().tolist()
        reid_acc = reid_top1(preds, y_w[w_split:].tolist())

    return LeakageResult(
        reconstruction_ssim=float(ssim),
        reid_top1_accuracy=float(reid_acc),
        reid_chance_rate=chance,
        n_reconstruction_samples=n_scene - split,
        n_reid_samples=n_worker - (max(1, int(n_worker * train_frac)) if n_worker >= 6 else 0),
    )
