"""EXP-03 記号条件付けのアブレーション（H2、poc_plan.md 6.2）の測定ロジック。

比較条件：(a) 条件なし、(b) 静的文脈（レイアウト・ゾーン制約）、(c) 動的文脈。
誤差は潜在空間誤差（予測潜在と実潜在のコサイン距離）を報告する。有効ホライズン
H* は記号空間誤差の代わりに、この潜在空間誤差が閾値 τ を超えない最大ホライズンを
使う（記号空間版は本実行時、`grounding.probes` を通した α 復号を追加して拡張する。
smoke ではエンコーダ・スロットモジュールの学習が不十分で記号復号そのものの精度が
低く、条件付け効果の切り分けにノイズが乗るため、まずは潜在空間で比較する）。

このセッション（06）の簡略化（docs/status.md に明記）：
- 「静的文脈」は ZONE_NAMES の工程順（入荷→検品→保管A→保管B→ピッキング→出荷）を
  隣接とみなした固定 KGSubgraph（時刻に依らず一定）。
- 「動的文脈」は各時刻 t の `poses.parquet` から実際のゾーン占有（どの個体がどの
  ゾーンにいるか）を KGSubgraph 化したもの（時刻ごとに変化する）。本来の
  poc_plan.md 3.2 H2 が言う「オーダー・作業割当・到着予定」（EPCIS記録）そのもの
  ではなく、その代理として現在の占有状態を使う（本実行時、`kg.epcis` で実際の
  記録済み信念から `conditioning.extract_subgraph` を使う形に拡張する）。
"""

from __future__ import annotations

from typing import Any

import torch
from omegaconf import DictConfig
from torch.nn import functional as F

from gtwm.grounding.conditioning import Conditioner, KGSubgraph
from gtwm.sim.env import ZONE_NAMES
from gtwm.utils.config import load_config
from gtwm.utils.device import get_device
from gtwm.utils.paths import repo_root
from gtwm.wm.dataset import WMSequenceDataset, list_episodes
from gtwm.wm.train import build_modules, encode_sequence


def _static_subgraph() -> KGSubgraph:
    """工程順に沿ったゾーン隣接（レイアウト）だけを持つ、時刻に依らない固定部分グラフ。"""
    nodes = [f"gt:Zone_{z}" for z in ZONE_NAMES]
    edges = [(i, i + 1, "gt:adjacentTo") for i in range(len(nodes) - 1)]
    return KGSubgraph(nodes=nodes, edges=edges, recent_discrepancy_count=0)


def _dynamic_subgraph(poses_row_by_entity: dict[str, str]) -> KGSubgraph:
    """時刻 t の実占有状態（個体→ゾーン）からの部分グラフ（動的文脈の代理、docstring参照）。"""
    nodes = [f"gt:Zone_{z}" for z in ZONE_NAMES]
    node_idx = {n: i for i, n in enumerate(nodes)}
    edges: list[tuple[int, int, str]] = []
    for entity, zone in poses_row_by_entity.items():
        zone_node = f"gt:Zone_{zone}"
        if zone_node not in node_idx:
            continue
        entity_idx = len(nodes)
        nodes.append(entity)
        edges.append((entity_idx, node_idx[zone_node], "gt:currentZone"))
    return KGSubgraph(nodes=nodes, edges=edges, recent_discrepancy_count=0)


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    device = get_device()
    wm_cfg = load_config(config.wm_config)
    modules = build_modules(wm_cfg, device)

    checkpoint_path = repo_root() / wm_cfg.train.checkpoint_dir / "best.pt"
    if checkpoint_path.exists():
        state = torch.load(checkpoint_path, map_location=device)
        modules.slot_module.load_state_dict(state["slot_module"])
        modules.fusion.load_state_dict(state["fusion"])
        modules.dynamics.load_state_dict(state["dynamics"])
        modules.encoder.adapter.load_state_dict(state["adapter"])
    modules.slot_module.eval()
    modules.fusion.eval()
    modules.dynamics.eval()

    conditioner = Conditioner(impl=config.conditioner_impl, cond_dim=wm_cfg.dynamics.cond_dim).to(
        device
    )
    conditioner.eval()

    episodes = list_episodes(config.data_set)
    if not episodes:
        raise FileNotFoundError(f"data/sim/{config.data_set} にエピソードがありません")
    cam_names = episodes[0].cameras
    ds = WMSequenceDataset(episodes, seq_len=config.seq_len, frame_stride=config.frame_stride)

    import pandas as pd

    poses_by_episode = {
        ep.episode_id: pd.read_parquet(ep.episode_dir / "poses.parquet") for ep in episodes
    }

    static_cond = conditioner([_static_subgraph()], device)  # [1,Dc]

    horizons = list(config.horizons_steps)
    errors: dict[str, dict[int, list[float]]] = {
        name: {h: [] for h in horizons} for name in ("none", "static", "dynamic")
    }

    for seq_idx in range(len(ds)):
        ep, start = ds._index[seq_idx]  # noqa: SLF001 - 実フレーム番号の逆引きに必要
        frames = ds[seq_idx].unsqueeze(0).to(device)
        with torch.no_grad():
            fused = encode_sequence(modules, frames, cam_names)  # [1,T,K,D]
        seq_len = fused.shape[1]
        poses = poses_by_episode[ep.episode_id]

        for t0 in range(seq_len):
            frame_idx = start + t0 * config.frame_stride
            t_s = frame_idx / ep.log_hz
            context = fused[:, t0]

            rows = poses[(poses["t"] - t_s).abs() < 0.5 / ep.log_hz]
            zone_by_entity = dict(zip(rows["entity_gt_id"], rows["zone"], strict=True))
            dynamic_cond = conditioner([_dynamic_subgraph(zone_by_entity)], device)

            for h in horizons:
                if t0 + h >= seq_len:
                    continue
                true_future = fused[:, t0 + h]
                conditions = (("none", None), ("static", static_cond), ("dynamic", dynamic_cond))
                for name, cond in conditions:
                    with torch.no_grad():
                        rollout = modules.dynamics.rollout(context, cond, None, h)
                    pred_future = rollout.mean(dim=0)[:, -1]
                    cos_sim = F.cosine_similarity(
                        pred_future.flatten(1), true_future.flatten(1)
                    ).item()
                    errors[name][h].append(1.0 - cos_sim)

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else float("nan")

    horizon_s = {h: h * config.frame_stride / episodes[0].log_hz for h in horizons}
    result: dict[str, Any] = {}
    for name in ("none", "static", "dynamic"):
        for h in horizons:
            result[f"latent_error_{name}_h{horizon_s[h]:g}s"] = _mean(errors[name][h])

    # H2 の目標指標：60秒に最も近いホライズンでの、無条件比の誤差改善率。
    closest_h = min(horizons, key=lambda h: abs(horizon_s[h] - 60.0))
    none_err = _mean(errors["none"][closest_h])
    dynamic_err = _mean(errors["dynamic"][closest_h])
    if none_err and none_err == none_err:  # NaN でない
        result["error_improvement_60s"] = (none_err - dynamic_err) / none_err
    else:
        result["error_improvement_60s"] = float("nan")

    tau = float(config.effective_horizon_tau)
    none_h_star = max(
        (horizon_s[h] for h in horizons if _mean(errors["none"][h]) <= tau), default=0.0
    )
    dynamic_h_star = max(
        (horizon_s[h] for h in horizons if _mean(errors["dynamic"][h]) <= tau), default=0.0
    )
    result["effective_horizon_ratio"] = (
        dynamic_h_star / none_h_star if none_h_star > 0 else float("nan")
    )
    result["closest_horizon_to_60s"] = horizon_s[closest_h]
    return result


__all__ = ["measure"]
