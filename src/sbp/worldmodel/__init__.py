"""ワールドモデル層。潜在状態推定、遷移、予測、介入シミュレーション。

責務: 潜在状態推定、遷移 f、ロールアウト、介入シミュレーション。
非責務: **記号の決定**（プローブは ``sbp.bridge``）、業務規則の適用。

``probe`` / ``explain`` は Protocol に含めない（ADR-0007）。帰属は
:meth:`~sbp.worldmodel.base.WorldModel.attribute` が「線形汎関数の係数に対する潜在次元の寄与」
として返し、WM はそれが何の概念なのかを知らない。
"""

from __future__ import annotations

from sbp.worldmodel.base import (
    Attribution,
    LatentLevel,
    LatentSpec,
    LatentState,
    ModelHealth,
    Trajectory,
    WorldModel,
    WorldModelMeta,
)
from sbp.worldmodel.families import (
    JepaConfig,
    JepaTimeSeriesModel,
    KoopmanConfig,
    KoopmanModel,
    TrainingReport,
    latent_shift_mmd,
)

__all__ = [
    "Attribution",
    "JepaConfig",
    "JepaTimeSeriesModel",
    "KoopmanConfig",
    "KoopmanModel",
    "LatentLevel",
    "LatentSpec",
    "LatentState",
    "ModelHealth",
    "TrainingReport",
    "Trajectory",
    "WorldModel",
    "WorldModelMeta",
    "latent_shift_mmd",
]
