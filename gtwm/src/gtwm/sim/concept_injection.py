"""EXP-08（概念発見、H7）向けの「オントロジー未登録の新概念」注入・タグ付け。

poc_plan.md 6.2 EXP-08「新しい荷姿・工程・置き場運用を1種ずつ、オントロジーには
未登録のまま導入」に対応する3種を実装する。検知側（`grounding/concept_discovery.py`）
はこのモジュールを import してはならない（盲検、`data/injections/` 経由でのみ
`eval/scoring.py` が読む。session08 の乖離注入と同じ境界・同じ台帳スキーマを共有する）。

簡略化（smoke規模、docs/status.md 参照）：
- oversized_cargo_proxy：新しいMJCF形状を追加する代わりに、既存倉庫に元々存在する
  「3段積みの上段ケース」（`sim/assets/generate_mjcf.py` の `gen_objects()` が
  case_id 1〜20 の偶数番を上段として生成）を「未登録の荷姿」の代理として扱う。
  これは物理的に本当に他と異なる（積み重ねによる床面投影の違い）実在パターンであり、
  捏造ではない。
- reinspecting_step：`wms_mock.py` に無い新しいCBV bizStep（"reinspecting"）を、
  検品記録の後に一定確率で追加発行する、本物の新規イベント生成（`sim/wms_mock.py`
  と同じ実装パターン）。
- staging_overflow：物理配置や記録を書き換えるのではなく、既に生成された
  `poses_df` の中から「本来滞留を想定しないゾーンに長時間留まっている」個体を
  事後的に検出してタグ付けする（session08 の drift.py と同じ「記録レベルの簡略化」
  の精神：物理ディスパッチの変更は行わない）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CBV_BIZSTEP_REINSPECTING = "urn:epcglobal:cbv:bizstep:reinspecting"  # gt-core.ttl/CBVに未登録
_LEDGER_COLUMNS = ["episode_id", "injection_type", "entity", "t_true", "detail"]

# gen_objects() の規則：case_id 1..2*n_stacked_pairs のうち偶数番が上段（積み重ね）。
_N_STACKED_PAIRS = 10


@dataclass
class ConceptInjectionConfig:
    enable_oversized_cargo_tagging: bool = False
    enable_reinspection: bool = False
    reinspection_prob: float = 0.4
    reinspection_delay_s_min: float = 3.0
    reinspection_delay_s_max: float = 8.0
    enable_staging_overflow_tagging: bool = False
    staging_zone: str = "Dock_Out"
    staging_dwell_fraction_threshold: float = 0.3


def _tag_oversized_cargo(poses_df: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    case_rows = poses_df[poses_df["entity"].str.startswith("case:")]
    for entity, group in case_rows.groupby("entity"):
        case_num = int(str(entity).split(":")[1])
        is_top_tier_stacked = case_num <= 2 * _N_STACKED_PAIRS and case_num % 2 == 0
        if not is_top_tier_stacked:
            continue
        t0 = float(group["t"].min())
        rows.append(
            {
                "episode_id": "",
                "injection_type": "oversized_cargo_proxy",
                "entity": str(group["entity_gt_id"].iloc[0]),
                "t_true": t0,
                "detail": '{"reason": "stacked_top_tier_case_unusual_footprint"}',
            }
        )
    return rows


def _apply_reinspection(
    events_df: pd.DataFrame, cfg: ConceptInjectionConfig, rng: np.random.Generator
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """検品記録の後段に、未登録の "reinspecting" bizStep を確率的に追加発行する。

    実機確認済みの制約：`sim/sensors/anchors.py` の `SCALE_POS` は固定座標であり、
    現状の物体配置ロジック（`sim/assets/generate_mjcf.py` のランダムグリッド配置）
    では、生成された48エピソード全て（p0/p1/p2 smoke含む）を確認しても
    "inspecting" bizStep が一度も発生していない（秤アンカーが実質到達不能）。
    これは本モジュール固有の問題ではなく、既存のシミュレーション全体の制約
    （session02由来、docs/status.md に追記）。そのため "inspecting" ではなく、
    確実に発生する "storing" 記録を新規プロセスステップの前提イベントとして使う。
    """
    df = events_df.copy().reset_index(drop=True)
    inspecting_mask = (df["event_type"] == "record") & (
        df["biz_step"] == "urn:epcglobal:cbv:bizstep:storing"
    )
    candidates = df.index[inspecting_mask].tolist()
    rows: list[dict[str, object]] = []
    new_records = []
    for i in candidates:
        if rng.random() >= cfg.reinspection_prob:
            continue
        delay = float(rng.uniform(cfg.reinspection_delay_s_min, cfg.reinspection_delay_s_max))
        base = df.loc[i]
        t_true = float(base["t_true"]) + delay
        new_record = dict(base)
        new_record["biz_step"] = CBV_BIZSTEP_REINSPECTING
        new_record["t_true"] = t_true
        new_record["t_obs"] = t_true
        new_records.append(new_record)
        rows.append(
            {
                "episode_id": "",
                "injection_type": "reinspecting_step",
                "entity": str(base["entity_gt_id"]),
                "t_true": t_true,
                "detail": '{"reason": "unregistered_process_step_after_storing"}',
            }
        )
    if new_records:
        df = pd.concat([df, pd.DataFrame(new_records)], ignore_index=True)
        df = df.sort_values("t_true").reset_index(drop=True)
    return df, rows


def _tag_staging_overflow(
    poses_df: pd.DataFrame, cfg: ConceptInjectionConfig
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entity, group in poses_df.groupby("entity"):
        if not (str(entity).startswith("pallet:") or str(entity).startswith("case:")):
            continue
        dwell_fraction = float((group["zone"] == cfg.staging_zone).mean())
        if dwell_fraction < cfg.staging_dwell_fraction_threshold:
            continue
        t0 = float(group.loc[group["zone"] == cfg.staging_zone, "t"].min())
        detail = f'{{"zone": "{cfg.staging_zone}", "dwell_fraction": {dwell_fraction:.3f}}}'
        rows.append(
            {
                "episode_id": "",
                "injection_type": "staging_overflow",
                "entity": str(group["entity_gt_id"].iloc[0]),
                "t_true": t0,
                "detail": detail,
            }
        )
    return rows


def apply_concept_injections(
    events_df: pd.DataFrame,
    poses_df: pd.DataFrame,
    cfg: ConceptInjectionConfig,
    injection_seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """3種の未登録概念注入・タグ付けを適用し、(更新後 events_df, 概念注入台帳) を返す。

    台帳は `sim/wms_mock.py` の乖離注入と同じスキーマ（`_LEDGER_COLUMNS`）を使うため、
    `eval/scoring.py` が同じ読み出しコードで両方を扱える。
    """
    rng = np.random.default_rng(injection_seed)
    rows: list[dict[str, object]] = []

    if cfg.enable_oversized_cargo_tagging:
        rows.extend(_tag_oversized_cargo(poses_df))

    if cfg.enable_reinspection:
        events_df, reinspect_rows = _apply_reinspection(events_df, cfg, rng)
        rows.extend(reinspect_rows)

    if cfg.enable_staging_overflow_tagging:
        rows.extend(_tag_staging_overflow(poses_df, cfg))

    ledger = pd.DataFrame(rows, columns=_LEDGER_COLUMNS)
    return events_df, ledger
