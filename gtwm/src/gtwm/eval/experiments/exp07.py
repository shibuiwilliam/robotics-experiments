"""EXP-07 WHAT-IF反実仮想の忠実性測定（H6、poc_plan.md 6.2「WHAT-IF反実仮想」）。

手順（poc_plan.md 6.2 原文）：介入3種（コンベア速度、担当人数、一時置き場位置）について、
事前にWHAT-IFで予測KPIと90%区間を記録し、実施した介入の実測と比較する。

このセッションの簡略化（docs/status.md に明記）：
- この sim は作業者の移動とパレット/ケースの実位置が独立している（`sim/drift.py` の
  docstring 参照：`wms_mock.generate_orders()` の割当はどこからも呼ばれず物理挙動に
  影響しない）。そのため3介入を文字通り物理接続する手段が無く、以下の代理として
  `sim/env.py`（`WarehouseEnv` の `active_workers`/`worker_speed_multiplier`/
  `worker_loop_overrides`）に実装する：
    - コンベア速度変更 → 全作業者の巡回速度を一律倍にする（コンベアが速いほど
      処理サイクルが速くなる、という業務上の意味を作業者の移動速度で代理する）。
    - 担当人数変更 → 指定した作業者をスプライン起点で静止させる（担当から外れた、
      の代理）。
    - 一時置き場位置変更 → 指定した作業者の巡回経路（ウェイポイント）を差し替える
      （動線が変わる、の代理）。
  これらは実際に `gtwm sim gen` で生成される物理エピソードに反映される「実測」であり、
  記録レベルの書き換え（`sim/drift.py` 方式）ではない点で、より物理的に誠実である。
- KPI（`queue_len`）は poc_plan.md 5.6/付録C の WHAT-IF エンジン（`kg/whatif/engine.py`）が
  既に実装している「対象ゾーンに存在するスロット数」の定義を踏襲するが、測定側では
  パレット/ケースが（この sim の制約により）ほぼ静的で常に同じゾーンに留まり続ける
  ため、type を作業者に限定した「対象ゾーンの作業者滞在数」を KPI として使う（介入は
  いずれも作業者の動線を操作するものであり、パレット/ケース込みの総数だと介入と無関係
  な定数項に信号が埋もれることを実測で確認した）。
- `do()` の介入は `kg/whatif/compiler.py` の既存の簡略化（行動ベクトル次元0への
  一様上書き、ADR候補として申し送り済み）をそのまま使う。介入の意味（速度・人数・
  位置）と、KPI予測に使われる数値（action_value）の対応は簡略化されたままである。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from omegaconf import DictConfig

from gtwm.kg.whatif.engine import run_whatif
from gtwm.sim.generate import generate_episode

TARGET_ZONE = "Pick"


@dataclass
class InterventionSpec:
    name: str
    query_do: str
    gen_kwargs: dict[str, Any] = field(default_factory=dict)


INTERVENTIONS: list[InterventionSpec] = [
    InterventionSpec(
        "conveyor_speed",
        "do(gt:Equipment_Conveyor_0001.speed := 1.8 * current)",
        {"worker_speed_multiplier": 1.8},
    ),
    InterventionSpec(
        "worker_headcount",
        "do(gt:Worker_0001.active := absent)",
        {"active_workers": frozenset({"worker:2", "worker:3"})},
    ),
    InterventionSpec(
        "staging_location",
        "do(gt:Worker_0003.staging_offset := current + 1.5)",
        {"worker_loop_overrides": {"worker:3": [(-2.0, 0.5), (0.0, -0.8), (3.5, 0.5), (0.0, 1.8)]}},
    ),
]


def _worker_zone_occupancy(
    poses_path: str, target_zone: str, t_center: float, half_window: float, duration_s: float
) -> float:
    """`t_center` を中心とした時間窓での「作業者のうち target_zone に居る人数」の
    フレーム平均（単一時刻の値は0/1に偏りノイズが大きいため、窓平均で滑らかにする）。
    """
    poses = pd.read_parquet(poses_path)
    workers = poses[poses["entity"].str.startswith("worker:")]
    lo = max(0.0, t_center - half_window)
    hi = min(duration_s, t_center + half_window)
    window = workers[(workers["t"] >= lo) & (workers["t"] <= hi)]
    if window.empty:
        return 0.0
    per_frame = window.groupby("t").apply(lambda g: float((g["zone"] == target_zone).sum()))
    return float(per_frame.mean())


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    duration_s = float(config.duration_s)
    horizon_s = float(config.horizon_s)
    window_half_s = float(config.window_half_s)
    probe_config = str(config.probe_config)
    base_set = str(config.base_set)
    samples = int(config.get("samples", 8))

    # 他の実験（exp04_seed0, exp05_seed0, wm_smoke 等）と同じ規約：`data/sim/` 直下に
    # 書き出す（gitignore 済み、`gtwm sim gen` で再生成できる状態を保つ）。
    base_set_name = f"{base_set}_base_seed{seed}"
    base_dir = generate_episode(base_set_name, 0, duration_s, seed)
    episode_id = base_dir.name

    pairs: list[dict[str, Any]] = []
    for spec in INTERVENTIONS:
        query = (
            f"PREDICT ?queue_len AT +{horizon_s:g}s WHERE station = gt:Zone_{TARGET_ZONE} "
            f"GIVEN {spec.query_do} SAMPLES {samples} INTERVAL 0.9"
        )
        result = run_whatif(query, episode_id, base_set_name, probe_config=probe_config)
        pred = result.predictions[0]

        intervened_dir = generate_episode(
            f"{base_set}_{spec.name}_seed{seed}",
            0,
            duration_s,
            seed,
            **spec.gen_kwargs,
        )
        measured = _worker_zone_occupancy(
            str(intervened_dir / "poses.parquet"),
            TARGET_ZONE,
            horizon_s,
            window_half_s,
            duration_s,
        )

        # measured が0近傍になりうる（作業者を1人減らすと対象ゾーンに誰も
        # 居ない窓が生じる）ため、分母に下限0.5を設けて相対誤差の発散を防ぐ
        # （poc_plan.md 付録Aに明示的な規定は無いsmoke限定の数値安全策）。
        relative_error = abs(pred.point_estimate - measured) / max(measured, 0.5)
        covered = pred.interval_low <= measured <= pred.interval_high
        pairs.append(
            {
                "intervention": spec.name,
                "predicted_point": pred.point_estimate,
                "predicted_interval_low": pred.interval_low,
                "predicted_interval_high": pred.interval_high,
                "measured": measured,
                "relative_error": relative_error,
                "covered": covered,
            }
        )

    relative_errors = [p["relative_error"] for p in pairs]
    coverage = [p["covered"] for p in pairs]
    return {
        "kpi_relative_error": float(np.mean(relative_errors)) if relative_errors else float("nan"),
        "interval_coverage_90": float(np.mean(coverage)) if coverage else float("nan"),
        "n_interventions": len(pairs),
        "pairs": pairs,
    }
