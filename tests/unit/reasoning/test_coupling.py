"""実体間結合（loopy BP）の単体テスト（設計書 §8.1）。

重点:

1. **集約の向きはオントロジーが決める** — ``Running`` は all、``Faulted`` は any。
2. **情報が運ばれる** — 部分の観測が全体の信念を動かし、その逆も動く。
3. **情報を作らない** — 事前の無いノードを 0.5 で埋めない。結合だけで信念を生まない。
4. **収束しなかったことを隠さない** — 振動している信念を確定値として返さない。
"""

from __future__ import annotations

import pytest

from sbp.core import vocab
from sbp.core.errors import ReasoningError
from sbp.reasoning.coupling import (
    AggregationRule,
    PartWhole,
    SituationLink,
    propagate,
)

LINE = "https://sbp.example/site/siteA/line/L-1"
PUMP_A = "https://sbp.example/site/siteA/asset/P-101"
PUMP_B = "https://sbp.example/site/siteA/asset/P-102"
SITUATION = "https://sbp.example/site/siteA/sit/s-1"
RUNNING = vocab.ent.Running
FAULTED = vocab.ent.Faulted


class TestAggregationRuleComesFromTheOntology:
    def test_the_individuals_map_to_rules(self) -> None:
        assert AggregationRule.from_iri(vocab.core.AggregateAll) is AggregationRule.ALL
        assert AggregationRule.from_iri(vocab.core.AggregateAny) is AggregationRule.ANY

    def test_an_unknown_rule_is_rejected(self) -> None:
        """コード側で勝手に集約規則を増やさない（オントロジーに宣言させる）。"""
        with pytest.raises(ReasoningError, match="unknown aggregation rule"):
            AggregationRule.from_iri("https://sbp.example/core#AggregateMost")

    def test_running_aggregates_as_all_and_faulted_as_any(self) -> None:
        """ドメインオントロジーが宣言している向き（0.4.0 で付与）。"""
        assert vocab.ent.Running in vocab.ALL_IRIS
        assert vocab.ent.Faulted in vocab.ALL_IRIS


class TestPartWholeCarriesInformation:
    def test_a_stopped_part_pulls_an_all_aggregated_whole_down(self) -> None:
        """ポンプが止まっていれば、ライン全体の「稼働中」も下がる（all 集約）。"""
        result = propagate(
            {(LINE, RUNNING): 0.90, (PUMP_A, RUNNING): 0.05, (PUMP_B, RUNNING): 0.95},
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                )
            ],
            max_iterations=10,
        )
        assert result.probability(LINE, RUNNING) < 0.90

    def test_a_faulted_part_pulls_an_any_aggregated_whole_up(self) -> None:
        """どこか一つ壊れていれば、系は壊れている（any 集約）。"""
        result = propagate(
            {(LINE, FAULTED): 0.10, (PUMP_A, FAULTED): 0.92, (PUMP_B, FAULTED): 0.03},
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=FAULTED,
                    rule=AggregationRule.ANY,
                )
            ],
            max_iterations=10,
        )
        assert result.probability(LINE, FAULTED) > 0.10

    def test_a_confident_whole_lifts_its_parts_under_all(self) -> None:
        """全体が稼働中なら部分も稼働中（all 集約の下向きの含意は強い）。"""
        result = propagate(
            {(LINE, RUNNING): 0.97, (PUMP_A, RUNNING): 0.55, (PUMP_B, RUNNING): 0.96},
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                )
            ],
            max_iterations=10,
        )
        assert result.probability(PUMP_A, RUNNING) > 0.55

    def test_agreement_barely_moves_anything(self) -> None:
        """全員が同じことを言っているときに信念を動かさない（結合は情報源ではない）。"""
        priors = {(LINE, RUNNING): 0.95, (PUMP_A, RUNNING): 0.96, (PUMP_B, RUNNING): 0.95}
        result = propagate(
            priors,
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                )
            ],
            max_iterations=10,
        )
        for key, before in priors.items():
            assert abs(result.posteriors[key] - before) < 0.2

    def test_strength_controls_how_much_moves(self) -> None:
        priors = {(LINE, RUNNING): 0.90, (PUMP_A, RUNNING): 0.05, (PUMP_B, RUNNING): 0.95}
        weak = propagate(
            priors,
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                    strength=0.1,
                )
            ],
            max_iterations=10,
        )
        strong = propagate(
            priors,
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                    strength=1.0,
                )
            ],
            max_iterations=10,
        )
        assert strong.probability(LINE, RUNNING) < weak.probability(LINE, RUNNING)


class TestSituationLink:
    def test_members_are_pulled_toward_each_other(self) -> None:
        """同じ作業に入っている設備の状態は揃いやすい（対称な引き寄せ）。"""
        result = propagate(
            {(PUMP_A, RUNNING): 0.10, (PUMP_B, RUNNING): 0.80},
            situations=[
                SituationLink(situation=SITUATION, members=(PUMP_A, PUMP_B), fluent=RUNNING)
            ],
            max_iterations=10,
        )
        assert result.probability(PUMP_A, RUNNING) > 0.10
        assert result.probability(PUMP_B, RUNNING) < 0.80

    def test_a_link_needs_two_members(self) -> None:
        with pytest.raises(ReasoningError, match="needs at least two"):
            SituationLink(situation=SITUATION, members=(PUMP_A,), fluent=RUNNING)


class TestItDoesNotInventBelief:
    def test_missing_priors_are_an_error(self) -> None:
        """欠けたノードを 0.5 で埋めると、結合が勝手に信念を作り出す。"""
        with pytest.raises(ReasoningError, match="no prior"):
            propagate(
                {(LINE, RUNNING): 0.9},
                part_wholes=[
                    PartWhole(
                        whole=LINE,
                        parts=(PUMP_A,),
                        fluent=RUNNING,
                        rule=AggregationRule.ALL,
                    )
                ],
            )

    def test_an_out_of_range_prior_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="must be a probability"):
            propagate({(LINE, RUNNING): 1.4})

    def test_no_couplings_means_no_change(self) -> None:
        priors = {(LINE, RUNNING): 0.73}
        result = propagate(priors, max_iterations=5)
        assert result.probability(LINE, RUNNING) == pytest.approx(0.73, abs=1e-6)
        assert result.converged

    def test_parts_are_required(self) -> None:
        with pytest.raises(ReasoningError, match="has no parts"):
            PartWhole(whole=LINE, parts=(), fluent=RUNNING, rule=AggregationRule.ALL)


class TestConvergenceIsReported:
    def test_a_converged_run_says_so(self) -> None:
        result = propagate(
            {(LINE, RUNNING): 0.9, (PUMP_A, RUNNING): 0.9, (PUMP_B, RUNNING): 0.9},
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                )
            ],
            max_iterations=50,
        )
        assert result.converged
        assert result.max_delta < 1e-3

    def test_a_truncated_run_is_not_claimed_converged(self) -> None:
        """反復上限で打ち切ったことを隠さない（振動を確定値として返さない）。"""
        result = propagate(
            {(LINE, RUNNING): 0.95, (PUMP_A, RUNNING): 0.02, (PUMP_B, RUNNING): 0.98},
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                    strength=1.0,
                )
            ],
            max_iterations=1,
            damping=1.0,
        )
        assert result.iterations == 1
        assert not result.converged

    def test_damping_must_be_positive(self) -> None:
        with pytest.raises(ReasoningError, match="damping must be in"):
            propagate({(LINE, RUNNING): 0.5}, damping=0.0)

    def test_messages_are_kept_for_diagnosis(self) -> None:
        result = propagate(
            {(LINE, RUNNING): 0.9, (PUMP_A, RUNNING): 0.1, (PUMP_B, RUNNING): 0.9},
            part_wholes=[
                PartWhole(
                    whole=LINE,
                    parts=(PUMP_A, PUMP_B),
                    fluent=RUNNING,
                    rule=AggregationRule.ALL,
                )
            ],
            max_iterations=3,
        )
        assert (LINE, PUMP_A, RUNNING) in result.messages
        assert result.messages[LINE, PUMP_A, RUNNING] != 0.0

    def test_unknown_node_lookup_fails_loudly(self) -> None:
        result = propagate({(LINE, RUNNING): 0.5})
        with pytest.raises(ReasoningError, match="no coupled belief"):
            result.probability(PUMP_A, RUNNING)
