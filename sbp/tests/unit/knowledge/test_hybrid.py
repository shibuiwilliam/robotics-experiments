"""ハイブリッドクエリ計画の単体テスト（設計書 §6.5、§6.6）。

重点:

1. **絞れているならグラフ先行** — 取り逃しが無い経路を選ぶ。
2. **絞れていない / 候補が多すぎるなら近傍先行 + 押し上げ** — SQL を巨大にしない。
3. **近傍先行では「k 不足で欠けうる」ことを結果が明示する** — 空を「該当なし」と誤報しない。
4. **押し上げ倍率に上限がある** — 際限なく取ると近傍検索の意味が消える。
"""

from __future__ import annotations

import pytest

from sbp.core.errors import KnowledgeError
from sbp.knowledge import PlanStrategy, Selectivity, plan_hybrid


def _selectivity(candidates: int, total: int = 1000, rows: int = 50_000) -> Selectivity:
    return Selectivity(graph_candidates=candidates, total_entities=total, vector_rows=rows)


class TestSelectivity:
    def test_the_fraction_is_candidates_over_total(self) -> None:
        assert _selectivity(10).graph_fraction == pytest.approx(0.01)

    def test_an_empty_index_is_treated_as_unselective(self) -> None:
        """総数 0 で「絞れている」と判断すると、空の索引でグラフ先行に倒れる。"""
        assert (
            Selectivity(graph_candidates=0, total_entities=0, vector_rows=0).graph_fraction == 1.0
        )

    def test_negative_counts_are_rejected(self) -> None:
        with pytest.raises(KnowledgeError, match="must be non-negative"):
            Selectivity(graph_candidates=-1, total_entities=10, vector_rows=0)

    def test_candidates_cannot_exceed_the_total(self) -> None:
        with pytest.raises(KnowledgeError, match="exceeds total_entities"):
            Selectivity(graph_candidates=20, total_entities=10, vector_rows=0)


class TestStrategyChoice:
    def test_a_selective_predicate_goes_graph_first(self) -> None:
        plan = plan_hybrid(limit=5, selectivity=_selectivity(3), graph_candidates=["a", "b", "c"])
        assert plan.strategy is PlanStrategy.GRAPH_FIRST
        assert plan.entity_filter == ("a", "b", "c")
        assert plan.fetch == 5
        assert not plan.may_be_incomplete

    def test_an_unselective_predicate_goes_vector_first(self) -> None:
        plan = plan_hybrid(
            limit=5,
            selectivity=_selectivity(900),
            graph_candidates=[f"e{index}" for index in range(900)],
        )
        assert plan.strategy is PlanStrategy.VECTOR_FIRST
        assert plan.may_be_incomplete

    def test_without_graph_candidates_it_goes_vector_first(self) -> None:
        """候補が分からないのにグラフ先行にすると、何で絞るのかが無い。"""
        plan = plan_hybrid(limit=5, selectivity=_selectivity(3))
        assert plan.strategy is PlanStrategy.VECTOR_FIRST
        assert "候補が与えられていない" in plan.reason

    def test_too_many_candidates_fall_back_to_vector_first(self) -> None:
        """グラフ先行でも entity IN (...) が巨大になるなら意味がない。"""
        plan = plan_hybrid(
            limit=5,
            selectivity=_selectivity(600),
            graph_candidates=[f"e{index}" for index in range(600)],
            max_entity_filter=500,
        )
        assert plan.strategy is PlanStrategy.VECTOR_FIRST
        assert len(plan.entity_filter) == 500
        assert "上限" in plan.reason


class TestOverfetch:
    def test_a_rare_predicate_fetches_far_more_than_the_limit(self) -> None:
        """1% しか通らないなら、limit 件そろえるには 100 件近く見る必要がある。"""
        plan = plan_hybrid(limit=10, selectivity=_selectivity(10))
        assert plan.fetch > plan.limit
        assert plan.overfetch_ratio > 5.0

    def test_a_common_predicate_barely_overfetches(self) -> None:
        """9 割通るなら押し上げはほとんど要らない。"""
        plan = plan_hybrid(limit=10, selectivity=_selectivity(900))
        assert plan.overfetch_ratio < 2.0

    def test_the_overfetch_is_capped(self) -> None:
        """際限なく取ると近傍検索ではなく全走査になる。"""
        plan = plan_hybrid(limit=10, selectivity=_selectivity(1, total=1_000_000))
        assert plan.overfetch_ratio <= 20.0

    def test_the_reason_is_recorded(self) -> None:
        """なぜこの順にしたかが残らないと、遅いクエリの原因を追えない。"""
        plan = plan_hybrid(limit=5, selectivity=_selectivity(3), graph_candidates=["a"])
        assert plan.reason
        assert "選択率" in plan.reason


class TestArgumentValidation:
    def test_a_non_positive_limit_is_rejected(self) -> None:
        with pytest.raises(KnowledgeError, match="limit must be at least 1"):
            plan_hybrid(limit=0, selectivity=_selectivity(3))

    @pytest.mark.parametrize("threshold", [0.0, 1.5])
    def test_an_out_of_range_threshold_is_rejected(self, threshold: float) -> None:
        with pytest.raises(KnowledgeError, match="graph_first_threshold"):
            plan_hybrid(limit=5, selectivity=_selectivity(3), graph_first_threshold=threshold)
