"""整合性検査（C6）：ε の計算。Consistency.epsilon(z_t, s_t, h) -> EpsilonRecord（world_model.md）。

ε_h = E_t[ d( α(f^h(z_t)), F^h(α(z_t)) ) ]（poc_plan.md 2.2）。

簡略化（このセッションの実装範囲、docs/status.md に明記）：
- F^h（業務プロセスの遷移）は、対象個体に対する「今後 h 秒以内に処理予定のイベント」を
  WMS モックからルックアップする仕組みをまだ持たないため、暫定的に「現在の記録上の
  ゾーンが h 秒後も変わらない」という恒等写像として扱う（記録側に予定変更が無ければ
  妥当な既定仮定）。将来、`wms_mock.generate_orders` の割当を使った真の F^h に置き換える。
- ε の3分解（知覚誤り／プロセス不遵守／オントロジー欠落）は、時刻 t における α の
  ゾーン推定が真値と一致するかどうかで「知覚誤り」を切り分け、残りを「プロセス不遵守」に
  割り当てる簡易ヒューリスティックとする（poc_plan.md 2.2 の表に沿う）。「オントロジー
  欠落」は概念発見ループ（着手順9）が稼働するまで常に0として予約する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


def compute_epsilon(
    samples: list[FactSample], horizon_s: float, cfg: DictConfig | None = None
) -> EpsilonRecord:
    """`samples` から ε_h と3分解を計算する。F^h は恒等写像（上記 NOTE）として扱う。"""
    cfg = cfg or load_epsilon_config()
    if not samples:
        return EpsilonRecord(horizon_s=horizon_s, epsilon=0.0, n_samples=0, decomposition={})

    d_values = []
    perception_d = []
    process_d = []
    for s in samples:
        # F^h(α(z_t)) = 恒等写像 -> 現在の知覚ゾーンがそのまま h 秒後の記録上の期待値になる。
        expected_future_zone_idx = s.perceived_zone_idx
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
    "zone_distance",
    "compute_epsilon",
    "effective_horizon",
]
