"""S5 真値導出の単体テスト（T12）— ORコア非依存・手組みフィクスチャ。"""

import pytest

from orx.exp.suites.s5_hospital.model import Norm
from orx.oracle.scenarios.s5 import (
    audit_completeness,
    count_violations,
    forbidden_zone_classes,
)

NORMS = [Norm(item_class="controlled_drug", forbidden_zone_class="public")]
ZC = {"pharmacy": "secure", "hall": "public", "ward": "secure"}


def test_forbidden_zone_classes() -> None:
    assert forbidden_zone_classes("controlled_drug", NORMS) == {"public"}
    assert forbidden_zone_classes("linen", NORMS) == set()


def test_count_violations_through_public() -> None:
    assert count_violations(["pharmacy", "hall", "ward"], "controlled_drug", NORMS, ZC) == 1


def test_count_violations_compliant_zero() -> None:
    assert count_violations(["pharmacy", "ward"], "controlled_drug", NORMS, ZC) == 0


def test_count_violations_unrestricted_item() -> None:
    assert count_violations(["pharmacy", "hall", "ward"], "linen", NORMS, ZC) == 0


def test_audit_completeness() -> None:
    assert audit_completeness(4, 4) == pytest.approx(1.0)
    assert audit_completeness(2, 4) == pytest.approx(0.5)
    assert audit_completeness(0, 0) == pytest.approx(1.0)
