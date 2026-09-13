from datetime import UTC, datetime, timedelta

import pytest

from gtwm.dataspace.policy import (
    OdrlPolicy,
    PolicyViolation,
    check_purpose,
    check_resharing,
    retention_cutoff,
)

pytestmark = pytest.mark.unit


def _policy(**overrides: object) -> OdrlPolicy:
    defaults: dict[str, object] = {
        "permitted_purposes": frozenset({"inbound_planning"}),
        "retention_days": 30,
        "allow_resharing": False,
    }
    defaults.update(overrides)
    return OdrlPolicy(**defaults)  # type: ignore[arg-type]


def test_check_purpose_allows_permitted() -> None:
    check_purpose(_policy(), "inbound_planning")  # should not raise


def test_check_purpose_rejects_other_purpose() -> None:
    with pytest.raises(PolicyViolation):
        check_purpose(_policy(), "marketing")


def test_check_resharing_rejects_when_disallowed() -> None:
    with pytest.raises(PolicyViolation):
        check_resharing(_policy(allow_resharing=False), requester_will_reshare=True)


def test_check_resharing_allows_when_permitted() -> None:
    check_resharing(_policy(allow_resharing=True), requester_will_reshare=True)


def test_retention_cutoff_is_now_minus_days() -> None:
    now = datetime(2026, 1, 31, tzinfo=UTC)
    cutoff = retention_cutoff(_policy(retention_days=10), now)
    assert cutoff == now - timedelta(days=10)


def test_policy_requires_at_least_one_purpose() -> None:
    with pytest.raises(ValueError, match="permitted_purposes"):
        OdrlPolicy(permitted_purposes=frozenset(), retention_days=30)


def test_policy_requires_positive_retention() -> None:
    with pytest.raises(ValueError, match="retention_days"):
        OdrlPolicy(permitted_purposes=frozenset({"x"}), retention_days=0)
