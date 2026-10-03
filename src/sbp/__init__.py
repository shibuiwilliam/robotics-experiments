"""SBP — Semantic Bridge Platform.

ワールドモデル × オントロジーで物理世界の観測と業務記録を同じ語彙・時間軸・確信度で
突き合わせる信念維持システム。層の依存は
``core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge ← reasoning ← actions ← api``
の一方向のみ（`make deps-check` で機械検査する）。
"""

from __future__ import annotations

__version__ = "0.1.0"
