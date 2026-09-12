"""意味プローブ α（`probes.Probe`）のアンカー自己教師あり学習と F1/ECE 評価。

アンカー時刻の (entity, 真値ゾーン, 真値床面座標, 真値型) だけを教師に使う
（world_model.md「損失」節：全真値ではなくアンカー時刻の真値のみを使う）。
DETR 系の集合予測と同様、各サンプルで「現在のモデルの床面座標予測に最も近いスロット」を
その場で選んで教師信号を当てる（K=8 個のスロットのうちどれが対象個体かは事前に固定できない
ため、割当も学習対象の一部として扱う）。

F1/ECE の評価はシミュレーション真値（`poses.parquet`）を使ってよい
（`.claude/rules/world_model.md`：「α のテストはシミュレーション真値で F1 を計算」）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import imageio.v2 as imageio
import numpy as np
import torch
from omegaconf import DictConfig
from torch import Tensor

from gtwm.grounding.anchors_labels import (
    AnchorSample,
    build_anchor_samples,
    chronological_sample_split,
)
from gtwm.grounding.probes import Probe, calibrate_temperature, expected_calibration_error
from gtwm.sim.env import ZONE_NAMES
from gtwm.utils.config import load_config
from gtwm.utils.device import get_device
from gtwm.utils.paths import repo_root
from gtwm.utils.seed import seed_everything
from gtwm.wm.dataset import list_episodes
from gtwm.wm.fusion import CameraParams, load_camera_params
from gtwm.wm.train import WMModules, build_modules

FrameCache = dict[tuple[str, int], np.ndarray]


def _preload_frames(samples: list[AnchorSample]) -> FrameCache:
    """アンカーサンプルが参照する (episode, frame_idx) だけを、カメラ動画1本につき
    1回のリーダーオープンでまとめて読み込む。

    `wm.dataset.read_single_frame`（1呼び出しごとに `imageio.get_reader` を開き直す）を
    数百サンプル分ループで呼ぶと動画コンテナの再オープンが支配的コストになり、
    250ステップの学習が3分を大幅に超える（実測）。アンカー学習は同一エピソード内の
    少数フレームを繰り返し参照するため、事前に必要フレームだけキャッシュする。
    """
    by_episode: dict[str, tuple[AnchorSample, set[int]]] = {}
    for s in samples:
        eid = s.episode.episode_id
        if eid not in by_episode:
            by_episode[eid] = (s, {s.frame_idx})
        else:
            by_episode[eid][1].add(s.frame_idx)

    cache: FrameCache = {}
    for eid, (rep_sample, frame_idxs) in by_episode.items():
        ep = rep_sample.episode
        sorted_idxs = sorted(frame_idxs)
        per_cam: dict[int, list[np.ndarray]] = {idx: [] for idx in sorted_idxs}
        for cam in ep.cameras:
            cam_id = cam.split(":")[1]
            path = ep.episode_dir / f"cam_{cam_id}.mp4"
            reader = imageio.get_reader(path)
            try:
                for idx in sorted_idxs:
                    per_cam[idx].append(reader.get_data(idx))
            finally:
                reader.close()
        for idx in sorted_idxs:
            stacked = np.stack(per_cam[idx], axis=0)  # [C,H,W,3]
            stacked = stacked.transpose(0, 3, 1, 2).astype(np.float32) / 255.0  # [C,3,H,W]
            cache[(eid, idx)] = stacked
    return cache


@dataclass
class ProbeTrainResult:
    steps: int
    duration_s: float
    n_train_samples: int
    n_eval_samples: int
    zone_f1_macro: float
    zone_accuracy: float
    type_accuracy: float
    ece_before: float
    ece_after: float
    temperature: float
    eval_confusion: dict[str, dict[str, int]] = field(default_factory=dict)


def _encode_batch_slots(
    modules: WMModules,
    samples: list[AnchorSample],
    cam_names: list[str],
    cam_params: dict[str, CameraParams],
    device: str,
    frame_cache: FrameCache,
) -> tuple[Tensor, Tensor]:
    """N サンプル（各 T=1）をまとめて1回のフォワードで処理する（1件ずつのループより大幅に高速）。

    戻り値：融合スロット [N,K,D]、型ロジット [N,K,n_types]。
    """
    batch_np = np.stack(
        [frame_cache[(s.episode.episode_id, s.frame_idx)] for s in samples], axis=0
    )  # [N,C,3,H,W]
    frames = torch.from_numpy(batch_np).unsqueeze(1).to(device)  # [N,1,C,3,H,W]
    tokens = modules.encoder.encode(frames)  # [N,1,C,P,De]
    b, t, c, p, de = tokens.shape
    per_cam_tokens = tokens.permute(0, 2, 1, 3, 4).reshape(b * c, t, p, de)
    slots_flat, type_logits_flat = modules.slot_module(per_cam_tokens)  # [(N*C),1,K,*]
    k = slots_flat.shape[-2]
    d = slots_flat.shape[-1]
    n_types = type_logits_flat.shape[-1]
    slots_per_cam = {
        name: slots_flat.reshape(b, c, t, k, d)[:, i] for i, name in enumerate(cam_names)
    }
    type_logits_per_cam = type_logits_flat.reshape(b, c, t, k, n_types)
    fused, _ = modules.fusion(slots_per_cam, cam_params)  # [N,1,K,D]
    # 融合後スロットに対応する型ロジットは、カメラ間平均を近似として使う（型ヘッド自体は
    # カメラ間で重み共有のため、この平均は「複数視点からの型の合議」に相当する）。
    type_logits = type_logits_per_cam.mean(dim=1)  # [N,1,K,n_types]
    return fused[:, 0], type_logits[:, 0]  # [N,K,D], [N,K,n_types]


def _assign_slot(floor_pred: Tensor, gt_xy: tuple[float, float]) -> int:
    target = torch.tensor(gt_xy, dtype=floor_pred.dtype, device=floor_pred.device)
    dists = (floor_pred - target).norm(dim=-1)
    return int(torch.argmin(dists).item())


def _macro_f1(
    preds: list[int], labels: list[int], n_classes: int
) -> tuple[float, dict[str, dict[str, int]]]:
    f1s = []
    confusion: dict[str, dict[str, int]] = {}
    present = sorted(set(labels) | set(preds))
    for c in present:
        tp = sum(1 for p, y in zip(preds, labels, strict=True) if p == c and y == c)
        fp = sum(1 for p, y in zip(preds, labels, strict=True) if p == c and y != c)
        fn = sum(1 for p, y in zip(preds, labels, strict=True) if p != c and y == c)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1s.append(f1)
        confusion[ZONE_NAMES[c]] = {"tp": tp, "fp": fp, "fn": fn}
    macro = sum(f1s) / len(f1s) if f1s else 0.0
    return macro, confusion


def train_probes(
    config_path: str = "configs/grounding/probe_train_smoke.yaml",
) -> tuple[Probe, WMModules, list[str], dict[str, CameraParams], ProbeTrainResult]:
    """戻り値：`(probe, modules, cam_names, cam_params, result)`。

    `modules` は `gtwm ground run`（session 05 CLI）が同じ重み（アンカー学習で微調整済みの
    slot_module/fusion を含む）でそのままロールアウトに使えるように、ここで返す。
    """
    cfg: DictConfig = load_config(config_path)
    seed_everything(cfg.seed)
    device = get_device()

    wm_cfg = load_config(cfg.wm_config)
    modules = build_modules(wm_cfg, device)
    modules.encoder.eval()  # backbone は凍結（アダプタ含め既に session04 で学習済み想定）

    checkpoint_path = repo_root() / wm_cfg.train.checkpoint_dir / "best.pt"
    if checkpoint_path.exists():
        state = torch.load(checkpoint_path, map_location=device)
        modules.slot_module.load_state_dict(state["slot_module"])
        modules.fusion.load_state_dict(state["fusion"])
        modules.dynamics.load_state_dict(state["dynamics"])
        modules.encoder.adapter.load_state_dict(state["adapter"])

    episodes = list_episodes(cfg.data_set)
    if not episodes:
        raise FileNotFoundError(
            f"data/sim/{cfg.data_set} にエピソードがありません。`gtwm sim gen` で生成してください。"
        )
    cam_names = episodes[0].cameras
    cam_params = load_camera_params(width=128, height=128)

    all_samples: list[AnchorSample] = []
    for ep in episodes:
        all_samples.extend(build_anchor_samples(ep))
    if len(all_samples) < 8:
        raise ValueError(
            f"アンカー教師サンプルが少なすぎます（{len(all_samples)}件）。"
            "episodes を増やしてください。"
        )
    train_samples, eval_samples = chronological_sample_split(all_samples, cfg.eval_fraction)
    frame_cache = _preload_frames(all_samples)

    probe = Probe(slot_dim=wm_cfg.slots.slot_dim).to(device)
    params = (
        list(probe.parameters())
        + list(modules.slot_module.parameters())
        + list(modules.fusion.parameters())
    )
    optimizer = torch.optim.Adam(params, lr=cfg.lr)

    # ゾーンクラスの不均衡（例：Storage_A が過半数）に対する逆頻度重み。
    # 重み無しだと多数派クラスへの一様予測（実質シャッフルされていないだけの多数決）に
    # 崩壊しやすいことを実験的に確認した（`docs/status.md` 参照）。
    zone_counts = torch.zeros(len(ZONE_NAMES))
    for s in train_samples:
        zone_counts[s.zone_idx] += 1
    zone_class_weights = (1.0 / zone_counts.clamp_min(1.0)).to(device)

    start = time.time()
    step = 0
    while step < cfg.n_steps:
        idx = torch.randint(0, len(train_samples), (cfg.batch_size,))
        batch = [train_samples[i] for i in idx.tolist()]
        optimizer.zero_grad()

        slots, type_logits = _encode_batch_slots(
            modules, batch, cam_names, cam_params, device, frame_cache
        )
        floor_pred_all = probe.floor_head(slots)  # [N,K,2]
        zone_logit_all = probe.zone_head(slots)  # [N,K,n_zones]

        zone_targets = torch.tensor([s.zone_idx for s in batch], device=device)
        type_targets = torch.tensor([s.type_idx for s in batch], device=device)
        floor_targets = torch.tensor(
            [s.xy for s in batch], dtype=floor_pred_all.dtype, device=device
        )

        assign_idx = [
            _assign_slot(floor_pred_all[i].detach(), batch[i].xy) for i in range(len(batch))
        ]
        idx_range = torch.arange(len(batch), device=device)
        assign_idx_t = torch.tensor(assign_idx, device=device)

        zone_sel = zone_logit_all[idx_range, assign_idx_t]  # [N,n_zones]
        floor_sel = floor_pred_all[idx_range, assign_idx_t]  # [N,2]
        type_sel = type_logits[idx_range, assign_idx_t]  # [N,n_types]

        loss_zone = torch.nn.functional.cross_entropy(
            zone_sel, zone_targets, weight=zone_class_weights
        )
        loss_floor = torch.nn.functional.mse_loss(floor_sel, floor_targets)
        loss_type = torch.nn.functional.cross_entropy(type_sel, type_targets)
        total_loss = (
            cfg.loss_weights.zone * loss_zone
            + cfg.loss_weights.floor * loss_floor
            + cfg.loss_weights.type * loss_type
        )
        total_loss.backward()
        optimizer.step()
        step += cfg.batch_size

    duration_s = time.time() - start

    # --- 評価。温度は train セットで当てはめ、ECE は held-out の eval セットで計算する。
    def _collect(
        samples: list[AnchorSample],
    ) -> tuple[Tensor, Tensor, list[int], list[int], list[int]]:
        eval_batch = 16
        logits_chunks = []
        type_preds: list[int] = []
        labels_list = [s.zone_idx for s in samples]
        type_labels_list = [s.type_idx for s in samples]
        with torch.no_grad():
            for i in range(0, len(samples), eval_batch):
                chunk = samples[i : i + eval_batch]
                slots, type_logits = _encode_batch_slots(
                    modules, chunk, cam_names, cam_params, device, frame_cache
                )
                floor_pred = probe.floor_head(slots)  # [n,K,2]
                zone_logit = probe.zone_head(slots)  # [n,K,n_zones]
                idx = [_assign_slot(floor_pred[j], chunk[j].xy) for j in range(len(chunk))]
                rng = torch.arange(len(chunk), device=device)
                idx_t = torch.tensor(idx, device=device)
                logits_chunks.append(zone_logit[rng, idx_t])
                type_preds.extend(type_logits[rng, idx_t].argmax(dim=-1).tolist())
        stacked_logits = torch.cat(logits_chunks, dim=0)
        labels_tensor = torch.tensor(labels_list, device=stacked_logits.device)
        preds_list = stacked_logits.argmax(dim=-1).tolist()
        return stacked_logits, labels_tensor, preds_list, type_preds, type_labels_list

    train_logits, train_labels, _, _, _ = _collect(train_samples)
    eval_logits, eval_labels, eval_preds, eval_type_preds, eval_type_labels = _collect(eval_samples)
    type_accuracy = (
        sum(1 for p, y in zip(eval_type_preds, eval_type_labels, strict=True) if p == y)
        / len(eval_type_labels)
        if eval_type_labels
        else 0.0
    )

    temperature = calibrate_temperature(train_logits, train_labels)
    probe.log_temperature.data.fill_(torch.log(torch.tensor(temperature)))

    probs_before = eval_logits.softmax(dim=-1)
    probs_after = (eval_logits / temperature).softmax(dim=-1)
    ece_before = expected_calibration_error(probs_before, eval_labels)
    ece_after = expected_calibration_error(probs_after, eval_labels)

    eval_labels_list = eval_labels.tolist()
    macro_f1, confusion = _macro_f1(eval_preds, eval_labels_list, n_classes=len(ZONE_NAMES))
    accuracy = sum(1 for p, y in zip(eval_preds, eval_labels_list, strict=True) if p == y) / len(
        eval_labels_list
    )

    result = ProbeTrainResult(
        steps=step,
        duration_s=duration_s,
        n_train_samples=len(train_samples),
        n_eval_samples=len(eval_samples),
        zone_f1_macro=macro_f1,
        zone_accuracy=accuracy,
        type_accuracy=type_accuracy,
        ece_before=ece_before,
        ece_after=ece_after,
        temperature=temperature,
        eval_confusion=confusion,
    )
    return probe, modules, cam_names, cam_params, result


__all__ = ["ProbeTrainResult", "train_probes"]
