"""P1 相当の現実らしさ（`.claude/rules/sim.md`「時間とアンカー」「WMS モックと EPCIS」、
CLAUDE.md「絶対条件」）。

2つの独立した軸を分ける：
1. **一般ノイズ**（`RealismConfig`）：アンカー時刻ジッタ、欠落率、記録遅延・欠落・誤登録率。
   全アンカー・全記録に無差別にかかる「センサ・業務プロセスの雑さ」。真値 `t_true` は
   常に別に保持し、`t_obs` だけを乱す（sim.md「時間とアンカー」）。
2. **注入**（`sim/wms_mock.InjectionConfig`）：検知側の性能を測るための、狙った少数の
   異常イベント（乖離台帳の元）。`injection_seed` はこのモジュールの一般ノイズの
   乱数とも実験 seed とも独立（盲検、`.claude/rules/experiments.md`）。

このモジュールは `data/injections` を書き出す側であり、読む側ではない（盲検境界は
`eval/scoring.py` が読む側、`grounding/`・`wm/` は読んではならない：`test_blindness.py`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from gtwm.sim.env import ZONE_NAMES
from gtwm.sim.wms_mock import InjectionConfig
from gtwm.utils.paths import repo_root


@dataclass
class RealismConfig:
    """`configs/realism/*.yaml` の1対1対応（コードに数値を書かない）。"""

    anchor_jitter_s_min: float = 0.0
    anchor_jitter_s_max: float = 0.0
    anchor_miss_rate: float = 0.0
    record_delay_s_min: float = 0.0
    record_delay_s_max: float = 0.0
    record_loss_rate: float = 0.0
    record_misregistration_rate: float = 0.0
    # 遮蔽の追加（棚裏通過・積み重ね・作業者による隠蔽の頻度）。session08時点では、
    # シーン形状（MJCFのスタッキング比率・作業者経路）を作り直すのはスコープ外とし、
    # 「遮蔽が増える状況ではアンカーも見えにくくなる」を近似する形で
    # anchor_miss_rate に対する追加寄与として実装する（docs/status.md に明記）。
    occlusion_extra_miss_boost: float = 0.0
    injections: InjectionConfig = field(default_factory=InjectionConfig)


def load_realism_config(path: str | Path) -> RealismConfig:
    full_path = repo_root() / path if not Path(path).is_absolute() else Path(path)
    raw = yaml.safe_load(full_path.read_text(encoding="utf-8")) or {}
    injections_raw = dict(raw.pop("injections", {}) or {})
    delay_raw = raw.pop("injection_late_registration_delay_s", None)
    if delay_raw:
        injections_raw["late_registration_delay_s_min"] = float(delay_raw.get("min", 5.0))
        injections_raw["late_registration_delay_s_max"] = float(delay_raw.get("max", 30.0))
    return RealismConfig(**raw, injections=InjectionConfig(**injections_raw))


def apply_observation_realism(
    events_df: pd.DataFrame, cfg: RealismConfig, seed: int
) -> pd.DataFrame:
    """一般ノイズ（ジッタ・欠落・遅延・誤登録）を適用した events_df を返す。

    `t_true` は変更しない。`t_obs` のみジッタ・遅延を受ける。全て no-op
    （全パラメータ0）なら入力をそのまま返す（smoke との後方互換）。
    """
    if events_df.empty:
        return events_df
    all_zero = (
        cfg.anchor_jitter_s_max == 0.0
        and cfg.anchor_miss_rate == 0.0
        and cfg.record_delay_s_max == 0.0
        and cfg.record_loss_rate == 0.0
        and cfg.record_misregistration_rate == 0.0
        and cfg.occlusion_extra_miss_boost == 0.0
    )
    if all_zero:
        return events_df

    rng = np.random.default_rng(seed)
    df = events_df.copy().reset_index(drop=True)
    is_anchor = df["event_type"] == "anchor"

    # --- アンカー：時刻ジッタ + 欠落（遮蔽増加ぶんを miss_rate に加算して近似） ---
    anchor_idx = df.index[is_anchor]
    if len(anchor_idx) > 0 and cfg.anchor_jitter_s_max > 0.0:
        signs = rng.choice([-1.0, 1.0], size=len(anchor_idx))
        magnitudes = rng.uniform(cfg.anchor_jitter_s_min, cfg.anchor_jitter_s_max, len(anchor_idx))
        df.loc[anchor_idx, "t_obs"] = df.loc[anchor_idx, "t_true"] + signs * magnitudes

    effective_miss_rate = min(1.0, cfg.anchor_miss_rate + cfg.occlusion_extra_miss_boost)
    if len(anchor_idx) > 0 and effective_miss_rate > 0.0:
        drop_mask = rng.uniform(size=len(anchor_idx)) < effective_miss_rate
        df = df.drop(index=anchor_idx[drop_mask])

    # --- 記録：登録遅延 + 欠落 + 誤登録 ---
    record_idx = df.index[df["event_type"] == "record"]
    if len(record_idx) > 0 and cfg.record_delay_s_max > 0.0:
        delays = rng.uniform(cfg.record_delay_s_min, cfg.record_delay_s_max, len(record_idx))
        df.loc[record_idx, "t_obs"] = df.loc[record_idx, "t_true"] + delays

    record_idx = df.index[df["event_type"] == "record"]
    if len(record_idx) > 0 and cfg.record_loss_rate > 0.0:
        drop_mask = rng.uniform(size=len(record_idx)) < cfg.record_loss_rate
        df = df.drop(index=record_idx[drop_mask])

    record_idx = df.index[df["event_type"] == "record"]
    if len(record_idx) > 0 and cfg.record_misregistration_rate > 0.0:
        mis_mask = rng.uniform(size=len(record_idx)) < cfg.record_misregistration_rate
        mis_idx = record_idx[mis_mask]
        for i in mis_idx:
            if pd.notna(df.at[i, "zone"]):
                df.at[i, "zone"] = str(rng.choice(ZONE_NAMES))

    return df.sort_values("t_true").reset_index(drop=True)


__all__ = ["RealismConfig", "load_realism_config", "apply_observation_realism"]
