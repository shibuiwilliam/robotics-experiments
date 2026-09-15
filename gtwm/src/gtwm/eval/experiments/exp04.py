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
import pandas as pd
from omegaconf import DictConfig

from gtwm.eval.metrics import roc_auc
from gtwm.grounding.ground_run import TrainedProbeBundle, run_ground
from gtwm.grounding.train_probes import train_probes
from gtwm.sim.drift import DriftConfig, apply_drift, drift_change_report
from gtwm.sim.generate import generate_episode
from gtwm.utils.paths import data_dir

_HORIZON_S = 10.0


def _episode_mean_epsilon(
    episode_id: str, set_name: str, probe_config: str, pretrained: TrainedProbeBundle
) -> float:
    result = run_ground(episode_id, set_name, probe_config=probe_config, pretrained=pretrained)
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
    # `run_ground` は呼ぶたびに `train_probes(probe_config)` を再学習する設計だが、
    # このループは同一 probe_config で `n_baseline+n_drift` エピソード分を評価する
    # だけなので、一度だけ学習して使い回す（EXP-08 の概念発見ループで先に見つかった
    # のと同じ無駄な再計算を避ける。詳細は `ground_run.run_ground` の `pretrained`
    # docstring 参照）。
    trained = train_probes(probe_config)
    pretrained: TrainedProbeBundle = (trained[0], trained[1], trained[2], trained[3])

    baseline_scores: list[float] = []
    for i in range(n_baseline):
        ep_seed = seed * 10_000 + i
        generate_episode(set_name, i, duration_s, ep_seed)
        episode_id = f"ep_{i:04d}_seed{ep_seed}"
        baseline_scores.append(
            _episode_mean_epsilon(episode_id, set_name, probe_config, pretrained)
        )

    drift_cfg = DriftConfig(inspection_position_change=True, picking_order_change=True)
    drift_scores: list[float] = []
    drift_rows_changed = 0
    drift_inspecting_rows = 0
    for j in range(n_drift):
        idx = n_baseline + j
        ep_seed = seed * 10_000 + idx
        generate_episode(set_name, idx, duration_s, ep_seed, drift=drift_cfg)
        episode_id = f"ep_{idx:04d}_seed{ep_seed}"
        drift_scores.append(_episode_mean_epsilon(episode_id, set_name, probe_config, pretrained))
        # ドリフトが実際に記録を変更しているかを指標として残す。EXP-04 本実行
        # （2026-09-14）で `inspection_position_change` が実データに対して0行しか
        # 変更しておらず（`inspecting` が一度も発火しないため）、AUROC が偶然水準に
        # なっていたことが判明した。「処置が施されたか」を測らないと、検知性能の未達と
        # 処置の不在を区別できない（docs/results/EXP-04.md 参照）。
        # 生成済みの events は既にドリフト適用後なので、同じ変換をもう一度当てて
        # 「この変換がこのデータで何行に触れるのか」を計測する（no-op なら0のまま）。
        events_path = data_dir() / "sim" / set_name / episode_id / "events.parquet"
        if events_path.exists():
            stored = pd.read_parquet(events_path)
            report = drift_change_report(stored, apply_drift(stored, drift_cfg))
            drift_rows_changed += report["zone_changed"] + report["entity_changed"]
            drift_inspecting_rows += report["inspecting_rows"]

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
        # ドリフト注入の実効性。0 なら「検知できなかった」のではなく
        # 「処置が施されていない」ことを意味する（結果の解釈に必須）。
        "drift_rows_changed": drift_rows_changed,
        "drift_inspecting_rows": drift_inspecting_rows,
    }


__all__ = ["measure"]
