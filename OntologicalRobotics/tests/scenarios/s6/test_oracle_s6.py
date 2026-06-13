"""S6 oracle 真値導出の単体テスト（手構築フィクスチャ）。ORコア非依存。"""

import pytest

from orx.oracle.scenarios.s6 import ESCALATE, correct_lane, is_high_cost_error, route_cost

ROUTE = {"battery": "fire_lane", "freon": "ozone_lane", "inert": "general"}
COST = {
    "battery": {"fire_lane": 0.0, "ozone_lane": 60.0, "general": 100.0},
    "freon": {"fire_lane": 30.0, "ozone_lane": 0.0, "general": 40.0},
    "inert": {"fire_lane": 5.0, "ozone_lane": 5.0, "general": 0.0},
}


def test_correct_lane() -> None:
    assert correct_lane("battery", ROUTE) == "fire_lane"
    with pytest.raises(ValueError, match="未知"):
        correct_lane("nope", ROUTE)


def test_route_cost_and_escalation() -> None:
    assert route_cost("battery", "fire_lane", COST, 3.0) == 0.0
    assert route_cost("battery", "general", COST, 3.0) == 100.0
    assert route_cost("battery", ESCALATE, COST, 3.0) == 3.0


def test_high_cost_error() -> None:
    assert is_high_cost_error("battery", "general", COST, 50.0)  # 100 >= 50
    assert is_high_cost_error("battery", "ozone_lane", COST, 50.0)  # 60 >= 50
    assert not is_high_cost_error("battery", "fire_lane", COST, 50.0)  # 0
    assert not is_high_cost_error("freon", "general", COST, 50.0)  # 40 < 50
    assert not is_high_cost_error("battery", ESCALATE, COST, 50.0)  # 委譲は対象外
