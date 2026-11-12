"""様相格子と信念の単体テスト（不変条件 3, 7）。"""

from __future__ import annotations

import math
from itertools import pairwise

import pytest
from pydantic import ValidationError

from sbp.core.belief import BooleanBelief, CategoricalBelief, NumericBelief, Uncertainty
from sbp.core.errors import ModalityError
from sbp.core.modality import MAIN_BELIEF_MODALITIES, Modality


class TestModalityLattice:
    def test_exactly_six_modalities(self) -> None:
        """不変条件 3: 様相はこの 6 値のみ。"""
        assert {m.value for m in Modality} == {
            "observed",
            "inferred",
            "predicted",
            "planned",
            "normative",
            "hypothetical",
        }

    def test_factuality_chain_order(self) -> None:
        """observed < inferred < predicted < planned が事実性の鎖。"""
        chain = [Modality.OBSERVED, Modality.INFERRED, Modality.PREDICTED, Modality.PLANNED]
        for earlier, later in pairwise(chain):
            assert earlier.precedes(later)
            assert not later.precedes(earlier)

    def test_normative_is_orthogonal(self) -> None:
        """normative は事実性の鎖に乗らず、どの様相とも比較不能。"""
        assert not Modality.NORMATIVE.is_on_factuality_chain
        for other in Modality:
            assert not Modality.NORMATIVE.precedes(other)
            assert not other.precedes(Modality.NORMATIVE)

    def test_hypothetical_is_isolated_from_main_belief(self) -> None:
        """hypothetical だけが主信念に入れない。"""
        assert not Modality.HYPOTHETICAL.enters_main_belief
        assert Modality.HYPOTHETICAL not in MAIN_BELIEF_MODALITIES
        assert len(MAIN_BELIEF_MODALITIES) == 5

    def test_assert_enters_main_belief_raises_for_hypothetical(self) -> None:
        with pytest.raises(ModalityError, match="promote_scenario"):
            Modality.HYPOTHETICAL.assert_enters_main_belief()

    def test_assert_enters_main_belief_passes_for_others(self) -> None:
        for m in MAIN_BELIEF_MODALITIES:
            m.assert_enters_main_belief()

    def test_precedes_is_irreflexive(self) -> None:
        for m in Modality:
            assert not m.precedes(m)

    def test_only_predicted_requires_horizon(self) -> None:
        assert [m for m in Modality if m.requires_horizon] == [Modality.PREDICTED]

    def test_only_hypothetical_requires_scenario(self) -> None:
        assert [m for m in Modality if m.requires_scenario] == [Modality.HYPOTHETICAL]


class TestBooleanBelief:
    def test_probability_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            BooleanBelief(p=1.5)
        with pytest.raises(ValidationError):
            BooleanBelief(p=-0.1)

    def test_calibrated_flag_cannot_be_false(self) -> None:
        """不変条件 7: 較正済みの値だけを belief に入れる。"""
        with pytest.raises(ValidationError):
            BooleanBelief(p=0.5, calibrated=False)  # type: ignore[arg-type]

    def test_log_odds_and_entropy(self) -> None:
        assert BooleanBelief(p=0.5).log_odds == pytest.approx(0.0)
        assert BooleanBelief(p=0.5).entropy_nats == pytest.approx(math.log(2))
        assert BooleanBelief(p=1.0).entropy_nats == 0.0
        assert BooleanBelief(p=1.0).log_odds == math.inf
        assert BooleanBelief(p=0.0).log_odds == -math.inf

    def test_negate_preserves_uncertainty(self) -> None:
        u = Uncertainty(epistemic=0.1, aleatoric=0.2)
        b = BooleanBelief(p=0.8, uncertainty=u).negate()
        assert b.p == pytest.approx(0.2)
        assert b.uncertainty == u

    def test_is_frozen(self) -> None:
        b = BooleanBelief(p=0.5)
        with pytest.raises(ValidationError):
            b.p = 0.9  # type: ignore[misc]


class TestCategoricalBelief:
    def test_pmf_must_sum_to_one(self) -> None:
        with pytest.raises(ValidationError, match="sum to 1"):
            CategoricalBelief(pmf={"a": 0.5, "b": 0.2})

    def test_argmax_and_entropy(self) -> None:
        # 設計書 §5.8 の inferred_event_type の例
        b = CategoricalBelief(pmf={"ent:StopCmd": 0.35, "ent:Fault": 0.56, "other": 0.09})
        assert b.argmax == "ent:Fault"
        assert b.entropy_nats > 0.0

    def test_uniform_has_max_entropy(self) -> None:
        uniform = CategoricalBelief(pmf={"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25})
        peaked = CategoricalBelief(pmf={"a": 0.97, "b": 0.01, "c": 0.01, "d": 0.01})
        assert uniform.entropy_nats > peaked.entropy_nats
        assert uniform.entropy_nats == pytest.approx(math.log(4))


class TestNumericBelief:
    def test_variance_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            NumericBelief(mean=1.0, var=-0.1)

    def test_std_is_sqrt_of_var(self) -> None:
        assert NumericBelief(mean=0.0, var=4.0).std == pytest.approx(2.0)

    def test_quantile_keys_must_be_probabilities(self) -> None:
        NumericBelief(mean=0.0, var=1.0, quantiles={"0.05": -1.64, "0.95": 1.64})
        with pytest.raises(ValidationError, match=r"\(0, 1\)"):
            NumericBelief(mean=0.0, var=1.0, quantiles={"1.5": 0.0})
        with pytest.raises(ValidationError, match="float string"):
            NumericBelief(mean=0.0, var=1.0, quantiles={"median": 0.0})


class TestUncertaintyDecomposition:
    def test_total_is_sum(self) -> None:
        """認識的・偶然的不確実性は分けて保持する（設計書 §5.6）。"""
        u = Uncertainty(epistemic=0.3, aleatoric=0.7)
        assert u.total == pytest.approx(1.0)

    def test_negative_uncertainty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Uncertainty(epistemic=-0.1, aleatoric=0.0)
