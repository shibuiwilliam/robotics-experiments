"""モデル族。設計書 §4.2 の表に沿って、物理ドメインごとに適した族を置く。

- ``koopman``: 設備・プロセス（回転機、反応器）。線形化された潜在遷移。
- ``jepa_ts``: 多変量時系列一般。潜在予測（M4-03）。

族は互いを import しない。同じ ``WorldModel`` Protocol を**独立に**満たすことが、
「モデルを差し替えても接地層が変わらない」という M4 の主張の中身である。
"""

from __future__ import annotations

from sbp.worldmodel.families.jepa_ts import JepaConfig, JepaTimeSeriesModel, TrainingReport
from sbp.worldmodel.families.koopman import KoopmanConfig, KoopmanModel, latent_shift_mmd

__all__ = [
    "JepaConfig",
    "JepaTimeSeriesModel",
    "KoopmanConfig",
    "KoopmanModel",
    "TrainingReport",
    "latent_shift_mmd",
]
