"""S5 病院内搬送の型（T12, 規範層・H5）。

規範（義務・禁止・許可）が物理空間の通行を支配する。規制薬物は公共区画を通過してはならない等。
custody連鎖（全通行の来歴）の完全記録が監査に必須。規範チェックと来歴記録は直交する2機構。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel


class Norm(StrictModel):
    """禁止規範: ある物分類は、ある区画分類を通過してはならない（計画時に事前棄却）。"""

    item_class: str  # controlled_drug | specimen | linen
    forbidden_zone_class: str  # public | ...
    modality: str = "prohibition"


class Transport(StrictModel):
    transport_id: str
    item_class: str
    src: str
    dst: str


class S5World(StrictModel):
    """S5 世界カタログ（configs/world/s5_hospital.yaml）。"""

    zone_class: dict[str, str]  # zone -> public | secure | locked
    edges: list[list[str]]  # 無向コリドー [[a,b], ...]
    transports: list[Transport]
    norms: list[Norm]
    audited_item_class: str = "controlled_drug"  # 監査クエリ対象（custody完全性）
