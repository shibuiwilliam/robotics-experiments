"""S7 真値導出の単体テスト（T14）— ORコア非依存・手組みフィクスチャ。"""

from orx.exp.suites.s7_ownership.model import S7Object
from orx.oracle.scenarios.s7 import ESCALATE, correct_object_index, delivery_outcome


def _obj(owner: str) -> S7Object:
    return S7Object(true_owner=owner, zone="z", embedding=[0.0])


OBJS = [_obj("sato"), _obj("tanaka"), _obj("suzuki")]


def test_success_when_correct_owner() -> None:
    assert delivery_outcome(0, "sato", OBJS) == "success"


def test_misdelivery_when_wrong_owner() -> None:
    assert delivery_outcome(1, "sato", OBJS) == "misdelivery"


def test_escalation() -> None:
    assert delivery_outcome(ESCALATE, "sato", OBJS) == "escalation"


def test_out_of_range_is_misdelivery() -> None:
    assert delivery_outcome(99, "sato", OBJS) == "misdelivery"


def test_correct_object_index() -> None:
    assert correct_object_index("suzuki", OBJS) == 2
    assert correct_object_index("unknown", OBJS) == ESCALATE
