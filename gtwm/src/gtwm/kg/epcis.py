"""WMS モックが書き出す `events.parquet` の `record` イベントを `record` 出所の Belief に変換する。

ontology.md：「EPCIS の eventTime を有効時間、受信時刻を処理時間とする」。
`events.parquet` では eventTime が `t_true`、受信時刻が `t_obs` に相当する
（`src/gtwm/sim/wms_mock.py` 参照。smoke／realism 無効時は t_true == t_obs）。

`t_true`/`t_obs` はエピソード開始からの相対秒（float）であり、絶対時刻を持たない
（`meta.json` にエピソード開始の壁時計時刻が無いため）。`Belief.valid_from` 等は
datetime 型が必要なため、固定の基準時刻 `EPISODE_EPOCH` からの相対時刻として
変換する。絶対時刻としての意味は持たず、差分・順序比較にのみ使う。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from gtwm.kg.schema import Belief

EPISODE_EPOCH = datetime(2000, 1, 1)

# シミュレーションのゾーン名（sim/env.py の zone_of_x 等が返す文字列）→ gt: 個体ID。
_ZONE_TO_GT = {
    "Dock_In": "gt:Zone_Dock_In",
    "Inspect": "gt:Zone_Inspect",
    "Storage_A": "gt:Zone_Storage_A",
    "Storage_B": "gt:Zone_Storage_B",
    "Pick": "gt:Zone_Pick",
    "Dock_Out": "gt:Zone_Dock_Out",
}


def episode_time_to_datetime(t: float) -> datetime:
    """エピソード内相対秒を `Belief` 用の datetime に変換する（絶対時刻としての意味はない）。"""
    return EPISODE_EPOCH + timedelta(seconds=float(t))


def zone_to_gt_id(zone: str) -> str:
    """ゾーン名を `gt:Zone_<Zone>` 形式の個体IDに変換する（未知ゾーンはそのまま整形）。"""
    return _ZONE_TO_GT.get(zone, f"gt:Zone_{zone}")


def events_to_beliefs(events_df: pd.DataFrame) -> list[Belief]:
    """`events.parquet` の `record` 行を `gt:currentZone` の record-Belief に変換する。

    `anchor` 行（アンカー検出そのもの）は Belief ではなく `gt:Anchor` 個体として扱うため、
    ここでは変換しない（アンカーからの Belief 変換は grounding 層の役割、着手順5）。

    同一個体について新しい currentZone 信念が来た時点で、直前の信念の `valid_to` を
    新しい信念の `valid_from` に設定して有効区間を閉じる。こうしておかないと
    移動履歴のすべての信念が「現在も有効」のまま残り、`snapshot(t)` や SHACL 検証
    （`PalletSingleLocationShape` の maxCount 1）が同一時刻に複数ゾーンを見つけて
    誤検知してしまう。
    """
    beliefs: list[Belief] = []
    open_belief_by_subject: dict[str, Belief] = {}
    record_rows = events_df[events_df["event_type"] == "record"].sort_values("t_true")
    for _, row in record_rows.iterrows():
        zone = row.get("zone")
        gt_id = row.get("entity_gt_id")
        if not isinstance(zone, str) or not zone or gt_id is None or pd.isna(gt_id):
            continue
        subject = str(gt_id)
        valid_from = episode_time_to_datetime(row["t_true"])

        if subject in open_belief_by_subject:
            open_belief_by_subject[subject].valid_to = valid_from

        belief = Belief(
            subject=subject,
            predicate="gt:currentZone",
            object=zone_to_gt_id(zone),
            confidence=1.0,
            source="record",
            valid_from=valid_from,
            transaction_time=episode_time_to_datetime(row["t_obs"]),
        )
        beliefs.append(belief)
        open_belief_by_subject[subject] = belief
    return beliefs


__all__ = ["EPISODE_EPOCH", "episode_time_to_datetime", "events_to_beliefs", "zone_to_gt_id"]
