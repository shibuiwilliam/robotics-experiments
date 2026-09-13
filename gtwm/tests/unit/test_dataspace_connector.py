from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gtwm.dataspace.audit import AuditLog
from gtwm.dataspace.connector import Connector
from gtwm.dataspace.models import ExchangeBelief, ExchangeRequest, Provenance
from gtwm.dataspace.policy import OdrlPolicy

pytestmark = pytest.mark.unit


def _connector(tmp_path: Path, **policy_overrides: object) -> Connector:
    defaults: dict[str, object] = {
        "permitted_purposes": frozenset({"inbound_planning"}),
        "retention_days": 30,
    }
    defaults.update(policy_overrides)
    policy = OdrlPolicy(**defaults)  # type: ignore[arg-type]
    return Connector(site_id="site_a", policy=policy, audit=AuditLog(tmp_path / "audit.sqlite"))


def _belief(when: datetime) -> ExchangeBelief:
    return ExchangeBelief(
        subject="gt:Pallet_0001",
        predicate="gt:currentZone",
        object="gt:Zone_Storage_A",
        confidence=0.9,
        valid_from=when,
        provenance=Provenance(generated_by="gt:WM", attributed_to="site_a", generated_at=when),
    )


def test_grants_request_with_permitted_purpose(tmp_path: Path) -> None:
    conn = _connector(tmp_path)
    now = datetime.now(UTC)
    conn.publish_belief(_belief(now))
    resp = conn.handle_request(
        ExchangeRequest(
            requester_site="site_b",
            purpose="inbound_planning",
            item_type="belief",
            since=now - timedelta(days=1),
        ),
        now=now,
    )
    assert resp.granted is True
    assert len(resp.items) == 1
    assert conn.audit.count_violations() == 0


def test_rejects_request_with_wrong_purpose(tmp_path: Path) -> None:
    conn = _connector(tmp_path)
    now = datetime.now(UTC)
    conn.publish_belief(_belief(now))
    resp = conn.handle_request(
        ExchangeRequest(
            requester_site="site_b",
            purpose="marketing",
            item_type="belief",
            since=now - timedelta(days=1),
        ),
        now=now,
    )
    assert resp.granted is False
    assert resp.items == []
    assert conn.audit.count_violations() == 1


def test_excludes_items_older_than_retention_period(tmp_path: Path) -> None:
    conn = _connector(tmp_path, retention_days=1)
    now = datetime.now(UTC)
    old = now - timedelta(days=5)
    conn.publish_belief(_belief(old))
    resp = conn.handle_request(
        ExchangeRequest(
            requester_site="site_b",
            purpose="inbound_planning",
            item_type="belief",
            since=old - timedelta(days=1),
        ),
        now=now,
    )
    assert resp.granted is True
    assert resp.items == []  # 保持期間切れで除外される
