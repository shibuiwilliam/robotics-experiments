"""世界モデルの学習ループ（Hydra 設定、MLflow 記録、チェックポイント、早期終了）。

損失は予測損失（潜在）のみを既定重み1.0で使う（アンカー接地損失・制約損失・同一性
損失は接地層の実装が揃う session 05 以降で `loss_weights` を通じて有効化する）。
"""

from __future__ import annotations

import os
import re
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import torch
from omegaconf import DictConfig
from scipy.optimize import linear_sum_assignment
from torch import Tensor
from torch.utils.data import DataLoader

from gtwm.utils.config import to_container
from gtwm.utils.device import get_device
from gtwm.utils.paths import repo_root
from gtwm.utils.seed import seed_everything
from gtwm.wm.dataset import WMSequenceDataset, camera_names, chronological_split, list_episodes
from gtwm.wm.dynamics import Dynamics
from gtwm.wm.encoder import Encoder, build_encoder
from gtwm.wm.fusion import FusionModule, load_camera_params
from gtwm.wm.slots import SlotModule


@dataclass
class WMModules:
    encoder: Encoder
    slot_module: SlotModule
    fusion: FusionModule
    dynamics: Dynamics


def build_modules(cfg: DictConfig, device: str) -> WMModules:
    enc_cfg = cfg.encoder
    encoder = build_encoder(
        enc_cfg.model_name, adapter_dim=enc_cfg.adapter_dim, freeze=enc_cfg.freeze_backbone
    )

    slots_cfg = cfg.slots
    slot_module = SlotModule(
        input_dim=enc_cfg.adapter_dim,
        n_slots=slots_cfg.n_slots,
        slot_dim=slots_cfg.slot_dim,
        n_iters=slots_cfg.n_iters,
        n_types=slots_cfg.n_types,
    )

    fusion_cfg = cfg.fusion
    fusion = FusionModule(
        n_slots=fusion_cfg.n_slots, slot_dim=fusion_cfg.slot_dim, n_heads=fusion_cfg.n_heads
    )

    dyn_cfg = cfg.dynamics
    dynamics = Dynamics(
        slot_dim=slots_cfg.slot_dim,
        d_model=dyn_cfg.d_model,
        n_layers=dyn_cfg.n_layers,
        n_heads=dyn_cfg.n_heads,
        cond_dim=dyn_cfg.cond_dim,
        action_dim=dyn_cfg.action_dim,
        ensemble_size=dyn_cfg.ensemble_size,
    )

    return WMModules(
        encoder=encoder.to(device),
        slot_module=slot_module.to(device),
        fusion=fusion.to(device),
        dynamics=dynamics.to(device),
    )


def encode_sequence(modules: WMModules, frames: Tensor, cam_names: list[str]) -> Tensor:
    """frames:[B,T,C,3,H,W] -> fused slots [B,T,K,D]。

    Encoder -> SlotModule（カメラ毎）-> Fusion の順で処理する。
    """
    b, t, c, ch, h, w = frames.shape
    tokens = modules.encoder.encode(frames)  # [B,T,C,P,De]
    p, de = tokens.shape[-2], tokens.shape[-1]

    per_cam_tokens = tokens.permute(0, 2, 1, 3, 4).reshape(b * c, t, p, de)  # [(B*C),T,P,De]
    slots_flat, _type_logits_flat = modules.slot_module(per_cam_tokens)  # [(B*C),T,K,D]
    k, d = slots_flat.shape[-2], slots_flat.shape[-1]
    slots_per_cam_tensor = slots_flat.reshape(b, c, t, k, d)

    slots_per_cam = {name: slots_per_cam_tensor[:, i] for i, name in enumerate(cam_names)}
    cam_params = load_camera_params(width=w, height=h)
    fused, _floor_positions = modules.fusion(slots_per_cam, cam_params)  # [B,T,K,D]
    return fused


def matched_prediction_loss(pred: Tensor, target: Tensor) -> Tensor:
    """スロットの並び順不定を考慮した順列不変損失。

    pred,target: [B,H,K,D]。各 (b,h) ごとにハンガリアン割当（scipy、勾配は流さない）で
    最良対応を求め、その対応でのMSEを損失にする。
    """
    b, h, k, _d = pred.shape
    total = pred.new_zeros(())
    count = 0
    for bi in range(b):
        for hi in range(h):
            p = pred[bi, hi]
            t = target[bi, hi]
            with torch.no_grad():
                cost = torch.cdist(p, t).cpu().numpy()
                row_ind, col_ind = linear_sum_assignment(cost)
            diff = p[row_ind] - t[col_ind]
            total = total + (diff**2).mean()
            count += 1
    return total / max(count, 1)


def latent_cosine_error(pred: Tensor, target: Tensor) -> float:
    """付録Aに準じた潜在空間誤差（コサイン距離の平均）。順列不変（ハンガリアン割当後）。"""
    b, h, k, _d = pred.shape
    dists = []
    for bi in range(b):
        for hi in range(h):
            p = pred[bi, hi]
            t = target[bi, hi]
            cost = torch.cdist(p, t).detach().cpu().numpy()
            row_ind, col_ind = linear_sum_assignment(cost)
            cos = torch.nn.functional.cosine_similarity(p[row_ind], t[col_ind], dim=-1)
            dists.append((1 - cos).mean().item())
    return float(np.mean(dists)) if dists else float("nan")


_MPS_FALLBACK_RE = re.compile(r"The operator '([^']+)'.*not currently supported on the MPS backend")


def _extract_mps_fallback_ops(caught: list[warnings.WarningMessage]) -> set[str]:
    ops: set[str] = set()
    for w in caught:
        m = _MPS_FALLBACK_RE.search(str(w.message))
        if m:
            ops.add(m.group(1))
    return ops


def _save_checkpoint(modules: WMModules, checkpoint_dir: Path) -> None:
    torch.save(
        {
            "slot_module": modules.slot_module.state_dict(),
            "fusion": modules.fusion.state_dict(),
            "dynamics": modules.dynamics.state_dict(),
            "adapter": modules.encoder.adapter.state_dict(),
        },
        checkpoint_dir / "best.pt",
    )


def train(cfg: DictConfig) -> dict[str, Any]:
    """`gtwm wm train` の実体。返り値は最終メトリクス（テストからも呼べるように）。"""
    device = get_device()
    seed_everything(cfg.train.seed)

    episodes = list_episodes(cfg.train.data_set)
    if not episodes:
        raise FileNotFoundError(
            f"data/sim/{cfg.train.data_set} にエピソードがありません。"
            "`gtwm sim gen` で生成してください。"
        )
    train_eps, eval_eps = chronological_split(episodes)
    cam_names = camera_names(episodes)

    train_ds = WMSequenceDataset(
        train_eps, seq_len=cfg.train.seq_len, frame_stride=cfg.train.frame_stride
    )
    train_loader = DataLoader(
        train_ds, batch_size=cfg.train.batch_size, shuffle=True, drop_last=True
    )

    modules = build_modules(cfg, device)
    params = [
        p
        for m in (modules.encoder.adapter, modules.slot_module, modules.fusion, modules.dynamics)
        for p in m.parameters()
    ]
    optimizer = torch.optim.Adam(params, lr=cfg.train.lr)

    # 新しめの mlflow はファイルストア（`mlruns/`）を既定で拒否し sqlite への移行を促す。
    # CLAUDE.md は「ローカル mlruns/」を明示的に固定しているため、ファイルストアを維持する。
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri(str(repo_root() / "mlruns"))
    mlflow.set_experiment(cfg.train.mlflow_experiment)

    checkpoint_dir = repo_root() / cfg.train.checkpoint_dir
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    best_loss = float("inf")
    patience = 0
    step = 0
    start = time.time()
    fallback_ops: set[str] = set()

    stop = False
    with mlflow.start_run():
        mlflow.log_params(to_container(cfg.train))
        for _epoch in range(cfg.train.max_epochs):
            if stop:
                break
            for batch in train_loader:
                frames = batch.to(device)  # [B,T,C,3,H,W]

                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    fused = encode_sequence(modules, frames, cam_names)  # [B,T,K,D]
                    context = fused[:, 0]  # [B,K,D]
                    target = fused[:, 1:]  # [B,T-1,K,D]
                    horizon = target.shape[1]

                    rollouts = modules.dynamics.rollout(context, None, None, horizon)  # [E,B,h,K,D]
                    pred = rollouts.mean(
                        dim=0
                    )  # アンサンブル平均で学習（不確実性は評価時に分散から）

                    loss = cfg.train.loss_weights.prediction * matched_prediction_loss(pred, target)
                fallback_ops.update(_extract_mps_fallback_ops(caught))

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                mlflow.log_metric("train_loss", float(loss.item()), step=step)
                step += 1

                if float(loss.item()) < best_loss - 1e-5:
                    best_loss = float(loss.item())
                    patience = 0
                    _save_checkpoint(modules, checkpoint_dir)
                else:
                    patience += 1
                    if patience >= cfg.train.early_stop_patience:
                        stop = True

                if stop or (cfg.train.max_steps is not None and step >= cfg.train.max_steps):
                    stop = True
                    break

        if not (checkpoint_dir / "best.pt").exists():
            # 改善が一度も無くても、少なくとも最終状態は必ず保存する。
            _save_checkpoint(modules, checkpoint_dir)

        duration_s = time.time() - start
        mlflow.log_metric("duration_s", duration_s)
        mlflow.log_metric("final_loss", float(loss.item()))

    return {
        "final_loss": float(loss.item()),
        "duration_s": duration_s,
        "steps": step,
        "eval_episodes": len(eval_eps),
        "mps_fallback_ops": sorted(fallback_ops),
    }


__all__ = [
    "WMModules",
    "build_modules",
    "encode_sequence",
    "matched_prediction_loss",
    "latent_cosine_error",
    "train",
]
