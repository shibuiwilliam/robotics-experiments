"""様相（modality）— 事実の「身分」を表す格子（設計書 §3.5）。

様相は確からしさの順ではなく**事実性の身分**の順である。

- ``observed < inferred < predicted < planned`` が事実性の鎖。
- ``normative`` は直交（「こうあるべき」は事実性の鎖に乗らない）。
- ``hypothetical`` はシナリオ用名前付きグラフに隔離され、``promote_scenario()`` 以外で
  主信念に入らない（不変条件 3）。

プローブ・規則・LLM・エージェントの出力は必ず ``inferred``（不変条件 3）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from sbp.core.errors import ModalityError

__all__ = ["INFERENCE_OUTPUT_MODALITY", "MAIN_BELIEF_MODALITIES", "Modality"]


class Modality(StrEnum):
    """事実の身分。不変条件 3 によりこの 6 値のみ。"""

    OBSERVED = "observed"
    """センサまたは人が直接観測した。最上位の事実性。"""
    INFERRED = "inferred"
    """プローブ・規則・LLM・エージェント・信念更新の出力。"""
    PREDICTED = "predicted"
    """地平付きの予測（``horizon_origin`` と ``lead_time`` を必須とする）。"""
    PLANNED = "planned"
    """計画・スケジュール（こうする予定）。"""
    NORMATIVE = "normative"
    """規程・契約・安全規則（こうあるべき）。事実性の鎖と直交する。"""
    HYPOTHETICAL = "hypothetical"
    """What-if シナリオ。主信念グラフから隔離される。"""

    # ---- 格子としての性質 ----

    @property
    def factuality_rank(self) -> int | None:
        """事実性の順位。鎖に乗らない様相（``normative`` / ``hypothetical``）は ``None``。"""
        return _FACTUALITY_RANK.get(self)

    @property
    def is_on_factuality_chain(self) -> bool:
        """事実性の鎖（observed < inferred < predicted < planned）上にあるか。"""
        return self in _FACTUALITY_RANK

    @property
    def requires_horizon(self) -> bool:
        """``horizon_origin`` と ``lead_time`` が必須か（``predicted`` のみ真）。"""
        return self is Modality.PREDICTED

    @property
    def requires_scenario(self) -> bool:
        """``scenario`` が必須か（``hypothetical`` のみ真）。"""
        return self is Modality.HYPOTHETICAL

    @property
    def enters_main_belief(self) -> bool:
        """主信念グラフに入れてよい様相か。``hypothetical`` のみ偽（不変条件 3）。"""
        return self is not Modality.HYPOTHETICAL

    def precedes(self, other: Modality) -> bool:
        """事実性の鎖上で ``self`` が ``other`` より厳密に手前（より事実的）か。

        比較不能な組（鎖に乗らない様相を含む）では常に ``False`` を返す。半順序であり
        全順序ではないことに注意する。

        Args:
            other: 比較対象の様相。
        """
        a, b = self.factuality_rank, other.factuality_rank
        return a is not None and b is not None and a < b

    def comparable_with(self, other: Modality) -> bool:
        """事実性の鎖上で比較可能か。"""
        return self.is_on_factuality_chain and other.is_on_factuality_chain

    def assert_enters_main_belief(self) -> None:
        """主信念に入れられない様相なら :class:`ModalityError` を送出する。

        Raises:
            ModalityError: ``hypothetical`` を主信念に入れようとした場合（不変条件 3）。
        """
        if not self.enters_main_belief:
            raise ModalityError(
                "hypothetical assertions must stay in a scenario-named graph; "
                "use promote_scenario() to move them into main belief (invariant 3)"
            )


_FACTUALITY_RANK: Final[dict[Modality, int]] = {
    Modality.OBSERVED: 0,
    Modality.INFERRED: 1,
    Modality.PREDICTED: 2,
    Modality.PLANNED: 3,
}

MAIN_BELIEF_MODALITIES: Final[frozenset[Modality]] = frozenset(
    m for m in Modality if m.enters_main_belief
)
"""主信念グラフに入れてよい様相の集合（``hypothetical`` を除く全て）。"""

INFERENCE_OUTPUT_MODALITY: Final = Modality.INFERRED
"""プローブ・規則・LLM・エージェントの出力に使う様相（不変条件 3）。"""
