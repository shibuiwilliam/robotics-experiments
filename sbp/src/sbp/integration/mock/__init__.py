"""開発用の業務システムモック（integration ルール: SQLite + 台本）。

実システムに接続せずに M1 の全経路（取り込み → 写像 → BusinessEvent → 信念）を通すための
置き場。台本（scenario）は設計書 §14 のワークドエグザンプルに合わせ、CMMS の作業指示、
MES の稼働実績、ERP の予備品在庫、保全計画を再現する。

**ここで作るのは合成データだけ**で、真値（オラクル）は持たない。真値は ``sbp.testing`` の
``SynthWorld`` が持つ（不変条件 10）。モックは「業務側が何と言っているか」だけを提供する。
"""

from __future__ import annotations

from sbp.integration.mock.business_system import MockBusinessSystem, Scenario, ScenarioStep

__all__ = ["MockBusinessSystem", "Scenario", "ScenarioStep"]
