"""S4 プラント点検の真値導出（T11）。

真の異常有無と系統→SOP写像から、必要な作業指示（該当系統SOP）を機械導出する。
ORコア非依存（プリミティブのみ）。アンカリング真値は観測の true_asset を直接照合する。
"""

from __future__ import annotations


def required_action(true_anomaly: bool, system: str, sop_map: dict[str, str]) -> str | None:
    """真に必要な作業指示（異常→該当系統SOP）。正常なら None。"""
    if not true_anomaly:
        return None
    if system not in sop_map:
        raise ValueError(f"系統 {system!r} の SOP が台帳に無い")
    return sop_map[system]


def is_missed_anomaly(true_anomaly: bool, concluded_anomaly: bool) -> bool:
    """真の異常を正常と誤結論（安全上の重大失敗＝見逃し）。"""
    return true_anomaly and not concluded_anomaly
