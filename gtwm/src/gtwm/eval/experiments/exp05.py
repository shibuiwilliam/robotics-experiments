"""EXP-05 乖離注入と検知（H4、poc_plan.md 6.2）の測定ロジック。

手順（poc_plan.md 6.2 原文）：「乖離5型×10件以上を、時間帯と対象を無作為化して2週間に
分散して注入。注入台帳は実験者のみが保持し、検知側には知らせない（盲検）」。
出力：型別の検知率、誤報数／日、検知遅延分布、誤報の原因分類。

このセッション（08）の簡略化（docs/status.md に明記）：
- 2週間の分散注入ではなく `config.n_episodes` 本（既定2）の `config.duration_s` 秒
  （既定30秒）エピソードに `configs/realism/p1.yaml` 相当の注入をまとめて入れる。
- 「誤報数／日」は、実際の evaluated duration（秒）から日数に換算する
  （`n_days = total_duration_s / 86400`）。smoke の数十秒では `n_days` が極小になり
  値が跳ねるため、本実行の実データでの評価が必須（report.md に明記）。
- 盲検境界：このモジュール自身は `data/injections` を読まない。実際の読み出しは
  `eval/scoring.load_injection_ledger()`（唯一許可されたモジュール）が行い、
  ここではその戻り値を受け取るだけ。検知パイプライン（`grounding/ground_run.run_ground`）
  は注入設定を一切知らない状態でエピソードを処理する。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from omegaconf import DictConfig

from gtwm.eval.metrics import detection_rate, false_alarms_per_day
from gtwm.eval.scoring import load_injection_ledger, score_detection
from gtwm.grounding.ground_run import run_ground
from gtwm.grounding.ledger import DiscrepancyLedger
from gtwm.sim.generate import generate_episode
from gtwm.sim.realism import load_realism_config


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    n_episodes = int(config.get("n_episodes", 2))
    duration_s = float(config.get("duration_s", 30.0))
    probe_config = str(config.get("probe_config", "configs/grounding/probe_train_smoke.yaml"))
    realism_path = str(config.get("realism_config", "configs/realism/p1.yaml"))
    set_name = f"exp05_seed{seed}"
    realism_cfg = load_realism_config(realism_path)

    n_injected_total = 0
    n_detected_total = 0
    n_false_alarms_total = 0
    all_latencies: list[float] = []
    all_detected_types: list[str] = []
    total_duration_s = 0.0

    for i in range(n_episodes):
        ep_seed = seed * 10_000 + i
        generate_episode(set_name, i, duration_s, ep_seed, realism=realism_cfg)
        episode_id = f"ep_{i:04d}_seed{ep_seed}"
        result = run_ground(episode_id, set_name, probe_config=probe_config)

        # 盲検境界：注入台帳の読み出しは eval/scoring.py 経由のみ（唯一の例外モジュール）。
        injection_ledger = load_injection_ledger(episode_id)

        ledger_db = DiscrepancyLedger(result.output_dir / "ledger.sqlite")
        open_entries = ledger_db.list_by_status("open")
        ledger_db.close()

        # time_tolerance_s は injection_late_registration_delay_s（既定最大300秒、
        # configs/realism/p1.yaml）を包含できる余裕を持たせる。ledger.py 側の
        # detected_at は record の t_obs 基準（record_consistency.py 参照）なので、
        # late_registration 注入自体の遅延時間そのものが検知時刻との差になり得るため。
        scoring = score_detection(open_entries, injection_ledger, time_tolerance_s=400.0)
        n_injected_total += scoring.n_injected
        n_detected_total += scoring.n_detected
        n_false_alarms_total += scoring.n_false_alarms
        all_latencies.extend(scoring.latencies_s)
        all_detected_types.extend(scoring.detected_types)
        total_duration_s += duration_s

    n_days = total_duration_s / 86400.0
    det_rate = detection_rate(n_detected_total, n_injected_total) if n_injected_total > 0 else 0.0
    fa_per_day = false_alarms_per_day(n_false_alarms_total, n_days) if n_days > 0 else float("nan")
    latency_median = float(np.median(all_latencies)) if all_latencies else float("nan")

    per_type_counts: dict[str, int] = {}
    for t in all_detected_types:
        per_type_counts[t] = per_type_counts.get(t, 0) + 1

    return {
        "detection_rate": det_rate,
        "false_alarms_per_day": fa_per_day,
        "detection_latency_median_s": latency_median,
        "n_injected": n_injected_total,
        "n_detected": n_detected_total,
        "n_false_alarms": n_false_alarms_total,
        "n_days_evaluated": n_days,
        "per_type_detected_counts": per_type_counts,
    }


__all__ = ["measure"]
