"""記録（`events.parquet` の `record` 型イベント）と物理（`poses.parquet` の真値）の
突き合わせによる乖離検知（poc_plan.md 2.2 の3分類のうち「(2) プロセス不遵守」）。

背景（docs/status.md に明記）：`ground_run.py` の既存チェック（WMロールアウトが予測する
未来ゾーンの不安定性）は物理側の内部一貫性しか見ておらず、業務記録を一切参照しない。
そのため `sim/wms_mock.apply_injections` が注入する5型の異常（record 側の改変）を
原理的に検知できなかった（EXP-05 の初回実行で detection_rate=0.0 として発覚）。
このモジュールは record と物理を直接突き合わせて `DiscrepancyEntry` 候補を作ることで
その欠落を埋める。

検知できる型（3/5）：
- late_registration: `t_obs - t_true` が閾値を超える記録。
- wrong_slot: 記録の `zone` と、同時刻の物理ゾーン（`poses.parquet`）が食い違う。
- ghost_stock: 対応するアンカー（`event_type=="anchor"`）が近傍に存在しない記録
  （このシミュレーションでは全ての正当な記録がアンカーから導出されるため、
  アンカーの裏付けが無い記録は捏造以外にありえない）。

検知できない型（2/5、既知の限界）：
- wrong_scan: 個体の外観照合（再識別）が無ければ「記録された個体が物理的に別人だった」
  ことを区別できない。このセッションでは未実装（identity.py は座標ベースの割当のみ）。
- unscanned_move: 物理ゾーン遷移のうち record が伴わないものを全て「見逃されたスキャン」
  とみなすと、そもそもスキャンが起きない正常な物理移動（ワーカーが1秒以上滞在しなかった
  等）まで誤報になり、平常時の誤報率が跳ね上がる（試作の結果、平常データでの誤報が
  多発することを確認したため見送った）。より正確な「期待されるスキャンのタイミング」の
  モデルが必要。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class RecordDiscrepancyCandidate:
    entity_gt_id: str
    discrepancy_type: str
    t_true: float
    t_obs: float  # 記録が実際に登録された時刻。検知はこれより前には起こり得ない
    physical_value: str
    record_value: str


def detect_record_discrepancies(
    events_df: pd.DataFrame,
    poses_df: pd.DataFrame,
    late_registration_threshold_s: float = 10.0,
    ghost_window_s: float = 5.0,
) -> list[RecordDiscrepancyCandidate]:
    """`events_df`（`events.parquet`）と `poses_df`（`poses.parquet`）を突き合わせる。

    どちらも `gtwm ground run` が既に読んでいる、注入後の業務記録・物理真値そのもの
    （盲検境界の対象は「どのエピソードのどの行が注入されたか」を記録した答え合わせ用の
    台帳だけであり、注入済みの記録そのものを検知側が読むのは仕様通り。乖離を見つけ出す
    のがこの関数の仕事）。
    """
    candidates: list[RecordDiscrepancyCandidate] = []
    records = events_df[events_df["event_type"] == "record"]
    anchors = events_df[events_df["event_type"] == "anchor"]

    for _, row in records.iterrows():
        entity_gt_id = row.get("entity_gt_id")
        if not entity_gt_id:
            continue
        t_true = float(row["t_true"])
        t_obs = float(row["t_obs"])

        delay = t_obs - t_true
        if delay > late_registration_threshold_s:
            candidates.append(
                RecordDiscrepancyCandidate(
                    entity_gt_id=str(entity_gt_id),
                    discrepancy_type="late_registration",
                    t_true=t_true,
                    t_obs=t_obs,
                    physical_value=f"t_true={t_true:.2f}",
                    record_value=f"delay={delay:.2f}s",
                )
            )

        if pd.notna(row.get("zone")):
            entity_poses = poses_df[poses_df["entity_gt_id"] == entity_gt_id]
            if not entity_poses.empty:
                nearest_idx = (entity_poses["t"] - t_true).abs().idxmin()
                physical_zone = str(entity_poses.loc[nearest_idx, "zone"])
                recorded_zone = str(row["zone"])
                if physical_zone != recorded_zone:
                    candidates.append(
                        RecordDiscrepancyCandidate(
                            entity_gt_id=str(entity_gt_id),
                            discrepancy_type="wrong_slot",
                            t_true=t_true,
                            t_obs=t_obs,
                            physical_value=physical_zone,
                            record_value=recorded_zone,
                        )
                    )

        nearby_anchors = anchors[
            (anchors["entity_gt_id"] == entity_gt_id)
            & (anchors["t_true"].sub(t_true).abs() <= ghost_window_s)
        ]
        if nearby_anchors.empty:
            candidates.append(
                RecordDiscrepancyCandidate(
                    entity_gt_id=str(entity_gt_id),
                    discrepancy_type="ghost_stock",
                    t_true=t_true,
                    t_obs=t_obs,
                    physical_value="no_anchor_nearby",
                    record_value=str(row.get("biz_step")),
                )
            )

    return candidates


__all__ = ["RecordDiscrepancyCandidate", "detect_record_discrepancies"]
