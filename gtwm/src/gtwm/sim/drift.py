"""業務手順変更（ドリフト）の注入（poc_plan.md 6.2 EXP-04「検品位置変更、ピッキング順序変更」）。

スコープの明記：この sim の物理挙動（作業者・AGV の経路）は固定のスプライン/ウェイポイント
巡回で、`wms_mock.generate_orders()` の割当は現状どこからも呼ばれておらず物理挙動に
影響しない（`generate.py` を参照）。物理経路生成そのものを作り直すのはこのセッションの
予算を超えるため、ドリフトは「業務記録（EPCIS記録相当）の期待プロセス定義が変わった」
という記録レベルの変換として実装する：
- 検品位置変更：inspecting の記録に付与される `zone` を別ゾーンに書き換える
  （現物は同じ場所で検品されているが、業務側の記録上の「期待される検品場所」が
  変わったことを表す）。
- ピッキング順序変更：picking 記録の時刻順と個体の対応を反転させる（どのタイミングで
  どの個体がピッキングされたと記録されるかの順序が変わったことを表す）。

いずれも `apply_injections` と同様に events_df を変換するだけで、盲検境界
（`data/injections`）とは無関係（ドリフトは「注入台帳」に載る乖離ではなく、業務側が
合意して実施した正当な手順変更なので、`eval/scoring.py` 経由の盲検読み出しの対象外）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from gtwm.sim.wms_mock import CBV_BIZSTEP


@dataclass
class DriftConfig:
    inspection_position_change: bool = False
    inspection_new_zone: str = "Storage_A"
    picking_order_change: bool = False


def apply_drift(events_df: pd.DataFrame, cfg: DriftConfig) -> pd.DataFrame:
    if events_df.empty or (not cfg.inspection_position_change and not cfg.picking_order_change):
        return events_df

    df = events_df.copy().reset_index(drop=True)

    if cfg.inspection_position_change:
        mask = df["biz_step"] == CBV_BIZSTEP["inspecting"]
        df.loc[mask, "zone"] = cfg.inspection_new_zone

    if cfg.picking_order_change:
        mask = (df["event_type"] == "record") & (df["biz_step"] == CBV_BIZSTEP["picking"])
        picking_idx = df.index[mask].tolist()
        if len(picking_idx) >= 2:
            # 個体・entity_gt_id の対応を時刻順で反転させ、記録上の「どの個体が
            # どのタイミングでピッキングされたか」の順序を変える（t_true/t_obs は
            # そのまま、entity/entity_gt_id だけ入れ替える）。
            entities = df.loc[picking_idx, "entity"].tolist()
            gt_ids = df.loc[picking_idx, "entity_gt_id"].tolist()
            df.loc[picking_idx, "entity"] = list(reversed(entities))
            df.loc[picking_idx, "entity_gt_id"] = list(reversed(gt_ids))

    return df


__all__ = ["DriftConfig", "apply_drift"]
