"""S4 真値導出の単体テスト（T11）— ORコア非依存・手組みフィクスチャ。"""

import pytest

from orx.oracle.scenarios.s4 import is_missed_anomaly, required_action

SOP = {"cooling_loop": "SOP-CL-12", "steam_line": "SOP-ST-07"}


def test_required_action_for_anomaly() -> None:
    assert required_action(True, "cooling_loop", SOP) == "SOP-CL-12"


def test_required_action_none_when_normal() -> None:
    assert required_action(False, "cooling_loop", SOP) is None


def test_required_action_unknown_system_raises() -> None:
    with pytest.raises(ValueError):
        required_action(True, "unknown", SOP)


def test_missed_anomaly() -> None:
    assert is_missed_anomaly(True, False) is True
    assert is_missed_anomaly(True, True) is False
    assert is_missed_anomaly(False, False) is False
