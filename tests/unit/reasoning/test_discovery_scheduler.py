"""概念発見ループとエピステミック・スケジューラの単体テスト（設計書 §8.6、§8.7、M6-04・M6-05）。

重点:

1. **LLM の提案を自動採用しない** — curation を経ないとオントロジーに入らない。
2. **既存概念で説明できるなら提案を作れない** — `rejected_alternatives` を必須にする。
3. **劣モジュラ性が重複した観測を落とす** — 同じことを 2 度確かめるバッチを作らない。
4. **人への質問に上限がある** — 質問が多すぎると現場が答えなくなり、情報が減る。
5. **EIG は不確実性が最大のときに最大** — 「確信していることを確かめる」を上位にしない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.core import vocab
from sbp.core.errors import ReasoningError
from sbp.reasoning import (
    ConceptProposal,
    DiscoverySignal,
    DiscoveryTrigger,
    HumanQueryLog,
    ObservationQuery,
    ProposalStatus,
    QueryKind,
    SchedulerBudget,
    cluster_latents,
    expected_information_gain,
    find_low_coverage_clusters,
    repeated_discrepancy_signals,
    residual_persistence_signals,
    schedule,
)

T0 = datetime(2026, 10, 3, 10, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _two_groups() -> tuple[np.ndarray, np.ndarray]:
    """プローブが反応する群と反応しない群。"""
    rng = np.random.default_rng(1)
    covered = rng.normal(0.0, 0.1, size=(30, 4))
    uncovered = rng.normal(5.0, 0.1, size=(20, 4))
    latents = np.vstack([covered, uncovered])
    activations = np.concatenate([np.full(30, 0.9), np.full(20, 0.05)])
    return latents, activations


def _signal() -> DiscoverySignal:
    return DiscoverySignal(
        trigger=DiscoveryTrigger.LOW_COVERAGE_CLUSTER,
        entities=(PUMP,),
        strength=0.7,
        evidence=("cluster:1",),
        note="20 標本の高密度クラスタで活性 0.05",
    )


class TestClustering:
    def test_it_separates_the_two_groups(self) -> None:
        latents, activations = _two_groups()
        clusters = cluster_latents(latents, activations, radius=1.0)
        assert len(clusters) == 2
        assert {cluster.size for cluster in clusters} == {30, 20}

    def test_small_groups_are_dropped(self) -> None:
        """1 回しか起きていない現象に名前を付けない。"""
        latents = np.array([[0.0, 0.0], [10.0, 10.0]])
        clusters = cluster_latents(latents, np.array([0.1, 0.1]), radius=0.5, min_size=5)
        assert clusters == ()

    def test_mismatched_shapes_are_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="but 2 activations"):
            cluster_latents(np.zeros((5, 2)), np.zeros(2), radius=1.0)

    def test_a_non_positive_radius_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="radius must be positive"):
            cluster_latents(np.zeros((5, 2)), np.zeros(5), radius=0.0)


class TestLowCoverageDetection:
    def test_only_the_silent_cluster_becomes_a_signal(self) -> None:
        """プローブが反応しない領域はどの指標にも現れない。密度と活性を同時に見る。"""
        latents, activations = _two_groups()
        clusters = cluster_latents(latents, activations, radius=1.0)
        signals = find_low_coverage_clusters(clusters, entity=PUMP)
        assert len(signals) == 1
        assert signals[0].cluster is not None
        assert signals[0].cluster.size == 20
        assert signals[0].trigger is DiscoveryTrigger.LOW_COVERAGE_CLUSTER

    def test_a_covered_cluster_produces_nothing(self) -> None:
        latents = np.random.default_rng(2).normal(0.0, 0.1, size=(20, 3))
        clusters = cluster_latents(latents, np.full(20, 0.95), radius=1.0)
        assert find_low_coverage_clusters(clusters, entity=PUMP) == ()

    def test_no_clusters_means_no_signals(self) -> None:
        assert find_low_coverage_clusters((), entity=PUMP) == ()


class TestResidualPersistence:
    def test_a_sustained_run_becomes_a_signal(self) -> None:
        signals = residual_persistence_signals(
            [0.01] * 5 + [0.4] * 6 + [0.02] * 5, entity=PUMP, threshold=0.2
        )
        assert len(signals) == 1
        assert signals[0].trigger is DiscoveryTrigger.ALIGNMENT_RESIDUAL

    def test_a_brief_spike_does_not(self) -> None:
        """残差は測定値であり揺らぐ。持続が証拠になる。"""
        assert (
            residual_persistence_signals(
                [0.01] * 5 + [0.4] * 2 + [0.01] * 5, entity=PUMP, threshold=0.2, min_run=3
            )
            == ()
        )

    def test_an_invalid_min_run_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="min_run must be at least 1"):
            residual_persistence_signals([0.5], entity=PUMP, threshold=0.2, min_run=0)


class TestRepeatedDiscrepancies:
    def test_repeats_become_a_signal(self) -> None:
        signals = repeated_discrepancy_signals(
            [(PUMP, 2, "physical_correct")] * 2 + [(PUMP, 2, "both_wrong")] * 2
        )
        assert len(signals) == 1
        assert "4 回" in signals[0].note

    def test_both_wrong_weighs_more(self) -> None:
        """「両方誤り」は既存語彙では説明できなかった可能性が高い。"""
        plain = repeated_discrepancy_signals([(PUMP, 2, "physical_correct")] * 3)
        mixed = repeated_discrepancy_signals(
            [(PUMP, 2, "physical_correct")] + [(PUMP, 2, "both_wrong")] * 2
        )
        assert mixed[0].strength > plain[0].strength

    def test_few_repeats_produce_nothing(self) -> None:
        assert repeated_discrepancy_signals([(PUMP, 2, "physical_correct")] * 2) == ()

    def test_an_invalid_min_repeats_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="min_repeats must be at least 2"):
            repeated_discrepancy_signals([(PUMP, 2, "x")], min_repeats=1)


class TestConceptProposal:
    def _proposal(self, **overrides: object) -> ConceptProposal:
        values: dict[str, object] = {
            "name": "CavitationOnset",
            "definition": "ポンプ内でキャビテーションが発生している状態",
            "superclass": "FluentType",
            "rejected_alternatives": ("VibrationAnomaly は流体の挙動を区別しない",),
            "signals": (_signal(),),
            "proposed_at": T0,
            "llm_provenance": {
                "provider": "replay",
                "model": "fixture",
                "prompt_version": "1.0.0@h",
            },
        }
        values.update(overrides)
        return ConceptProposal(**values)  # type: ignore[arg-type]

    def test_a_proposal_starts_unadopted(self) -> None:
        """LLM の提案を自動採用しない（設計書 §8.6 の段階 4）。"""
        proposal = self._proposal()
        assert proposal.status is ProposalStatus.PROPOSED
        assert not proposal.needs_ontology_bump

    def test_it_must_say_which_existing_concepts_fall_short(self) -> None:
        """既存で済むなら新しい概念は要らない。"""
        with pytest.raises(ReasoningError, match="does not say which existing concepts fall short"):
            self._proposal(rejected_alternatives=())

    def test_it_must_cite_a_signal(self) -> None:
        """データからの証拠が無い提案は推測である。"""
        with pytest.raises(ReasoningError, match="cites no discovery signal"):
            self._proposal(signals=())

    def test_a_nameless_proposal_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="needs both a name and a definition"):
            self._proposal(name="")

    def test_acceptance_triggers_an_ontology_bump(self) -> None:
        accepted = self._proposal().curate(ProposalStatus.ACCEPTED, note="定義は妥当")
        assert accepted.needs_ontology_bump
        assert accepted.curation_note == "定義は妥当"

    def test_rejection_requires_a_reason(self) -> None:
        """理由が残らないと同じ提案が次の発見サイクルで繰り返される。"""
        with pytest.raises(ReasoningError, match="curation requires a note"):
            self._proposal().curate(ProposalStatus.REJECTED, note="")

    def test_a_revision_carries_the_new_definition(self) -> None:
        revised = self._proposal().curate(
            ProposalStatus.REVISED,
            note="定義を限定した",
            revised_definition="吸込圧低下に伴う気泡発生",
        )
        assert revised.definition == "吸込圧低下に伴う気泡発生"
        assert revised.needs_ontology_bump

    def test_a_revision_without_a_definition_is_refused(self) -> None:
        with pytest.raises(ReasoningError, match="must carry the revised definition"):
            self._proposal().curate(ProposalStatus.REVISED, note="修正した")

    def test_curation_happens_once(self) -> None:
        accepted = self._proposal().curate(ProposalStatus.ACCEPTED, note="採用")
        with pytest.raises(ReasoningError, match="already accepted"):
            accepted.curate(ProposalStatus.REJECTED, note="やめる")

    def test_the_llm_provenance_is_kept(self) -> None:
        """誰の提案かが残らないと、提案の質をプロバイダ別に評価できない。"""
        assert self._proposal().llm_provenance["prompt_version"] == "1.0.0@h"


class TestExpectedInformationGain:
    def test_it_peaks_at_maximum_uncertainty(self) -> None:
        """すでに確信していることを観測しても得るものは少ない。"""
        assert expected_information_gain(0.5) == pytest.approx(1.0)
        assert expected_information_gain(0.95) < 0.5
        assert expected_information_gain(0.5) > expected_information_gain(0.8)

    def test_a_less_reliable_observation_gains_less(self) -> None:
        assert expected_information_gain(0.5, reliability=0.6) < expected_information_gain(
            0.5, reliability=1.0
        )

    def test_an_anti_informative_reliability_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="anti-informative"):
            expected_information_gain(0.5, reliability=0.2)


class TestScheduling:
    def _query(
        self,
        query_id: str,
        *,
        kind: QueryKind = QueryKind.SAMPLING_RATE,
        fluent: str = vocab.ent.Running,
        prior: float = 0.5,
        value: float = 1.0,
        cost: float = 0.05,
    ) -> ObservationQuery:
        return ObservationQuery(
            query_id=query_id,
            kind=kind,
            entity=PUMP,
            fluents=(fluent,),
            prior={fluent: prior},
            value={fluent: value},
            cost=cost,
        )

    def test_submodularity_discounts_the_duplicate(self) -> None:
        """同じフルーエントを狙う 2 つ目は、1 つ目で減った分しか得られない。"""
        decision = schedule(
            [self._query("first"), self._query("second")],
            budget=SchedulerBudget(total_cost=1.0),
            at=T0,
        )
        assert [query.query_id for query in decision.chosen] == ["first", "second"]
        # 2 つ目の効用は 1 つ目より大幅に小さい（複利で減衰する）
        assert decision.utilities[1] < decision.utilities[0] / 5.0

    def test_a_costly_duplicate_is_not_worth_running(self) -> None:
        """残存エントロピーがコストを下回れば、2 つ目は実行しない。"""
        decision = schedule(
            [self._query("first", cost=0.05), self._query("second", cost=0.2)],
            budget=SchedulerBudget(total_cost=1.0),
            at=T0,
        )
        assert [query.query_id for query in decision.chosen] == ["first"]

    def test_a_duplicate_human_question_is_held_by_the_limit(self) -> None:
        """人への重複質問を止めるのは劣モジュラ性ではなく質問頻度の上限である。"""
        decision = schedule(
            [
                self._query("ask-1", kind=QueryKind.HUMAN_QUESTION),
                self._query("ask-2", kind=QueryKind.HUMAN_QUESTION),
            ],
            budget=SchedulerBudget(total_cost=1.0, max_human_queries=1),
            at=T0,
        )
        assert [query.query_id for query in decision.chosen] == ["ask-1"]
        assert decision.skipped_for_human_limit == ("ask-2",)

    def test_different_fluents_are_both_chosen(self) -> None:
        decision = schedule(
            [self._query("running"), self._query("anomaly", fluent=vocab.ent.VibrationAnomaly)],
            budget=SchedulerBudget(total_cost=1.0),
            at=T0,
        )
        assert len(decision.chosen) == 2

    def test_a_certain_fluent_is_deprioritised(self) -> None:
        """不確実性の高い順ではなく、効用で並べる。"""
        decision = schedule(
            [
                self._query("certain", fluent=vocab.ent.Present, prior=0.99),
                self._query("uncertain", fluent=vocab.ent.Running, prior=0.5),
            ],
            budget=SchedulerBudget(total_cost=1.0),
            at=T0,
        )
        assert decision.chosen[0].query_id == "uncertain"

    def test_a_low_value_fluent_is_deprioritised(self) -> None:
        """高い不確実性でも業務価値が低ければ後回しでよい。"""
        decision = schedule(
            [
                self._query("cheap_but_worthless", fluent=vocab.ent.Present, value=0.05),
                self._query("valuable", fluent=vocab.ent.Running, value=1.0),
            ],
            budget=SchedulerBudget(total_cost=1.0),
            at=T0,
        )
        assert decision.chosen[0].query_id == "valuable"

    def test_negative_utility_queries_are_not_run(self) -> None:
        """情報利得がコストを下回る観測は実行しない。"""
        decision = schedule(
            [self._query("expensive", cost=99.0)],
            budget=SchedulerBudget(total_cost=1000.0),
            at=T0,
        )
        assert decision.chosen == ()
        assert "何も実行しない" in decision.explain()

    def test_the_human_question_limit_holds(self) -> None:
        """質問が多すぎると現場が答えなくなり、上限を外すほうが情報が減る。"""
        decision = schedule(
            [
                self._query("ask", kind=QueryKind.HUMAN_QUESTION),
                self._query("sensor", kind=QueryKind.SAMPLING_RATE, fluent=vocab.ent.Faulted),
            ],
            budget=SchedulerBudget(total_cost=1.0, max_human_queries=0),
            at=T0,
        )
        assert [query.query_id for query in decision.chosen] == ["sensor"]
        assert decision.skipped_for_human_limit == ("ask",)

    def test_recent_questions_count_against_the_limit(self) -> None:
        decision = schedule(
            [self._query("ask", kind=QueryKind.HUMAN_QUESTION)],
            budget=SchedulerBudget(total_cost=1.0, max_human_queries=1),
            recent_human_queries=[T0 - timedelta(minutes=10)],
            at=T0,
        )
        assert decision.chosen == ()
        assert decision.skipped_for_human_limit == ("ask",)

    def test_questions_outside_the_window_do_not_count(self) -> None:
        decision = schedule(
            [self._query("ask", kind=QueryKind.HUMAN_QUESTION)],
            budget=SchedulerBudget(total_cost=1.0, max_human_queries=1),
            recent_human_queries=[T0 - timedelta(hours=3)],
            at=T0,
        )
        assert [query.query_id for query in decision.chosen] == ["ask"]

    def test_the_budget_caps_the_batch(self) -> None:
        decision = schedule(
            [
                self._query("cheap", fluent=vocab.ent.Running, cost=0.02),
                self._query("pricey", fluent=vocab.ent.Faulted, cost=0.5),
            ],
            budget=SchedulerBudget(total_cost=0.05),
            at=T0,
        )
        assert [query.query_id for query in decision.chosen] == ["cheap"]
        assert decision.skipped_for_budget == ("pricey",)

    def test_the_decision_explains_itself(self) -> None:
        decision = schedule([self._query("first")], budget=SchedulerBudget(total_cost=1.0), at=T0)
        assert "効用" in decision.explain()
        assert "コスト" in decision.explain()

    def test_a_query_without_a_prior_is_refused(self) -> None:
        """効用が黙って 0 として扱われるのを防ぐ。"""
        with pytest.raises(ReasoningError, match="has no prior/value"):
            ObservationQuery(
                query_id="broken",
                kind=QueryKind.SAMPLING_RATE,
                entity=PUMP,
                fluents=(vocab.ent.Running,),
                prior={},
                value={vocab.ent.Running: 1.0},
            )

    def test_an_invalid_budget_is_refused(self) -> None:
        with pytest.raises(ReasoningError, match="total_cost must be positive"):
            SchedulerBudget(total_cost=0.0)

    def test_which_kinds_ask_a_human(self) -> None:
        assert QueryKind.HUMAN_QUESTION.asks_a_human
        assert QueryKind.PARTIAL_STOCKTAKE.asks_a_human
        assert not QueryKind.SAMPLING_RATE.asks_a_human


class TestHumanQueryLog:
    def test_it_counts_within_the_window(self) -> None:
        log = HumanQueryLog()
        log.record(at=T0 - timedelta(minutes=30))
        log.record(at=T0 - timedelta(hours=5))
        assert log.count_within(window=timedelta(hours=1), at=T0) == 1
