"""X2 — 接触イベント蒸留（S2/S5 共有）。

シム接触（または宣言された接触スケジュール）を知覚側で蒸留して ContactEvent 列にする。
劣化ノブ `contact_miss_rate` は知覚側にのみ存在する（CLAUDE.md §9）。anchoring/kg は
ノイズを知らない。決定的（シード派生RNG）。
"""

from __future__ import annotations

import numpy as np

from orx.common.schemas import ContactEvent


def distill_contacts(
    contacts: list[ContactEvent], miss_rate: float, rng: np.random.Generator
) -> list[ContactEvent]:
    """接触列を蒸留する。`miss_rate` の確率で各接触を見落とす（H5 の被験ノブ）。

    時刻順は保存。観測者・両当事者はそのまま運ぶ。
    """
    if not 0.0 <= miss_rate <= 1.0:
        raise ValueError(f"miss_rate は [0,1]: {miss_rate}")
    kept: list[ContactEvent] = []
    for c in sorted(contacts, key=lambda e: (e.sim_time, e.a, e.b)):
        if miss_rate > 0.0 and rng.random() < miss_rate:
            continue
        kept.append(c)
    return kept
