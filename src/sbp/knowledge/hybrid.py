"""ハイブリッドクエリの計画（設計書 §6.5、§6.6、M4-08）。

> ハイブリッドクエリ（グラフ述語 + 近傍）は ``sbp.knowledge.hybrid.plan()`` を通す。選択率で
> 押し下げ順を決める。

設計書 §6.6 の例は「9/12 時点で信じていた状況のうち、現在の状況に似ていて SLA 違反に至ったもの」
——**記号側の絞り込みと潜在側の近傍検索を組み合わせる**問い合わせである。

**なぜ計画を明示的に持つのか。** 素直に書くと「グラフで候補を出す → 全件の潜在を引いて並べる」
か「近傍を取る → グラフで濾す」のどちらかに固定される。前者はグラフ側の選択率が低いと潜在の
読み出しが爆発し、後者は近傍の上位 k にグラフ条件を満たすものが入っていないと**空を返す**
（しかもエラーにならない）。選択率を見て順序を決め、**どちらの順で実行したかを結果に残す**。

**k の押し上げ（over-fetch）を明示する。** 近傍先行の場合、グラフ条件で落ちる分を見込んで
``limit`` より多めに取る必要がある。何倍取ったかを計画に残さないと、「空だったのは条件に合う
ものが無いからか、k が足りなかったからか」が区別できない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sbp.core.errors import KnowledgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["HybridPlan", "PlanStrategy", "Selectivity", "plan"]

# ``sbp.knowledge`` からは ``plan_hybrid`` として公開する（最上位で ``plan`` だけでは
# 何の計画か分からないため）。モジュール内の名前は knowledge ルールの表記に合わせている。

log = get_logger(__name__)

_MAX_OVERFETCH = 20
"""近傍先行のときに押し上げる上限倍率。これを超えるならグラフ先行にすべきである。"""


class PlanStrategy(StrEnum):
    """実行順の選択。"""

    GRAPH_FIRST = "graph_first"
    """グラフで候補を絞ってから、その集合内で近傍を取る。"""
    VECTOR_FIRST = "vector_first"
    """近傍を広めに取ってから、グラフ条件で濾す。"""


@dataclass(frozen=True, slots=True)
class Selectivity:
    """絞り込みの効き方の見積り。

    Attributes:
        graph_candidates: グラフ述語を満たす実体の見込み件数。
        total_entities: 索引に入っている実体の総数。
        vector_rows: 近傍検索の対象になる潜在の行数。
    """

    graph_candidates: int
    total_entities: int
    vector_rows: int

    def __post_init__(self) -> None:
        """件数が負でないこと、候補が総数を超えないことを検証する。"""
        for name, value in (
            ("graph_candidates", self.graph_candidates),
            ("total_entities", self.total_entities),
            ("vector_rows", self.vector_rows),
        ):
            if value < 0:
                raise KnowledgeError(f"{name} must be non-negative, got {value}")
        if self.graph_candidates > self.total_entities:
            raise KnowledgeError(
                f"graph_candidates {self.graph_candidates} exceeds total_entities "
                f"{self.total_entities}; the estimate is inconsistent"
            )

    @property
    def graph_fraction(self) -> float:
        """グラフ述語の選択率（0 に近いほど絞れている）。"""
        if self.total_entities == 0:
            return 1.0
        return self.graph_candidates / self.total_entities


@dataclass(frozen=True, slots=True)
class HybridPlan:
    """ハイブリッドクエリの実行計画。

    Attributes:
        strategy: 実行順。
        limit: 最終的に返す件数。
        fetch: 近傍検索で取る件数（近傍先行では ``limit`` より多い）。
        entity_filter: グラフ先行で絞り込んだ実体（近傍検索に渡す）。
        selectivity: 判断に使った見積り。
        reason: なぜこの順にしたか（ログと説明に残す）。
    """

    strategy: PlanStrategy
    limit: int
    fetch: int
    entity_filter: tuple[str, ...]
    selectivity: Selectivity
    reason: str

    @property
    def overfetch_ratio(self) -> float:
        """``limit`` に対して何倍取るか。"""
        return self.fetch / self.limit if self.limit else 1.0

    @property
    def may_be_incomplete(self) -> bool:
        """結果が「条件に合うものが無い」ではなく「k 不足」で欠けうるか。

        近傍先行では常に真になる。呼び出し側はこれが真のとき、空や少ない結果を「該当なし」と
        報告してはならない（押し上げ倍率を上げて再試行するか、グラフ先行に切り替える）。
        """
        return self.strategy is PlanStrategy.VECTOR_FIRST


def plan(
    *,
    limit: int,
    selectivity: Selectivity,
    graph_candidates: Sequence[str] = (),
    graph_first_threshold: float = 0.1,
    max_entity_filter: int = 500,
) -> HybridPlan:
    """選択率から実行順と取得件数を決める。

    判断は 2 段である。

    1. グラフ述語が十分に絞れている（選択率 ≤ ``graph_first_threshold``）ならグラフ先行。
       実体の集合を近傍検索の ``WHERE entity IN (...)`` に渡せるので、取り逃しが無い。
    2. ただし候補が多すぎる（``max_entity_filter`` 超）ならグラフ先行でも SQL が巨大になる。
       このときは近傍先行に切り替え、押し上げ倍率で取り逃しを補う。

    Args:
        limit: 返す件数。
        selectivity: 選択率の見積り。
        graph_candidates: グラフ述語を満たす実体 IRI（分かっていれば渡す）。
        graph_first_threshold: グラフ先行にする選択率の上限。
        max_entity_filter: ``entity IN (...)`` に入れる上限。

    Returns:
        実行計画。

    Raises:
        KnowledgeError: ``limit`` が正でない、または閾値が 0〜1 の外の場合。
    """
    if limit < 1:
        raise KnowledgeError(f"limit must be at least 1, got {limit}")
    if not 0.0 < graph_first_threshold <= 1.0:
        raise KnowledgeError(
            f"graph_first_threshold must be in (0, 1], got {graph_first_threshold}"
        )

    fraction = selectivity.graph_fraction
    candidates = tuple(graph_candidates)

    if candidates and fraction <= graph_first_threshold and len(candidates) <= max_entity_filter:
        chosen = HybridPlan(
            strategy=PlanStrategy.GRAPH_FIRST,
            limit=limit,
            fetch=limit,
            entity_filter=candidates,
            selectivity=selectivity,
            reason=(
                f"グラフ述語が選択率 {fraction:.3f} で絞れており、候補 {len(candidates)} 件を"
                "近傍検索に渡せる（取り逃しなし）"
            ),
        )
    else:
        # 押し上げ倍率は選択率の逆数。条件を満たす割合が 1/10 なら 10 倍取る。上限で打ち切る。
        ratio = min(_MAX_OVERFETCH, max(1.0, 1.0 / max(fraction, 1e-6)))
        if not candidates:
            why = "グラフ側の候補が与えられていない"
        elif len(candidates) > max_entity_filter:
            why = (
                f"候補が {len(candidates)} 件で entity フィルタの上限 {max_entity_filter} を超える"
            )
        else:
            why = f"グラフ述語の選択率 {fraction:.3f} が閾値 {graph_first_threshold} を超える"
        chosen = HybridPlan(
            strategy=PlanStrategy.VECTOR_FIRST,
            limit=limit,
            fetch=int(limit * ratio),
            entity_filter=candidates[:max_entity_filter],
            selectivity=selectivity,
            reason=f"{why}ため近傍先行。押し上げ {ratio:.1f} 倍で取り逃しを補う",
        )

    log.debug(
        "knowledge.hybrid_planned",
        strategy=chosen.strategy.value,
        limit=limit,
        fetch=chosen.fetch,
        graph_fraction=fraction,
        may_be_incomplete=chosen.may_be_incomplete,
    )
    return chosen
