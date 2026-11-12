"""学習パイプライン（設計書 §4.5、§4.6）。

合成損失 ``L_pred + λ_phys·L_phys + λ_probe·L_probe + λ_logic·L_logic + λ_align·L_align`` を
回し、**各項を個別に記録する**。λ_probe と λ_align の項はプローブ出力（記号の決定）を必要と
するため、この層では受け取るだけで中身は ``bridge`` が与える（ADR-0007 と同じ境界）。
"""

from __future__ import annotations

from sbp.worldmodel.train.loop import (
    EpochRecord,
    TrainingProvenance,
    stage_gate_passed,
    train_latent_predictor,
)
from sbp.worldmodel.train.losses import (
    LossTerm,
    LossWeights,
    TermValues,
    exclusivity_penalty,
    smoothness_penalty,
)

__all__ = [
    "EpochRecord",
    "LossTerm",
    "LossWeights",
    "TermValues",
    "TrainingProvenance",
    "exclusivity_penalty",
    "smoothness_penalty",
    "stage_gate_passed",
    "train_latent_predictor",
]
