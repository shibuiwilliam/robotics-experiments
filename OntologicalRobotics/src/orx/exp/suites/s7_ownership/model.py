"""S7 介護施設「所有」シナリオの型（T14, H2/H4/H6）。

所有は観測不可能な情報的関係。類似外観の小物（ID無し）が複数あり、所有(記号)×最終目撃(時空間)
×視覚署名(ベクトル)の三系統融合で「誰のものか」を解く。真の所有者は採点専用。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel


class S7World(StrictModel):
    """S7 世界カタログ（configs/world/s7_ownership.yaml）。"""

    item_type: str = "glasses"
    residents: list[str]  # 入居者ID
    resident_zone: dict[str, str]  # 入居者 -> 最終目撃ゾーン（共有→loc単独では曖昧）
    rooms: dict[str, str]  # 入居者 -> 居室（配送先・照合には使わない）
    embedding_dim: int = 16
    base_sep: float = 1.0  # 所有者間の署名分離（小さいほど瓜二つ＝knob lookalike_sep）
    note_sigma: float = 0.15  # 台帳の特徴メモ記録ノイズ
    episode_sigma: float = 0.15  # 観測ノイズ
    margin_threshold: float = 0.05  # OR-full の確認行動(X5)発動マージン


class S7Object(StrictModel):
    """観測された物理小物（ID無し）。true_owner は採点専用。"""

    true_owner: str
    zone: str  # 現在観測ゾーン（=最終目撃ゾーン）
    embedding: list[float]  # 真の視覚署名


class S7Ledger(StrictModel):
    """入居者台帳: 所有(記号)＋特徴メモ署名(ベクトル)＋最終目撃ゾーン(時空間)。"""

    note_embedding: dict[str, list[float]]  # 入居者 -> 記録された特徴メモ署名
    last_seen_zone: dict[str, str]  # 入居者 -> 最終目撃ゾーン


class S7Episode(StrictModel):
    objects: list[S7Object]
    ledger: S7Ledger
    prototype: list[float]  # 物品種の一般プロトタイプ（OR-vec が使う owner非依存クエリ）
