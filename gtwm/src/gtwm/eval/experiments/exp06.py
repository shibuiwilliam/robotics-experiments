"""EXP-06 シールド付き潜在計画（H5、poc_plan.md 6.2）の測定ロジック。

手順（poc_plan.md 6.2 原文）：「P0の模擬AGVで、進入禁止ゾーン・危険物隣接・容量制約を
含む100タスクを、シールド有／無で実行。出力：違反件数、タスク完了時間、経路長」。

このセッション（06）の簡略化（docs/status.md に明記。session 07 で
`kg.whatif` エンジンに接続する際に拡張可能）：
- 100タスクではなく `config.n_tasks`（smoke既定3）本。各タスクは実際に学習済みの
  smoke WM チェックポイントで符号化した実フレームのスロット状態を初期状態に使う。
- 「進入禁止ゾーン」制約のみを対象にする（`gt:ZoneCapacityShape` を capacity=0 の
  ゾーンとして解釈：`grounding/shield.RestrictedZoneShield`）。危険物隣接・容量制約は
  同じ違反度関数（`grounding/constraints.py`）が既に存在するが、単一 AGV・単一制御
  スロットのタスク設定では意味を成さないため、この実験では対象外とし docs/status.md に
  明記する。
- コスト関数は「目標ゾーンへの到達」を報酬にしつつ、ホライズン中間時点で禁止ゾーンを
  通過するとコストが下がる「近道」項を加える（`shortcut_bonus`）。これにより、
  シールド無しの場合は現実に違反が発生する、シールドありの場合は同じコスト関数の下でも
  違反候補が候補プールから除外される、という対比が生まれる（シールドが無意味な状況で
  0件対0件を比較しても H5 の検証にならないため）。
- 「タスク完了時間」の代理指標として、実行された行動列のコスト関数値
  （`task_cost`）を使う。「経路長」の代理指標として行動ベクトルの L2 ノルムの
  ホライズン合計（`path_length`）を使う。`throughput_loss` は
  `(task_cost_with_shield - task_cost_without_shield) / |task_cost_without_shield|`
  （poc_plan.md 3.1 の「スループット低下」に対応）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import torch
from omegaconf import DictConfig
from torch import Tensor

from gtwm.grounding.anchors_labels import episode_meta_from_dir
from gtwm.grounding.ground_run import encode_single_frame_slots
from gtwm.grounding.shield import RestrictedZoneShield
from gtwm.grounding.train_probes import train_probes
from gtwm.sim.env import ZONE_NAMES
from gtwm.utils.device import get_device
from gtwm.wm.dataset import list_episodes, read_single_frame
from gtwm.wm.planner import MPPIConfig, MPPIPlanner

CONTROLLED_SLOT_IDX = 0  # smoke規模では型付け前提が弱いため固定スロットを「AGV」とみなす


def _make_cost_fn(
    probe: Any, goal_zone_idx: int, restricted_zone_idx: int, shortcut_bonus: float
) -> Callable[[Tensor], Tensor]:
    def cost_fn(mean_rollout: Tensor) -> Tensor:
        zone_probs = probe.zone_probs(mean_rollout, calibrated=True)  # [N,h,K,n_zones]
        controlled = zone_probs[:, :, CONTROLLED_SLOT_IDX, :]  # [N,h,n_zones]
        goal_prob_final = controlled[:, -1, goal_zone_idx]
        goal_cost = -torch.log(goal_prob_final.clamp_min(1e-6))
        mid = controlled.shape[1] // 2
        shortcut_prob_mid = controlled[:, mid, restricted_zone_idx]
        return goal_cost - shortcut_bonus * shortcut_prob_mid

    return cost_fn


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    device = get_device()
    probe, modules, cam_names, cam_params, _train_result = train_probes(config.probe_config)
    probe.eval()
    modules.slot_module.eval()
    modules.fusion.eval()
    modules.dynamics.eval()

    episodes = list_episodes(config.data_set)
    if not episodes:
        raise FileNotFoundError(f"data/sim/{config.data_set} にエピソードがありません")

    goal_zone_idx = ZONE_NAMES.index(config.goal_zone)
    restricted_zone_idx = ZONE_NAMES.index(config.restricted_zone)

    mppi_cfg = MPPIConfig(
        n_samples=int(config.planner.n_samples),
        horizon=int(config.planner.horizon),
        action_dim=int(config.planner.action_dim),
        temperature=float(config.planner.temperature),
        noise_std=float(config.planner.noise_std),
    )

    violations_without_shield = 0
    violations_with_shield = 0
    costs_without_shield: list[float] = []
    costs_with_shield: list[float] = []
    path_lengths_without_shield: list[float] = []
    path_lengths_with_shield: list[float] = []

    n_tasks = int(config.n_tasks)
    for task_idx in range(n_tasks):
        ep_id = episodes[task_idx % len(episodes)].episode_id
        ep_meta = episode_meta_from_dir(ep_id, config.data_set)
        frame_idx = (task_idx * 7) % ep_meta.n_frames  # タスクごとに異なる開始フレーム
        frame = read_single_frame(ep_meta, frame_idx).to(device)
        with torch.no_grad():
            slots, _type_logits = encode_single_frame_slots(modules, frame, cam_names, cam_params)
        slots_t = slots.unsqueeze(0)  # [1,K,D]

        cost_fn = _make_cost_fn(
            probe, goal_zone_idx, restricted_zone_idx, float(config.shortcut_bonus)
        )

        torch.manual_seed(seed * 1000 + task_idx)
        planner_unshielded = MPPIPlanner(modules.dynamics, mppi_cfg, shield=None)
        actions_unshielded = planner_unshielded.plan(slots_t, None, cost_fn)

        shield = RestrictedZoneShield(
            dynamics=modules.dynamics,
            probe=probe,
            slots_t=slots_t,
            cond=None,
            restricted_zone_idx=restricted_zone_idx,
            violation_threshold=float(config.violation_threshold),
            controlled_slot_idx=CONTROLLED_SLOT_IDX,
        )
        torch.manual_seed(seed * 1000 + task_idx)  # 同じノイズ列で対比する
        planner_shielded = MPPIPlanner(modules.dynamics, mppi_cfg, shield=shield)
        actions_shielded = planner_shielded.plan(slots_t, None, cost_fn)

        for actions, shield_on in ((actions_unshielded, False), (actions_shielded, True)):
            degree = shield.symbolize_and_check(actions.unsqueeze(0))[0].item()
            violated = degree >= float(config.violation_threshold)
            with torch.no_grad():
                rollout = modules.dynamics.rollout(
                    slots_t, None, actions.unsqueeze(0), mppi_cfg.horizon
                )
                task_cost = cost_fn(rollout.mean(dim=0)).item()
            path_length = float(torch.linalg.vector_norm(actions, dim=-1).sum().item())

            if shield_on:
                violations_with_shield += int(violated)
                costs_with_shield.append(task_cost)
                path_lengths_with_shield.append(path_length)
            else:
                violations_without_shield += int(violated)
                costs_without_shield.append(task_cost)
                path_lengths_without_shield.append(path_length)

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else float("nan")

    mean_cost_without = _mean(costs_without_shield)
    mean_cost_with = _mean(costs_with_shield)
    throughput_loss = (
        (mean_cost_with - mean_cost_without) / abs(mean_cost_without)
        if mean_cost_without and mean_cost_without == mean_cost_without
        else float("nan")
    )

    return {
        "n_tasks": n_tasks,
        "violations_with_shield": violations_with_shield,
        "violations_without_shield": violations_without_shield,
        "task_cost_with_shield": mean_cost_with,
        "task_cost_without_shield": mean_cost_without,
        "throughput_loss": throughput_loss,
        "path_length_with_shield": _mean(path_lengths_with_shield),
        "path_length_without_shield": _mean(path_lengths_without_shield),
    }


__all__ = ["measure"]
