"""アサーションの単体テスト（不変条件 1, 2, 3, 8）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from tests.constants import ONTOLOGY_VERSION, PROV_BUNDLE, PUMP, RUNNING, T0

from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.errors import TemporalError
from sbp.core.modality import Modality

HOUR = timedelta(hours=1)


def _base_kwargs() -> dict[str, object]:
    return {
        "subject": PUMP,
        "predicate": RUNNING,
        "object": True,
        "valid_from": T0,
        "tx_from": T0,
        "modality": Modality.INFERRED,
        "belief": BooleanBelief(p=0.9),
        "provenance": PROV_BUNDLE,
        "ontology_version": ONTOLOGY_VERSION,
    }


class TestRequiredFields:
    @pytest.mark.parametrize(
        "missing",
        [
            "subject",
            "predicate",
            "object",
            "valid_from",
            "tx_from",
            "modality",
            "belief",
            "provenance",
            "ontology_version",
        ],
    )
    def test_every_invariant1_field_is_required(self, missing: str) -> None:
        """不変条件 1: どの必須フィールドが欠けても構築できない。"""
        kwargs = _base_kwargs()
        del kwargs[missing]
        with pytest.raises(ValidationError):
            Assertion(**kwargs)  # type: ignore[arg-type]

    def test_schema_version_defaults(self, sample_assertion: Assertion) -> None:
        assert sample_assertion.schema_version == "1.0.0"

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Assertion(**_base_kwargs(), is_true=True)  # type: ignore[arg-type]

    def test_invalid_semver_rejected(self) -> None:
        kwargs = _base_kwargs() | {"ontology_version": "v1"}
        with pytest.raises(ValidationError, match="semver"):
            Assertion(**kwargs)  # type: ignore[arg-type]

    def test_non_iri_subject_rejected(self) -> None:
        """不変条件 5: IRI でない主語は受け付けない。"""
        kwargs = _base_kwargs() | {"subject": "P-101"}
        with pytest.raises(ValidationError, match="valid IRI"):
            Assertion(**kwargs)  # type: ignore[arg-type]


class TestBitemporal:
    def test_assertion_is_frozen(self, sample_assertion: Assertion) -> None:
        """不変条件 2: アサーションは書き換えられない。"""
        with pytest.raises(ValidationError):
            sample_assertion.belief = BooleanBelief(p=0.1)  # type: ignore[misc]

    def test_close_tx_returns_new_object_and_keeps_original_open(
        self, sample_assertion: Assertion
    ) -> None:
        """訂正は旧文の tx_to を閉じて新文を追加する（不変条件 2）。"""
        closed = sample_assertion.close_tx(T0 + HOUR)
        assert closed.tx_to == T0 + HOUR
        assert not closed.is_current
        assert sample_assertion.is_current, "元の文は変更されない"
        assert closed.assertion_id == sample_assertion.assertion_id

    def test_close_tx_before_tx_from_is_rejected(self, sample_assertion: Assertion) -> None:
        with pytest.raises(TemporalError):
            sample_assertion.close_tx(T0 - HOUR)

    def test_supersede_creates_new_identity_and_tx(self, sample_assertion: Assertion) -> None:
        new = sample_assertion.supersede(
            belief=BooleanBelief(p=0.14), at=T0 + HOUR, provenance=PROV_BUNDLE
        )
        assert new.assertion_id != sample_assertion.assertion_id
        assert new.tx_from == T0 + HOUR
        assert new.tx_to is None
        assert new.valid_from == sample_assertion.valid_from, "有効時間は同じ主張のまま"
        assert isinstance(new.belief, BooleanBelief)
        assert new.belief.p == pytest.approx(0.14)

    def test_valid_at_and_as_of(self) -> None:
        a = Assertion(**(_base_kwargs() | {"valid_to": T0 + HOUR, "tx_to": T0 + 2 * HOUR}))  # type: ignore[arg-type]
        assert a.holds_at(T0)
        assert not a.holds_at(T0 + HOUR), "右開区間"
        assert a.known_as_of(T0 + HOUR)
        assert not a.known_as_of(T0 + 2 * HOUR)

    def test_empty_valid_interval_rejected(self) -> None:
        with pytest.raises((ValidationError, TemporalError)):
            Assertion(**(_base_kwargs() | {"valid_to": T0}))  # type: ignore[arg-type]


class TestModalityConstraints:
    def test_predicted_requires_horizon_and_lead_time(self) -> None:
        kwargs = _base_kwargs() | {"modality": Modality.PREDICTED}
        with pytest.raises(ValidationError, match="horizon_origin and lead_time"):
            Assertion(**kwargs)  # type: ignore[arg-type]

        ok = Assertion(**(kwargs | {"horizon_origin": T0, "lead_time": timedelta(days=7)}))  # type: ignore[arg-type]
        assert ok.lead_time == timedelta(days=7)

    def test_non_predicted_cannot_carry_horizon(self) -> None:
        kwargs = _base_kwargs() | {"horizon_origin": T0, "lead_time": timedelta(days=1)}
        with pytest.raises(ValidationError, match="only valid for modality=predicted"):
            Assertion(**kwargs)  # type: ignore[arg-type]

    def test_non_positive_lead_time_rejected(self) -> None:
        kwargs = _base_kwargs() | {
            "modality": Modality.PREDICTED,
            "horizon_origin": T0,
            "lead_time": timedelta(0),
        }
        with pytest.raises(ValidationError, match="lead_time must be positive"):
            Assertion(**kwargs)  # type: ignore[arg-type]

    def test_hypothetical_requires_scenario(self) -> None:
        """不変条件 3: hypothetical はシナリオに隔離される。"""
        kwargs = _base_kwargs() | {"modality": Modality.HYPOTHETICAL}
        with pytest.raises(ValidationError, match="must carry a scenario"):
            Assertion(**kwargs)  # type: ignore[arg-type]

        ok = Assertion(**(kwargs | {"scenario": "urn:sbp:scenario:defer-maintenance"}))  # type: ignore[arg-type]
        assert ok.scenario == "urn:sbp:scenario:defer-maintenance"

    def test_non_hypothetical_cannot_carry_scenario(self) -> None:
        kwargs = _base_kwargs() | {"scenario": "urn:sbp:scenario:x"}
        with pytest.raises(ValidationError, match="only valid for modality=hypothetical"):
            Assertion(**kwargs)  # type: ignore[arg-type]
