"""S3 多ベンダー製造ラインの型（T10, H1/H3）。

異ベンダーの機体が能力契約（宣言）を持つ。真の能力プロファイルは採点専用で計画器は見ない。
段取り替え（品種切替）と故障（経年劣化）を時間軸（エピソードindex）で表現する。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel


class ProductSpec(StrictModel):
    """品種（ワーク）仕様。段取り替えで重量・把持面素材が変わる。"""

    name: str
    weight_kg: float
    material: str  # 把持面素材タグ（plastic / metal ...）


class MachineSpec(StrictModel):
    """機体の能力契約（宣言）＋真プロファイル（採点専用・計画器非可視）。"""

    machine_id: str
    vendor: str  # vendor_a / vendor_b / vendor_c（語彙が異なる）
    declared_payload_kg: float  # 能力契約の宣言上限
    prior_success: float  # 宣言事前成功率（台帳のベータ事前）
    true_payload_kg: float  # 真の可搬上限（採点専用）
    true_material_success: dict[str, float]  # 真の素材別成功率（採点専用）
    degrades: bool = False  # 故障(経年劣化)の対象機体か


class S3World(StrictModel):
    """S3 世界カタログ（configs/world/s3_multi_vendor.yaml）。"""

    machines: list[MachineSpec]
    products: dict[str, ProductSpec]  # name -> spec
    initial_product: str
    setup_change_product: str  # 段取り替え後の品種（重量・素材が変化）
    setup_change_step: int  # このエピソードindex以降は新品種
    fault_machine: str  # 故障する機体
    fault_step: int  # このエピソードindex以降に劣化
    fault_degradation: float  # 故障時に true success に乗じる係数 (<1)
    reference_vendor: str  # SOPが書かれた基準ベンダー（B1 はこの語彙しか解せない）
    # H1 オンボーディング: ベンダーD参入のスキーマ・フィールドと既知意味型
    vendor_d_fields: list[str] = []
    known_semantic_fields: list[str] = []
    n_episodes: int = 12
    tasks_per_episode: int = 6
    tolerance: float = 0.05  # 割当正答の許容（真の最良との差）
