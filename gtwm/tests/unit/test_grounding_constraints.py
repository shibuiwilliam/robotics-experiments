import pytest
import torch

from gtwm.grounding.constraints import (
    constraint_loss,
    hazmat_adjacency_violation,
    single_zone_violation,
    zone_capacity_violation,
)

pytestmark = pytest.mark.unit


def test_single_zone_violation_zero_when_one_hot() -> None:
    zone_probs = torch.tensor([[1.0, 0.0, 0.0]])
    violation = single_zone_violation(zone_probs)
    assert violation.item() == pytest.approx(0.0)


def test_single_zone_violation_high_when_uniform() -> None:
    zone_probs = torch.tensor([[1 / 3, 1 / 3, 1 / 3]])
    violation = single_zone_violation(zone_probs)
    assert violation.item() == pytest.approx(2 / 3)


def test_zone_capacity_violation_low_under_capacity() -> None:
    zone_probs = torch.zeros(2, 3)
    zone_probs[:, 0] = 1.0  # 2個体とも zone0 に確実に所属、容量5なら余裕
    capacities = torch.tensor([5.0, 5.0, 5.0])
    violation = zone_capacity_violation(zone_probs, capacities)
    assert violation[0].item() < 0.5


def test_zone_capacity_violation_high_over_capacity() -> None:
    zone_probs = torch.zeros(10, 3)
    zone_probs[:, 0] = 1.0  # 10個体が zone0 に所属、容量1
    capacities = torch.tensor([1.0, 5.0, 5.0])
    violation = zone_capacity_violation(zone_probs, capacities)
    assert violation[0].item() > 0.9


def test_hazmat_adjacency_violation_product_tnorm() -> None:
    hazard_a = torch.tensor([1.0])
    hazard_b = torch.tensor([1.0])
    incompatible = torch.tensor([1.0])
    adjacent = torch.tensor([1.0])
    violation = hazmat_adjacency_violation(hazard_a, hazard_b, incompatible, adjacent)
    assert violation.item() == pytest.approx(1.0)

    adjacent_false = torch.tensor([0.0])
    violation2 = hazmat_adjacency_violation(hazard_a, hazard_b, incompatible, adjacent_false)
    assert violation2.item() == pytest.approx(0.0)


def test_constraint_loss_is_differentiable() -> None:
    zone_probs = torch.rand(4, 3, requires_grad=True)
    loss = constraint_loss(zone_probs)
    loss.backward()
    assert zone_probs.grad is not None
