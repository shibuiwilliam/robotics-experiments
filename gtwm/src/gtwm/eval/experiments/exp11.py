"""EXP-11 非機能（N1、poc_plan.md 6.2）の測定ロジック。

手順（poc_plan.md 6.2 原文）：「観測から信念KG更新までのE2E遅延を継続計測。稼働率、
障害時の復旧時間、月額計算コストを記録」。出力：遅延分布（p50/p95）、稼働率、
コスト試算（区画あたり）。

このセッション（08）の簡略化（docs/status.md に明記）：
- E2E遅延：`gtwm ground run`（`grounding/ground_run.run_ground`）の各サンプルフレームに
  ついて「フレーム読込開始 → `store.add_beliefs()` 完了」の経過時間を
  `GroundRunResult.frame_latencies_s` として計測する。現在の実装はバッチ処理
  （全フレームを処理してから一度だけコミット）のため、早いフレームほど「バッチの
  残り処理＋最終コミット」を待つ分だけ大きい値になる。ストリーミング化すれば
  個々のフレームはもっと速く確定できるはずで、ここで得られる値は実際のP1本番より
  悲観的な上限（このバッチ実装での値）だと解釈する。
- 稼働率：実際の稼働率SLA計測には長期間デプロイされたサービスが必要（本 PoC は
  CLAUDE.md「絶対条件」により全フェーズをシミュレーションで行うため対象外）。
  代わりに `config.n_trials` 回 `gtwm ground run` 相当のパイプラインを実行し、
  例外なく完走した割合を「稼働率」の代理指標として報告する（あくまで
  「処理が落ちずに完走したか」であり、稼働率SLAの定義とは異なることを明記する）。
- コスト試算：実測した生成・処理時間（本セッションの実測値）から、1区画を
  常時稼働で監視する場合の概算月間計算コストをナラティブに算出する
  （`experiments/criteria.yaml` の `monthly_cost_per_zone` は `report_only`）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from omegaconf import DictConfig

from gtwm.grounding.ground_run import run_ground
from gtwm.sim.generate import generate_episode

# 実測値（このセッションで計測、docs/status.md 参照）：
# - 30秒エピソード生成: 約7〜15秒（session02実測、smoke構成）。
# - 5分（300秒）エピソード生成: 約75秒（session08実測、p1_train/p1_eval）。
# 生成速度の実時間比 ≈ 300秒 / 75秒 ≈ 4倍速（0.25倍の実時間）。
_MEASURED_300S_EPISODE_GEN_S = 75.0
_HOURS_PER_MONTH = 24.0 * 30.0
# AWS g5.xlarge（A10G、smoke程度のGPU処理に近い規模感の参考値、2026年時点の目安）。
# 正確な単価はクラウド事業者・リージョン・割引で変動するため、あくまで概算の参考値。
_ASSUMED_GPU_HOURLY_USD = 1.0


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    n_trials = int(config.get("n_trials", 3))
    duration_s = float(config.get("duration_s", 30.0))
    probe_config = str(config.get("probe_config", "configs/grounding/probe_train_smoke.yaml"))
    set_name = f"exp11_seed{seed}"

    all_latencies: list[float] = []
    n_success = 0
    for i in range(n_trials):
        ep_seed = seed * 10_000 + i
        try:
            generate_episode(set_name, i, duration_s, ep_seed)
            episode_id = f"ep_{i:04d}_seed{ep_seed}"
            result = run_ground(episode_id, set_name, probe_config=probe_config)
            all_latencies.extend(result.frame_latencies_s)
            n_success += 1
        except Exception:  # noqa: BLE001 - 稼働率の代理指標として「例外なく完走したか」を見る
            continue

    p50 = float(np.percentile(all_latencies, 50)) if all_latencies else float("nan")
    p95 = float(np.percentile(all_latencies, 95)) if all_latencies else float("nan")
    availability_proxy = n_success / n_trials if n_trials > 0 else float("nan")

    # コスト試算（ナラティブ、report_only）：1区画を24時間365日監視すると仮定し、
    # 5分(300秒)分の観測を処理するのに実測75秒かかる（0.25倍の実時間）ことから、
    # 1ヶ月に必要な計算時間 = (1ヶ月の秒数 / 300) * 75秒 の按分で概算する。
    seconds_per_month = _HOURS_PER_MONTH * 3600.0
    compute_hours_per_month = (seconds_per_month / 300.0) * _MEASURED_300S_EPISODE_GEN_S / 3600.0
    monthly_cost_usd = compute_hours_per_month * _ASSUMED_GPU_HOURLY_USD

    return {
        "e2e_latency_p50_s": p50,
        "e2e_latency_p95_s": p95,
        "availability": availability_proxy,
        "monthly_cost_per_zone": monthly_cost_usd,
        "n_trials": n_trials,
        "n_success": n_success,
        "n_frame_samples": len(all_latencies),
    }


__all__ = ["measure"]
