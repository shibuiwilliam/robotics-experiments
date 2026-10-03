"""エピステミック・スケジューラ（設計書 §8.7、M6-05）。

> 効用 ``U(q) = Σ_x v(x) · EIG_q(x) − c(q)``。EIG は決定関連フルーエントの認識的不確実性の
> 期待削減量。選択は貪欲 + 劣モジュラ近似でバッチ選択。

**「何を観測すれば最も分かるか」を効用で決める。** 不確実性が高いものを順に潰すのではない——
高い不確実性でも業務価値が低ければ後回しでよく、低い不確実性でも決定に直結するなら先に確かめる
べきである。だから ``v(x)``（業務価値）と ``EIG``（情報利得）の積を取り、コストを引く。

**劣モジュラ性を使うのが要点である。** 同じフルーエントを狙う 2 つの観測は、2 つ目の価値が
1 つ目より小さい（最初の観測で不確実性が減っているため）。これを無視して効用の上位から選ぶと、
**同じことを 2 度確かめるバッチ**ができる。

実装は**残存エントロピーを追跡する**。割引係数を足し引きするのではなく、選んだ観測の後に残る
エントロピーを計算し、次の観測の EIG をそこから出す。こうすると減衰が複利で正しく効き、
「信頼度 0.95 の観測を 2 回」が「1 回の 2 倍の情報」にならない。

**ただし十分に安い重複観測は効用が正のまま残る。** 信頼度 0.95 の観測後も残存エントロピーは
0 ではないので、コストが極小ならもう 1 回測る価値はある——これは正しい挙動である。人への
重複質問を止めるのは劣モジュラ性ではなく**質問頻度の上限**の役目である。

**人への質問には頻度上限がある**（設計書 §8.7、``max_human_queries_per_hour``）。上限を
超える質問は効用が高くても選ばない。質問が多すぎると現場が答えなくなり、**上限を外すほうが
情報が減る**。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ObservationQuery",
    "QueryKind",
    "ScheduleDecision",
    "SchedulerBudget",
    "expected_information_gain",
    "schedule",
]

log = get_logger(__name__)


class QueryKind(StrEnum):
    """観測クエリの種類（設計書 §8.7 の候補クエリ）。"""

    SAMPLING_RATE = "sampling_rate"
    """センサのサンプリング率変更。安く、帯域だけを使う。"""
    CAMERA_AIM = "camera_aim"
    """PTZ カメラの向き変更。安全（監視義務）の制約が付く。"""
    TEMPORARY_SENSOR = "temporary_sensor"
    """追加センサの一時投入。高いが情報量が大きい。"""
    HUMAN_QUESTION = "human_question"
    """人への確認質問。**頻度上限の対象**。"""
    RECORD_RECHECK = "record_recheck"
    """記録の再照合。業務側の手間がかかる。"""
    PARTIAL_STOCKTAKE = "partial_stocktake"
    """部分棚卸。存在不一致（類型 1）に直接効く。"""

    @property
    def asks_a_human(self) -> bool:
        """人の手を取るか（頻度上限の対象）。"""
        return self in {
            QueryKind.HUMAN_QUESTION,
            QueryKind.RECORD_RECHECK,
            QueryKind.PARTIAL_STOCKTAKE,
        }


def expected_information_gain(prior: float, *, reliability: float = 1.0) -> float:
    """認識的不確実性の期待削減量（bit、設計書 §8.7）。

    観測前のエントロピーから、観測後の期待エントロピーを引く。観測が完全（``reliability=1``）なら
    期待エントロピーは 0 で、EIG は事前のエントロピーそのものになる。

    **不確実性が最大（0.5）のときに EIG が最大になる。** すでに確信していること（0.95）を
    観測しても得るものは少ない——これが「不確実性の高い順」ではなく EIG で測る理由である。

    Args:
        prior: 観測前の確率。
        reliability: 観測の信頼度（0.5 で無情報、1.0 で完全）。

    Returns:
        期待情報利得（bit、0 以上）。

    Raises:
        ReasoningError: 確率・信頼度が範囲外の場合。
    """
    if not 0.0 <= prior <= 1.0:
        raise ReasoningError(f"prior must be a probability, got {prior}")
    if not 0.5 <= reliability <= 1.0:
        raise ReasoningError(
            f"reliability must be in [0.5, 1.0] (below 0.5 the observation is "
            f"anti-informative, which means its polarity is wrong), got {reliability}"
        )
    prior_entropy = _entropy(prior)
    # 観測の信頼度が低いほど、観測後も不確実性が残る
    posterior_entropy = prior_entropy * (1.0 - (2.0 * reliability - 1.0))
    return max(0.0, prior_entropy - posterior_entropy)


def _entropy(probability: float) -> float:
    """ベルヌーイ分布の 2 値エントロピー（bit）。"""
    epsilon = 1e-12
    p = min(max(probability, epsilon), 1.0 - epsilon)
    q = 1.0 - p
    return -(p * math.log2(p) + q * math.log2(q))


@dataclass(frozen=True, slots=True)
class ObservationQuery:
    """候補となる観測 1 件。

    Attributes:
        query_id: 識別子。
        kind: 種類。
        entity: 対象実体の IRI。
        fluents: この観測で不確実性が減るフルーエント型 IRI。
        prior: 各フルーエントの観測前確率。
        value: 各フルーエントの業務価値（``v(x)``。正規化済み 0〜1）。
        reliability: 観測の信頼度。
        cost: 実行コスト（効用と同じ尺度）。
        reason: なぜこの観測を考えたか（不一致 ID、未決の期待など）。
    """

    query_id: str
    kind: QueryKind
    entity: str
    fluents: tuple[str, ...]
    prior: dict[str, float]
    value: dict[str, float]
    reliability: float = 0.95
    cost: float = 0.1
    reason: str = ""

    def __post_init__(self) -> None:
        """フルーエントと事前・価値の対応を検証する。"""
        if not self.fluents:
            raise ReasoningError(f"query {self.query_id} targets no fluent")
        missing = [
            fluent
            for fluent in self.fluents
            if fluent not in self.prior or fluent not in self.value
        ]
        if missing:
            raise ReasoningError(
                f"query {self.query_id} has no prior/value for {missing}; the utility would "
                "silently treat them as zero"
            )
        if self.cost < 0.0:
            raise ReasoningError(f"query {self.query_id} has negative cost {self.cost}")

    def utility(self, *, remaining_entropy: dict[str, float] | None = None) -> float:
        """効用 ``Σ v(x)·EIG(x) − cost``（設計書 §8.7）。

        ``remaining_entropy`` は「すでに選んだ観測の後に残っているエントロピー」。渡されていない
        フルーエントは事前のエントロピーをそのまま使う。劣モジュラ性をこれで表す——同じ
        フルーエントを狙う 2 つ目の観測は、1 つ目で減った分しか得られない。

        Args:
            remaining_entropy: フルーエント IRI → 残存エントロピー（bit）。
        """
        residual = remaining_entropy or {}
        informativeness = 2.0 * self.reliability - 1.0
        gain = 0.0
        for fluent in self.fluents:
            entropy = residual.get(fluent, _entropy(self.prior[fluent]))
            gain += self.value[fluent] * entropy * informativeness
        return gain - self.cost

    def entropy_after(
        self, *, remaining_entropy: dict[str, float] | None = None
    ) -> dict[str, float]:
        """この観測を実行した後の残存エントロピー。

        Args:
            remaining_entropy: 実行前の残存エントロピー。
        """
        residual = remaining_entropy or {}
        informativeness = 2.0 * self.reliability - 1.0
        return {
            fluent: residual.get(fluent, _entropy(self.prior[fluent])) * (1.0 - informativeness)
            for fluent in self.fluents
        }


@dataclass(frozen=True, slots=True)
class SchedulerBudget:
    """バッチ選択の制約（設計書 §8.7）。

    Attributes:
        total_cost: 使えるコストの合計。
        max_queries: 選ぶ件数の上限。
        max_human_queries: 人に向ける質問の上限（``max_human_queries_per_hour`` 由来）。
        window: 人への質問の上限を見る時間窓。
    """

    total_cost: float
    max_queries: int = 10
    max_human_queries: int = 4
    window: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        """予算が正であることを検証する。"""
        if self.total_cost <= 0.0:
            raise ReasoningError(f"total_cost must be positive, got {self.total_cost}")
        if self.max_queries < 1:
            raise ReasoningError(f"max_queries must be at least 1, got {self.max_queries}")
        if self.max_human_queries < 0:
            raise ReasoningError(
                f"max_human_queries must be non-negative, got {self.max_human_queries}"
            )


@dataclass(frozen=True, slots=True)
class ScheduleDecision:
    """バッチ選択の結果。

    Attributes:
        chosen: 選んだ観測（選んだ順）。
        utilities: 選択時点での効用（割引後）。
        skipped_for_budget: 予算で落ちたもの。
        skipped_for_human_limit: 人への質問上限で落ちたもの。
        spent: 使ったコスト。
    """

    chosen: tuple[ObservationQuery, ...]
    utilities: tuple[float, ...]
    skipped_for_budget: tuple[str, ...] = ()
    skipped_for_human_limit: tuple[str, ...] = ()
    spent: float = 0.0

    @property
    def human_queries(self) -> int:
        """選んだもののうち人に向けた件数。"""
        return sum(1 for query in self.chosen if query.kind.asks_a_human)

    def explain(self) -> str:
        """人が読む説明（なぜこのバッチになったか）。"""
        if not self.chosen:
            return "効用が正の観測が無いため、何も実行しない"
        parts = [
            f"{query.query_id}（{query.kind.value}, 効用 {utility:.4f}）"
            for query, utility in zip(self.chosen, self.utilities, strict=True)
        ]
        tail = ""
        if self.skipped_for_human_limit:
            tail += f" / 人への質問上限で保留: {list(self.skipped_for_human_limit)}"
        if self.skipped_for_budget:
            tail += f" / 予算で保留: {list(self.skipped_for_budget)}"
        return "選択: " + ", ".join(parts) + f"（コスト {self.spent:.3f}）" + tail


def schedule(
    candidates: Sequence[ObservationQuery],
    *,
    budget: SchedulerBudget,
    recent_human_queries: Sequence[datetime] = (),
    at: datetime,
) -> ScheduleDecision:
    """貪欲 + 劣モジュラ近似でバッチを選ぶ（設計書 §8.7）。

    各反復で**割引後の**効用が最大のものを選び、選んだ観測が覆ったフルーエントの割引を更新する。
    効用が 0 以下になったら止める——情報利得がコストを下回る観測は実行しない。

    Args:
        candidates: 候補の観測。
        budget: 制約。
        recent_human_queries: 直近に人へ発した質問の時刻（上限判定に使う）。
        at: 判定時刻。

    Returns:
        選択の結果と理由。
    """
    moment = ensure_utc(at)
    already_asked = sum(
        1 for asked in recent_human_queries if moment - ensure_utc(asked) < budget.window
    )
    remaining_human = max(0, budget.max_human_queries - already_asked)

    pending = list(candidates)
    remaining: dict[str, float] = {}
    chosen: list[ObservationQuery] = []
    utilities: list[float] = []
    over_budget: list[str] = []
    over_human: list[str] = []
    spent = 0.0

    while pending and len(chosen) < budget.max_queries:
        scored = sorted(
            ((query.utility(remaining_entropy=remaining), query) for query in pending),
            key=lambda pair: pair[0],
            reverse=True,
        )
        utility, best = scored[0]
        if utility <= 0.0:
            break
        pending.remove(best)

        if best.kind.asks_a_human and remaining_human <= 0:
            over_human.append(best.query_id)
            continue
        if spent + best.cost > budget.total_cost:
            over_budget.append(best.query_id)
            continue

        chosen.append(best)
        utilities.append(utility)
        spent += best.cost
        if best.kind.asks_a_human:
            remaining_human -= 1
        # この観測の後に残るエントロピーを更新する（劣モジュラ性。複利で効く）
        remaining.update(best.entropy_after(remaining_entropy=remaining))

    decision = ScheduleDecision(
        chosen=tuple(chosen),
        utilities=tuple(utilities),
        skipped_for_budget=tuple(over_budget),
        skipped_for_human_limit=tuple(over_human),
        spent=spent,
    )
    log.info(
        "reasoning.schedule_decided",
        candidates=len(candidates),
        chosen=len(chosen),
        human_queries=decision.human_queries,
        human_remaining=remaining_human,
        spent=spent,
        skipped_budget=len(over_budget),
        skipped_human_limit=len(over_human),
    )
    return decision


@dataclass(slots=True)
class HumanQueryLog:
    """人への質問の履歴（頻度上限の判定に使う）。

    Attributes:
        asked_at: 質問した時刻の列。
    """

    asked_at: list[datetime] = field(default_factory=list)

    def record(self, *, at: datetime) -> None:
        """質問を記録する。

        Args:
            at: 質問した時刻。
        """
        self.asked_at.append(ensure_utc(at))

    def count_within(self, *, window: timedelta, at: datetime) -> int:
        """時間窓内の質問回数。

        Args:
            window: 時間窓。
            at: 判定時刻。
        """
        moment = ensure_utc(at)
        return sum(1 for asked in self.asked_at if moment - asked < window)
