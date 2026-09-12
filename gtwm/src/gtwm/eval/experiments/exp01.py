"""EXP-01 アンカー接地の基礎精度（H1、poc_plan.md 6.2）の測定ロジック。

手順（P0部分）：シミュレーション真値により α を学習し、真値との照合で上限性能を
把握する（`gtwm.grounding.train_probes.train_probes` を再利用）。出力：事実種別
ごとのF1（ここではゾーン=位置事実のマクロF1と型精度）、較正曲線とECE。

P1（実サイトの2週間分データでの層別評価：遮蔽率・照明・混雑度）は本実行時に
拡張する（config の `stratify` を使う想定、smoke では未実装）。
"""

from __future__ import annotations

from typing import Any

from omegaconf import DictConfig, OmegaConf

from gtwm.grounding.train_probes import train_probes
from gtwm.utils.config import load_config


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    probe_cfg = load_config(config.probe_config)
    merged = OmegaConf.merge(probe_cfg, {"seed": seed})
    assert isinstance(merged, DictConfig)
    _, _, _, _, result = train_probes(merged)
    return {
        "position_fact_f1": result.zone_f1_macro,
        "type_accuracy": result.type_accuracy,
        "ece": result.ece_after,
        "ece_before_calibration": result.ece_before,
        "n_train_samples": result.n_train_samples,
        "n_eval_samples": result.n_eval_samples,
        "duration_s": result.duration_s,
    }


__all__ = ["measure"]
