"""エンコードの結果の指標化（M8-15）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sbp.worldmodel.base import ModelHealth
from sbp.worldmodel.encoding import EncodeReport, metric_samples

AT = datetime(2026, 11, 8, 9, tzinfo=UTC)
WM = "https://sbp.example/wm/koopman/0.1.0"


def _health(shift: float, error: float) -> ModelHealth:
    return ModelHealth(
        latent_shift=shift, pred_error=error, align_residual=0.0, samples=10, wm_version=WM
    )


def test_health_and_latency_become_three_series() -> None:
    report = EncodeReport(health=_health(0.1, 0.2), wm_version=WM, latency_s=[0.01, 0.03])
    samples = {s.name.key: s for s in metric_samples(report, at=AT)}
    assert set(samples) == {"latent_shift", "pred_error", "inference_latency"}
    assert samples["inference_latency"].value == pytest.approx(0.02)
    assert samples["latent_shift"].labels == {"wm_version": WM}


def test_ratios_are_capped_at_one() -> None:
    report = EncodeReport(health=_health(3.5, 0.2), wm_version=WM)
    samples = {s.name.key: s for s in metric_samples(report, at=AT)}
    assert samples["latent_shift"].value == 1.0


def test_nothing_measured_means_no_samples() -> None:
    assert metric_samples(EncodeReport(wm_version=WM), at=AT) == []
