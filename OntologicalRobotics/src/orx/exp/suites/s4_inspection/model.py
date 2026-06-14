"""S4 プラント点検の型（T11, H2/H5）。

資産台帳（P&ID個体: ラフ位置・系統・据付時署名）と、2視点（ドローン/地上ロボット）の知覚個体を
ID無しでアンカリングし、矛盾する異常観測を来歴・確信度で調停する。真値は採点専用。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel


class S4Asset(StrictModel):
    """資産台帳の設備個体（ID無しで知覚と対応付ける対象）。"""

    asset_id: str  # 例 V-205
    system: str  # 系統トポロジ（cooling_loop 等）→ SOP 引当のキー
    ledger_pos: list[float]  # 台帳のラフ座標（2D）
    true_pos: list[float]  # 真の位置（観測生成・採点専用）
    true_anomaly: bool  # 真の異常有無（採点専用）


class S4World(StrictModel):
    """S4 世界カタログ（configs/world/s4_inspection.yaml）。"""

    assets: list[S4Asset]
    sop: dict[str, str]  # 系統 -> 点検手順SOP ID
    embedding_dim: int = 16
    position_noise: float = 0.4  # 観測位置ノイズ（knob）: 大きいほど位置単独で曖昧
    ledger_noise: float = 0.3  # 台帳ラフ座標の誤差
    signature_noise: float = 0.12  # 観測署名ノイズ
    ref_noise: float = 0.08  # 据付時参照署名のノイズ
    # 2視点の異常検知特性（決定的）: ドローン上方は見落としがち、地上近接は高信頼
    drone_detect_conf: float = 0.5
    ground_detect_conf: float = 0.9
    normal_conf: float = 0.8


class S4Observation(StrictModel):
    """1視点の知覚個体（ID無し）。"""

    viewpoint: str  # drone | ground
    true_asset: str  # 採点専用（真の対応資産）
    pos: list[float]  # 観測位置
    signature: list[float]  # 観測視覚署名
    anomaly_reading: bool  # この視点の異常判定
    confidence: float  # 判定確信度（来歴・確信度の調停に使う）


class S4LedgerEntry(StrictModel):
    asset_id: str
    system: str
    ledger_pos: list[float]
    ref_signature: list[float]  # 据付時の参照署名（ベクトル同定に使う）


class S4Episode(StrictModel):
    observations: list[S4Observation]
    ledger: list[S4LedgerEntry]
