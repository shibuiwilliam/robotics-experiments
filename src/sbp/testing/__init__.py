"""テスト支援。合成データ生成 SynthWorld（テスト用フィクスチャ）。

責務: 合成データと真値（オラクル）の提供。
非責務: ユースケース検証（別リポジトリ）。真値はこの層の外から読まない（不変条件 10）。

SynthWorld.oracle への参照が sbp.testing の外に漏れないことは import-linter の
契約で機械検査している。これは「真値を見て推定する」自己欺瞞を構造的に防ぐ境界である。
"""

from __future__ import annotations

from sbp.testing.synth import (
    LATENT_DIM,
    Fluent,
    GroundTruth,
    ObservationWindow,
    SynthConfig,
    SynthWorld,
)

__all__ = [
    "LATENT_DIM",
    "Fluent",
    "GroundTruth",
    "ObservationWindow",
    "SynthConfig",
    "SynthWorld",
]
