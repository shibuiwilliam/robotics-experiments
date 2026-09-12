"""WMS モック：オーダー生成、期待プロセス F、EPCIS 2.0 JSON-LD 発行。

アンカー検出結果（真値）を「業務記録」に変換する。乖離注入（5型、着手順8で実装）は
既定で全て 0 件（無効＝P0/smoke）。検知側（grounding/eval）はこのモジュールの
`InjectionConfig`/`apply_injections` を import してはならない（盲検、
`tests/unit/test_blindness.py` で検査。読んでよいのは `eval/scoring.py` が書き出し済みの
`data/injections/*.parquet` を読むことだけ）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from gtwm.sim.env import ZONE_NAMES, zone_of_x

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
    """乖離注入の件数設定。既定は全て0（無効＝smoke、P0）。着手順8（P1相当）で実装。"""

    unscanned_move: int = 0
    wrong_slot: int = 0
    wrong_scan: int = 0
    late_registration: int = 0
    ghost_stock: int = 0
    late_registration_delay_s_min: float = 5.0
    late_registration_delay_s_max: float = 30.0


_LEDGER_COLUMNS = ["episode_id", "injection_type", "entity", "t_true", "detail"]


def apply_injections(
    events_df: pd.DataFrame, cfg: InjectionConfig, injection_seed: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """乖離注入を events_df に適用し、(注入後の events_df, 注入台帳) を返す。

    実験の seed（データ生成そのもの）とは別系統の `injection_seed` を使う
    （盲検、`.claude/rules/experiments.md`）。全件数が0のときは no-op。

    5種の注入（poc_plan.md 5.3 / `.claude/rules/sim.md`「WMS モックと EPCIS」）：
    - unscanned_move: 実際には発生したはずの記録イベント（storing/picking）を削除する
      （現物は動いたのにスキャンされなかった）。
    - wrong_slot: 記録の `zone` を別のゾーンに書き換える（誤ったスロットに登録）。
    - wrong_scan: 記録の `entity`/`entity_gt_id` を別個体のものに差し替える
      （誤った個体がスキャンされた）。
    - late_registration: 記録の `t_obs` に大きな遅延を加える（登録の遅れ）。
    - ghost_stock: 物理的な裏付け（アンカー）が無い記録イベントを新規に捏造する
      （台帳上だけ存在する幽霊在庫）。
    """
    _count_fields = (
        "unscanned_move",
        "wrong_slot",
        "wrong_scan",
        "late_registration",
        "ghost_stock",
    )
    if all(getattr(cfg, name) == 0 for name in _count_fields):
        return events_df, pd.DataFrame(columns=_LEDGER_COLUMNS)

    rng = np.random.default_rng(injection_seed)
    df = events_df.copy().reset_index(drop=True)
    ledger_rows: list[dict[str, object]] = []
    episode_span = float(df["t_true"].max()) if not df.empty else 0.0

    def sample(candidates: list[int], n: int) -> list[int]:
        if not candidates or n <= 0:
            return []
        n = min(n, len(candidates))
        chosen = rng.choice(len(candidates), size=n, replace=False)
        return [candidates[i] for i in chosen]

    is_record = df["event_type"] == "record"

    # --- unscanned_move: storing/picking の記録を削除する ---
    move_candidates = df.index[
        is_record & df["biz_step"].isin([CBV_BIZSTEP["storing"], CBV_BIZSTEP["picking"]])
    ].tolist()
    for i in sample(move_candidates, cfg.unscanned_move):
        row = df.loc[i]
        ledger_rows.append(
            {
                "episode_id": "",
                "injection_type": "unscanned_move",
                "entity": row["entity"],
                "t_true": row["t_true"],
                "detail": f'{{"dropped_biz_step": "{row["biz_step"]}"}}',
            }
        )
        df = df.drop(index=i)

    # --- wrong_slot: 記録の zone を別ゾーンに書き換える ---
    df = df.reset_index(drop=True)
    is_record = df["event_type"] == "record"
    slot_candidates = df.index[is_record & df["zone"].notna()].tolist()
    for i in sample(slot_candidates, cfg.wrong_slot):
        true_zone = df.at[i, "zone"]
        wrong_zone = str(rng.choice([z for z in ZONE_NAMES if z != true_zone]))
        df.at[i, "zone"] = wrong_zone
        ledger_rows.append(
            {
                "episode_id": "",
                "injection_type": "wrong_slot",
                "entity": df.at[i, "entity"],
                "t_true": df.at[i, "t_true"],
                "detail": f'{{"true_zone": "{true_zone}", "recorded_zone": "{wrong_zone}"}}',
            }
        )

    # --- wrong_scan: 記録の entity を別個体に差し替える ---
    df = df.reset_index(drop=True)
    is_record = df["event_type"] == "record"
    scan_candidates = df.index[is_record].tolist()
    known_entities = df[["entity", "entity_gt_id"]].dropna().drop_duplicates()
    for i in sample(scan_candidates, cfg.wrong_scan):
        true_entity = df.at[i, "entity"]
        others = known_entities[known_entities["entity"] != true_entity]
        if others.empty:
            continue
        pick = others.sample(n=1, random_state=int(rng.integers(0, 2**31 - 1))).iloc[0]
        recorded_entity = pick["entity"]
        ledger_rows.append(
            {
                "episode_id": "",
                "injection_type": "wrong_scan",
                "entity": true_entity,
                "t_true": df.at[i, "t_true"],
                "detail": (
                    f'{{"true_entity": "{true_entity}", "recorded_entity": "{recorded_entity}"}}'
                ),
            }
        )
        df.at[i, "entity"] = pick["entity"]
        df.at[i, "entity_gt_id"] = pick["entity_gt_id"]

    # --- late_registration: 記録の t_obs に大きな遅延を加える ---
    df = df.reset_index(drop=True)
    is_record = df["event_type"] == "record"
    late_candidates = df.index[is_record].tolist()
    for i in sample(late_candidates, cfg.late_registration):
        delay = float(
            rng.uniform(cfg.late_registration_delay_s_min, cfg.late_registration_delay_s_max)
        )
        ledger_rows.append(
            {
                "episode_id": "",
                "injection_type": "late_registration",
                "entity": df.at[i, "entity"],
                "t_true": df.at[i, "t_true"],
                "detail": f'{{"delay_s": {delay:.3f}}}',
            }
        )
        df.at[i, "t_obs"] = float(df.at[i, "t_true"]) + delay

    # --- ghost_stock: 物理的裏付けの無い記録を捏造する ---
    df = df.reset_index(drop=True)
    known_entities = df[["entity", "entity_gt_id"]].dropna().drop_duplicates()
    for _ in range(cfg.ghost_stock):
        if known_entities.empty or episode_span <= 0:
            break
        pick = known_entities.sample(n=1, random_state=int(rng.integers(0, 2**31 - 1))).iloc[0]
        t_ghost = float(rng.uniform(0.0, episode_span))
        zone = str(rng.choice(ZONE_NAMES))
        ghost_row = {
            "event_type": "record",
            "entity": pick["entity"],
            "entity_gt_id": pick["entity_gt_id"],
            "anchor_type": None,
            "biz_step": CBV_BIZSTEP["storing"],
            "disposition": CBV_DISPOSITION_ACTIVE,
            "zone": zone,
            "detail": '{"ghost_stock": true}',
            "t_true": t_ghost,
            "t_obs": t_ghost,
        }
        df = pd.concat([df, pd.DataFrame([ghost_row])], ignore_index=True)
        ledger_rows.append(
            {
                "episode_id": "",
                "injection_type": "ghost_stock",
                "entity": pick["entity"],
                "t_true": t_ghost,
                "detail": f'{{"fabricated_zone": "{zone}"}}',
            }
        )

    ledger = pd.DataFrame(ledger_rows, columns=_LEDGER_COLUMNS)
    df = df.sort_values("t_true").reset_index(drop=True)
    return df, ledger


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
