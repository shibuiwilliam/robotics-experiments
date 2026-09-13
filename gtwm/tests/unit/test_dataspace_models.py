from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from gtwm.dataspace.models import ExchangeBelief, PredictionRecord, Provenance

pytestmark = pytest.mark.unit


def _prov() -> Provenance:
    return Provenance(generated_by="gt:WM", attributed_to="site_b", generated_at=datetime.now(UTC))


def test_exchange_belief_rejects_raw_frame_field() -> None:
    """poc_plan.md 5.5「生映像・潜在表現は交換しない」を型レベルで強制する。"""
    with pytest.raises(ValidationError):
        ExchangeBelief(
            subject="gt:Pallet_0001",
            predicate="gt:currentZone",
            object="gt:Zone_Storage_A",
            confidence=0.9,
            valid_from=datetime.now(UTC),
            provenance=_prov(),
            frame=[[0, 0, 0]],  # type: ignore[call-arg]
        )


def test_prediction_record_rejects_latent_field() -> None:
    with pytest.raises(ValidationError):
        PredictionRecord(
            variable="queue_len",
            point_estimate=1.0,
            interval_low=0.5,
            interval_high=1.5,
            horizon_s=600.0,
            model_version="wm-smoke",
            confidence=0.8,
            provenance=_prov(),
            latent=[0.1, 0.2, 0.3],  # type: ignore[call-arg]
        )


def test_prediction_record_confidence_must_be_a_probability() -> None:
    """`confidence` はモデルが実際に出す較正済み確率のためのフィールド：[0,1] 範囲外は拒否する。"""
    with pytest.raises(ValidationError):
        PredictionRecord(
            variable="queue_len",
            point_estimate=1.0,
            interval_low=0.5,
            interval_high=1.5,
            horizon_s=600.0,
            model_version="wm-smoke",
            confidence=1.5,
            provenance=_prov(),
        )


def test_valid_exchange_belief_constructs() -> None:
    b = ExchangeBelief(
        subject="gt:Pallet_0001",
        predicate="gt:currentZone",
        object="gt:Zone_Storage_A",
        confidence=0.9,
        valid_from=datetime.now(UTC),
        provenance=_prov(),
    )
    assert b.confidence == 0.9
