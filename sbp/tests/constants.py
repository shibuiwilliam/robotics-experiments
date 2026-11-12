"""テスト全体で共有する固定値。

時刻は設計書 §14 のワークドエグザンプル（2026-09-12 のポンプ P-101）に合わせる。
ここに置くのはテスト用の定数のみで、真値（オラクル）は ``sbp.testing`` が持つ（不変条件 10）。
"""

from __future__ import annotations

from datetime import UTC, datetime

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
"""テストの基準時刻。"""

ONTOLOGY_VERSION = "0.1.0"
SITE = "siteA"
PUMP = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = "https://sbp.example/ent#Running"
UNDER_MAINTENANCE = "https://sbp.example/ent#UnderMaintenance"
PROV_BUNDLE = "https://sbp.example/site/siteA/prov/test-bundle"
