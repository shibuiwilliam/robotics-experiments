"""イベントログ（記録源）。Redpanda producer / consumer、スキーマ検証、冪等性。

責務: イベントの送受信、``schemas/*.json`` による検証、``event_id`` による冪等化、順序（実体単位）。
非責務: 意味の確定、永続化先の選択、推論。
"""

from __future__ import annotations
