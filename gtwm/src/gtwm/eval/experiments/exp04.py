"""EXP-04 整合性ギャップの計測とドリフト注入（H3、poc_plan.md 6.2）の測定ロジック。

手順（poc_plan.md 6.2 原文）：「2週間の平常運転で ε_h の分布を推定し管理限界を設定。
その後、業務側と合意した手順変更（検品位置変更、ピッキング順序変更）を各3日実施し、
ε の応答を計測。変更前後の ε を用いてAUROCを算出」。

このセッション（08）の簡略化（docs/status.md に明記）：
- 2週間の平常運転ではなく `config.n_baseline_episodes` 本（既定3）の
  `config.duration_s` 秒（既定30秒）エピソードで代替する。
- ε は `configs/grounding/epsilon.yaml` の h10s（10秒先）を使う。h60s 以上の
  ホライズンは `frame_idx + h_frames < ep.n_frames` を満たせないため、
  duration が短い smoke では1サンプルも得られない（本実行では duration を
  60秒超にすれば同じロジックで h60s も評価できる）。
- ドリフトは `sim/drift.py` の `inspection_position_change` + `picking_order_change`
  を同時に有効化した1種類のみを扱う（poc_plan.md は2種を別々の3日間で試すが、
  smoke では両方を1本のエピソード集合にまとめる）。
- AUROC は「エピソードごとの平均 ε_10s」をスコアとして、ドリフト前(label=0)/後(label=1)
  の2群に対して計算する（`eval/metrics.roc_auc`）。
- `epsilon_daily_cv`（日次変動係数）は「日次」の代わりに baseline エピソード間の
  平均 ε_10s の変動係数（std/mean）で代替する。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from omegaconf import DictConfig

from gtwm.eval.metrics import roc_auc
from gtwm.grounding.ground_run import run_ground
from gtwm.sim.drift import DriftConfig
from gtwm.sim.generate import generate_episode

_HORIZON_S = 10.0


def _episode_mean_epsilon(episode_id: str, set_name: str, probe_config: str) -> float:
    result = run_ground(episode_id, set_name, probe_config=probe_config)
    for rec in result.epsilon_records:
        if rec.horizon_s == _HORIZON_S and rec.n_samples > 0:
            return float(rec.epsilon)
    return float("nan")


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    n_baseline = int(config.get("n_baseline_episodes", 3))
    n_drift = int(config.get("n_drift_episodes", 3))
    duration_s = float(config.get("duration_s", 30.0))
    probe_config = str(config.get("probe_config", "configs/grounding/probe_train_smoke.yaml"))
    set_name = f"exp04_seed{seed}"

    baseline_scores: list[float] = []
    for i in range(n_baseline):
        ep_seed = seed * 10_000 + i
        generate_episode(set_name, i, duration_s, ep_seed)
        episode_id = f"ep_{i:04d}_seed{ep_seed}"
        baseline_scores.append(_episode_mean_epsilon(episode_id, set_name, probe_config))

    drift_cfg = DriftConfig(inspection_position_change=True, picking_order_change=True)
    drift_scores: list[float] = []
    for j in range(n_drift):
        idx = n_baseline + j
        ep_seed = seed * 10_000 + idx
        generate_episode(set_name, idx, duration_s, ep_seed, drift=drift_cfg)
        episode_id = f"ep_{idx:04d}_seed{ep_seed}"
        drift_scores.append(_episode_mean_epsilon(episode_id, set_name, probe_config))

    scores = baseline_scores + drift_scores
    labels = [0] * len(baseline_scores) + [1] * len(drift_scores)
    valid = [(s, y) for s, y in zip(scores, labels, strict=True) if s == s]  # NaN除外
    valid_labels = {y for _, y in valid}
    if len(valid_labels) == 2:
        auroc = roc_auc([s for s, _ in valid], [y for _, y in valid])
    else:
        auroc = float("nan")

    baseline_arr = np.array([s for s in baseline_scores if s == s])
    drift_arr = np.array([s for s in drift_scores if s == s])
    cv = (
        float(baseline_arr.std() / baseline_arr.mean())
        if len(baseline_arr) > 0 and baseline_arr.mean() != 0
        else float("nan")
    )

    baseline_mean = float(baseline_arr.mean()) if len(baseline_arr) else float("nan")
    drift_mean = float(drift_arr.mean()) if len(drift_arr) else float("nan")
    return {
        "drift_detection_auroc": auroc,
        "epsilon_daily_cv": cv,
        "baseline_epsilon_10s_mean": baseline_mean,
        "drift_epsilon_10s_mean": drift_mean,
        "n_baseline_episodes": n_baseline,
        "n_drift_episodes": n_drift,
    }


__all__ = ["measure"]
