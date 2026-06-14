"""S3 真値導出の単体テスト（T10）— ORコア非依存・手組みフィクスチャ。"""

import pytest

from orx.exp.suites.s3_multi_vendor.model import MachineSpec, ProductSpec
from orx.oracle.scenarios.s3 import (
    allocation_correct,
    best_feasible_success,
    chosen_true_success,
    true_success,
)

A = MachineSpec(
    machine_id="arm_a1", vendor="vendor_a", declared_payload_kg=5.0, prior_success=0.9,
    true_payload_kg=5.0, true_material_success={"plastic": 0.95, "metal": 0.4, "default": 0.5},
)
B = MachineSpec(
    machine_id="cobot_b1", vendor="vendor_b", declared_payload_kg=20.0, prior_success=0.9,
    true_payload_kg=20.0, true_material_success={"plastic": 0.7, "metal": 0.95, "default": 0.6},
    degrades=True,
)
C = MachineSpec(
    machine_id="agv_c1", vendor="vendor_c", declared_payload_kg=15.0, prior_success=0.85,
    true_payload_kg=15.0, true_material_success={"plastic": 0.6, "metal": 0.9, "default": 0.6},
)
MACHINES = [A, B, C]
TRAY = ProductSpec(name="tray", weight_kg=3.0, material="plastic")
ENGINE = ProductSpec(name="engine_block", weight_kg=12.0, material="metal")


def test_true_success_payload_exceeded_is_zero() -> None:
    assert true_success(A, ENGINE) == 0.0  # 12kg > a1 上限 5kg


def test_true_success_material_rate() -> None:
    assert true_success(A, TRAY) == pytest.approx(0.95)
    assert true_success(B, ENGINE) == pytest.approx(0.95)


def test_true_success_degradation_applies_only_to_faulted() -> None:
    assert true_success(B, ENGINE, 0.2) == pytest.approx(0.19)


def test_best_feasible_picks_global_best() -> None:
    # 段取り替え後（engine）の最良は b1 metal 0.95
    assert best_feasible_success(MACHINES, ENGINE, frozenset(), 0.2) == pytest.approx(0.95)


def test_best_feasible_under_fault_switches_to_c1() -> None:
    # b1 故障(×0.2=0.19) → 最良は c1 metal 0.9
    best = best_feasible_success(MACHINES, ENGINE, frozenset({"cobot_b1"}), 0.2)
    assert best == pytest.approx(0.9)


def test_allocation_correct_within_tolerance() -> None:
    assert allocation_correct(MACHINES, ENGINE, "cobot_b1", frozenset(), 0.2, 0.05)
    # a1 は engine に不適格 → 不正答
    assert not allocation_correct(MACHINES, ENGINE, "arm_a1", frozenset(), 0.2, 0.05)


def test_allocation_correct_after_fault_requires_c1() -> None:
    f = frozenset({"cobot_b1"})
    assert allocation_correct(MACHINES, ENGINE, "agv_c1", f, 0.2, 0.05)
    assert not allocation_correct(MACHINES, ENGINE, "cobot_b1", f, 0.2, 0.05)  # 劣化機体は不正答


def test_allocation_correct_none_is_false() -> None:
    assert not allocation_correct(MACHINES, ENGINE, None, frozenset(), 0.2, 0.05)


def test_chosen_true_success_none_zero() -> None:
    assert chosen_true_success(MACHINES, ENGINE, None, frozenset(), 0.2) == 0.0
    assert chosen_true_success(MACHINES, ENGINE, "agv_c1", frozenset(), 0.2) == pytest.approx(0.9)
