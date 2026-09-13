"""整合性検査（C6）：ε の計算。Consistency.epsilon(z_t, s_t, h) -> EpsilonRecord（world_model.md）。

ε_h = E_t[ d( α(f^h(z_t)), F^h(α(z_t)) ) ]（poc_plan.md 2.2）。

F^h（業務プロセスの遷移）は `events.parquet` の `record` 型イベント（EPCIS由来の業務記録、
`entity_gt_id`/`zone`/`t_obs` 列を持つ）を実際に参照する（`lookup_expected_future_zone_idx`）：
時刻 t で個体 e について、記録上「今後 h 秒以内（t_obs が (t, t+h] に入る）」に最も近い
将来の記録があればそのゾーンを F^h(α(z_t)) とする。無ければ「直近の記録ゾーン（t_obs<=t
の中で最新）」を h 秒後も変わらない前提で使う。記録が一件も無ければ（エピソード冒頭で
その個体がまだ一度も記録されていない場合）期待値を定義できないため、そのサンプルは
ε の計算対象から除外する（`FactSample.expected_future_zone_idx=None`）。

【修正履歴・要ADR不要（poc_plan.md 2.2 の定義通りに実装を直すバグ修正）】
旧実装は F^h を「現在の知覚ゾーン（WMの α 自身の出力）が h 秒後も変わらない」という
恒等写像として扱っており、`events.parquet`（業務記録）を一切参照していなかった。この
ため ε は realism/注入設定に構造的に無反応だった（着手順8で realism 有無の ε_60s が
完全に同一値になることを確認して発覚。docs/status.md「全体まとめ」ギャップ#1）。

ε の3分解（知覚誤り／プロセス不遵守／オントロジー欠落）は、時刻 t における α の
ゾーン推定が真値と一致するかどうかで「知覚誤り」を切り分け、残りを「プロセス不遵守」に
割り当てる簡易ヒューリスティックとする（poc_plan.md 2.2 の表に沿う）。「オントロジー
欠落」は概念発見ループ（着手順9）が稼働するまで常に0として予約する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from omegaconf import DictConfig

from gtwm.utils.config import load_config

EPSILON_CONFIG_PATH = "configs/grounding/epsilon.yaml"


@dataclass
class EpsilonRecord:
    """契約：Consistency.epsilon(z_t, s_t, h) -> EpsilonRecord。"""

    horizon_s: float
    epsilon: float
    n_samples: int
    decomposition: dict[str, float] = field(default_factory=dict)  # perception/process/ontology


def load_epsilon_config(path: str = EPSILON_CONFIG_PATH) -> DictConfig:
    return load_config(path)


def zone_distance(pred_zone_idx: int, ref_zone_idx: int, cfg: DictConfig) -> float:
    """位置事実の不一致率（付録A）：ゾーン一致=0、隣接ゾーン=0.5、それ以外=1。"""
    if pred_zone_idx == ref_zone_idx:
        return 0.0
    if abs(pred_zone_idx - ref_zone_idx) == 1:
        return float(cfg.d_weights.zone_adjacent_penalty)
    return 1.0


@dataclass
class FactSample:
    """1時刻・1個体ぶんの、ε 計算に必要な最小情報。"""

    entity_gt_id: str
    t: float
    perceived_zone_idx: int  # α(z_t) の現在時刻ゾーン推定
    truth_zone_idx: int  # 同時刻の真値ゾーン（知覚誤り切り分け用）
    future_zone_idx: int  # α(f^h(z_t))：h ステップ先の WM ロールアウト予測ゾーン
    # F^h(α(z_t))：業務記録（events.parquet）から求めた「h秒後に記録上期待されるゾーン」。
    # 個体がまだ一度も記録されていない場合は None（このサンプルは ε 計算から除外する）。
    expected_future_zone_idx: int | None = None


def lookup_expected_future_zone_idx(
    events_df: pd.DataFrame,
    entity_gt_id: str,
    t_s: float,
    horizon_s: float,
    zone_names: list[str],
) -> int | None:
    """F^h(α(z_t)) の実装：`events.parquet` の `record` イベントから、時刻 t の個体
    `entity_gt_id` について「h秒後に業務記録上期待されるゾーン」を求める。

    優先順位：
    1. (t, t+h] の間に記録される予定のイベント（t_obs 基準）があれば、その中で最も
       早い（最も近い将来の）記録のゾーンを使う（記録上、その時刻までにゾーンが変わる
       予定があるということ）。
    2. 無ければ、t 以前の直近の記録（t_obs<=t の中で最新）のゾーンが h 秒後も変わらない
       という既定仮定を使う。
    3. 記録が一件も無ければ（エピソード冒頭でまだ記録されていない個体）None を返す
       （呼び出し側でサンプルごと除外する）。

    `t_true` ではなく `t_obs`（記録が実際に登録された時刻）を基準にする：業務プロセスが
    「知っている」のはいつ記録が登録されたか（t_obs）であり、真の物理時刻（t_true）を
    業務側が先取りできると仮定するのは F^h の定義（記録側の遷移）に反するため。
    """
    records = events_df[
        (events_df["event_type"] == "record")
        & (events_df["entity_gt_id"] == entity_gt_id)
        & events_df["zone"].notna()
    ]
    if records.empty:
        return None

    future = records[(records["t_obs"] > t_s) & (records["t_obs"] <= t_s + horizon_s)]
    if not future.empty:
        nearest = future.loc[future["t_obs"].idxmin()]
        zone = str(nearest["zone"])
        return zone_names.index(zone) if zone in zone_names else None

    past = records[records["t_obs"] <= t_s]
    if not past.empty:
        latest = past.loc[past["t_obs"].idxmax()]
        zone = str(latest["zone"])
        return zone_names.index(zone) if zone in zone_names else None

    return None


def compute_epsilon(
    samples: list[FactSample], horizon_s: float, cfg: DictConfig | None = None
) -> EpsilonRecord:
    """`samples` から ε_h と3分解を計算する。

    F^h(α(z_t)) は各サンプルの `expected_future_zone_idx`（`lookup_expected_future_zone_idx`
    で業務記録から求めた値）を使う。`expected_future_zone_idx is None`（対象個体がまだ
    一度も記録されていない）のサンプルは期待値を定義できないため計算対象から除外する。
    """
    cfg = cfg or load_epsilon_config()
    usable = [s for s in samples if s.expected_future_zone_idx is not None]
    if not usable:
        return EpsilonRecord(horizon_s=horizon_s, epsilon=0.0, n_samples=0, decomposition={})

    d_values = []
    perception_d = []
    process_d = []
    for s in usable:
        expected_future_zone_idx = s.expected_future_zone_idx
        assert expected_future_zone_idx is not None  # usable でフィルタ済み
        d = zone_distance(s.future_zone_idx, expected_future_zone_idx, cfg) * cfg.d_weights.position
        d_values.append(d)

        perception_ok = s.perceived_zone_idx == s.truth_zone_idx
        if not perception_ok:
            perception_d.append(d)
        else:
            process_d.append(d)

    epsilon = sum(d_values) / len(d_values)
    n = len(d_values)
    decomposition = {
        "perception": sum(perception_d) / n,
        "process_deviation": sum(process_d) / n,
        "ontology_gap": 0.0,  # 概念発見ループ（着手順9）が稼働するまで予約
    }
    return EpsilonRecord(
        horizon_s=horizon_s, epsilon=epsilon, n_samples=n, decomposition=decomposition
    )


def effective_horizon(records: list[EpsilonRecord], tau: float) -> float | None:
    """有効ホライズン H*（付録A）：記号空間誤差が tau を超えない最大の h。"""
    ok_horizons = [
        r.horizon_s for r in sorted(records, key=lambda r: r.horizon_s) if r.epsilon <= tau
    ]
    return max(ok_horizons) if ok_horizons else None


__all__ = [
    "EPSILON_CONFIG_PATH",
    "EpsilonRecord",
    "FactSample",
    "load_epsilon_config",
    "lookup_expected_future_zone_idx",
    "zone_distance",
    "compute_epsilon",
    "effective_horizon",
]
