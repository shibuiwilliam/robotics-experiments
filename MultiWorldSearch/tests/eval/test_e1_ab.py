"""IMPROVEMENT P9/E1 — A/B harness structure tests (mock; live run via CLI)."""

from __future__ import annotations

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.embedding.mock import MockEmbedder
from mws.eval.e1_ab import PRE_REGISTERED, _measure_arm, run_e1_ab


def test_pre_registered_criterion_is_declared() -> None:
    assert PRE_REGISTERED["metrics"] == ["recall_at_5", "recall_at_10", "mrr"]
    assert "recall_at_5" in PRE_REGISTERED["hypothesis"]


def test_run_e1_ab_without_key_is_undetermined() -> None:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, google_api_key="")
    out = run_e1_ab(settings)
    assert out["verdict"]["prefix_improves"] is None
    assert "raw" not in out  # no arms were run (no silent fake verdicts)


def test_measure_arm_reports_metrics_per_scheme() -> None:
    """Structural check on a symmetric embedder: both schemes are measurable
    and identical (mock formatting is identity), so deltas would be 0."""
    emb = MockEmbedder(seed=0)
    raw = _measure_arm(emb, "raw", prefixed=False)
    prefixed = _measure_arm(emb, "prefixed", prefixed=True)
    for key in ("recall_at_5", "recall_at_10", "mrr"):
        assert raw[key] == prefixed[key]
    assert raw["arm"] == "raw"
    assert prefixed["prefixed"] is True
