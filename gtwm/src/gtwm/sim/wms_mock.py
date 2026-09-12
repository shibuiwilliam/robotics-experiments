"""WMS モック：オーダー生成、期待プロセス F、EPCIS 2.0 JSON-LD 発行。

アンカー検出結果（真値）を「業務記録」に変換する。乖離注入（遅延・欠落・誤登録）は
既定で全て 0（無効）とし、実装は着手順8で行う。検知側（grounding/eval）はこのモジュールの
`injections` を import してはならない（盲検、`tests/unit/test_blindness.py` で検査）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields

import pandas as pd

from gtwm.sim.env import zone_of_x

CBV_BIZSTEP = {
    "receiving": "urn:epcglobal:cbv:bizstep:receiving",
    "inspecting": "urn:epcglobal:cbv:bizstep:inspecting",
    "storing": "urn:epcglobal:cbv:bizstep:storing",
    "picking": "urn:epcglobal:cbv:bizstep:picking",
    "shipping": "urn:epcglobal:cbv:bizstep:shipping",
}
CBV_DISPOSITION_ACTIVE = "urn:epcglobal:cbv:disp:active"

# アンカー種別 → 期待プロセス F の bizStep への対応表。
# gate:1（入荷ゲート）→ receiving、gate:2（出荷ゲート）→ shipping、
# scale → inspecting、scan は現在のゾーンで storing / picking を判定する。
_GATE_BIZSTEP = {"gate:1": "receiving", "gate:2": "shipping"}


@dataclass
class InjectionConfig:
    """乖離注入の件数設定。既定は全て0（無効＝smoke）。着手順8で実装する。"""

    unscanned_move: int = 0
    wrong_slot: int = 0
    wrong_scan: int = 0
    late_registration: int = 0
    ghost_stock: int = 0


def apply_injections(
    events_df: pd.DataFrame, cfg: InjectionConfig, injection_seed: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """乖離注入を events_df に適用し、(注入後の events_df, 注入台帳) を返す。

    実験の seed とは別系統の `injection_seed` を使う。全件数が0のときは no-op。
    """
    if all(getattr(cfg, f.name) == 0 for f in fields(cfg)):
        ledger = pd.DataFrame(
            columns=["episode_id", "injection_type", "entity", "t_true", "detail"]
        )
        return events_df, ledger
    raise NotImplementedError("乖離注入（unscanned_move 等）は着手順8で実装する")


def generate_conveyor_schedule(duration_s: float, seed: int) -> list[tuple[float, bool]]:
    """コンベアの ON/OFF スケジュール（PLC アンカーの元）。smoke では単純な周期にする。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    schedule: list[tuple[float, bool]] = [(0.0, True)]
    t = 0.0
    state = True
    while t < duration_s:
        t += float(rng.uniform(8.0, 15.0))
        if t < duration_s:
            state = not state
            schedule.append((t, state))
    return schedule


def generate_orders(pallet_names: list[str], n_inbound: int, n_outbound: int, seed: int) -> dict:
    """オーダー生成（簡易）：どのパレットが入荷/出荷対象かを割り当てる。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    n_inbound = min(n_inbound, len(pallet_names))
    n_outbound = min(n_outbound, len(pallet_names))
    inbound = list(rng.choice(pallet_names, size=n_inbound, replace=False))
    remaining = [p for p in pallet_names if p not in inbound]
    outbound = list(rng.choice(remaining, size=min(n_outbound, len(remaining)), replace=False))
    return {"inbound_pallets": inbound, "outbound_pallets": outbound}


def derive_epcis_events(
    anchor_df: pd.DataFrame, ontology_id_fn: Callable[[str], str | None]
) -> pd.DataFrame:
    """アンカー検出結果から EPCIS 風の「記録」イベントを導出する（smoke: 遅延なし＝即時記録）。

    対応：gate:1 crossing → receiving、gate:2 crossing → shipping、
    scale → inspecting、scan かつ現在ゾーンが Storage_A/B → storing、Pick → picking。
    """
    records = []
    for _, row in anchor_df.iterrows():
        biz_step = None
        if row["anchor_type"] == "rfid_gate":
            biz_step = _GATE_BIZSTEP.get(row["detail_gate"])
        elif row["anchor_type"] == "scale":
            biz_step = "inspecting"
        elif row["anchor_type"] == "scan":
            biz_step = (
                None  # ゾーン依存の判定は呼び出し側で位置情報が必要なため generate.py 側で付与
            )
        if biz_step is None:
            continue
        gt_id = ontology_id_fn(row["entity"])
        records.append(
            {
                "event_type": "record",
                "subject": row["entity"],
                "subject_gt_id": gt_id,
                "biz_step": CBV_BIZSTEP[biz_step],
                "disposition": CBV_DISPOSITION_ACTIVE,
                "t_true": row["t_true"],
                "t_obs": row["t_obs"],
            }
        )
    return pd.DataFrame(
        records,
        columns=[
            "event_type",
            "subject",
            "subject_gt_id",
            "biz_step",
            "disposition",
            "t_true",
            "t_obs",
        ],
    )


def derive_scan_biz_step(zone: str) -> str | None:
    if zone in ("Storage_A", "Storage_B"):
        return "storing"
    if zone == "Pick":
        return "picking"
    return None


__all__ = [
    "CBV_BIZSTEP",
    "CBV_DISPOSITION_ACTIVE",
    "InjectionConfig",
    "apply_injections",
    "generate_conveyor_schedule",
    "generate_orders",
    "derive_epcis_events",
    "derive_scan_biz_step",
    "zone_of_x",
]
