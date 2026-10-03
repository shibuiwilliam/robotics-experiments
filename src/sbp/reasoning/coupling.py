"""実体間結合（設計書 §8.1 の「関係制約」と loopy BP）。

> 実体間結合は関係に沿ったメッセージパッシング（loopy BP）を一定周期で回す。大規模時は結合を
> 「強い関係」（部分−全体、同一 Situation）に限定する。

**なぜ前向きフィルタだけでは足りないか。** ポンプが止まっているという観測は、そのポンプの信念
だけでなく「そのポンプを含むライン」の信念も動かすべきである。実体単位のフィルタはこの情報を
運ばないため、ライン全体は観測と無関係に稼働と信じ続ける。

**なぜ「強い関係」に限るか。** 同じ拠点にある、という程度の関係まで結合すると、辺数が実体数の
二乗に近づき、しかも弱い相関で信念が一様化する（全部が同じ確率に寄る）。部分−全体と同一
Situation の 2 つに限るのは、計算量のためだけでなく、**強い依存だけが情報を運ぶ**からである。

集約の向き（all か any か）は :data:`sbp.core.vocab` の ``aggregatesAs``——つまりオントロジー
——が決める。コードの条件分岐に書かない（CLAUDE.md: オントロジーを飛ばしてコードで概念を
定義しない）。

実装は log オッズ空間での同期更新 + 減衰（damping）。loopy BP は収束保証が無いため、
``bp_max_iterations`` で打ち切り、収束したかどうかを :class:`CouplingResult` に残す。
「収束しなかった」を黙って返すと、振動している信念が確定値として永続化される。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "AggregationRule",
    "CouplingResult",
    "PartWhole",
    "SituationLink",
    "propagate",
]

log = get_logger(__name__)

_LOG_ODDS_CLAMP = 12.0
"""対数オッズの上限（確率 ~0.999994）。無限大を避け、1 件の因子で信念を固定しないため。"""

_EPSILON = 1e-9


class AggregationRule(StrEnum):
    """部分 → 全体の集約の型（オントロジーの ``sbp:AggregationRule`` に対応）。"""

    ALL = "all"
    """全部分で成立して初めて全体で成立する（``Running`` など）。"""
    ANY = "any"
    """いずれかの部分で成立すれば全体で成立する（``Faulted`` など）。"""

    @classmethod
    def from_iri(cls, iri: str) -> AggregationRule:
        """オントロジーの個体 IRI から集約規則を引く。

        Args:
            iri: ``sbp:AggregateAll`` または ``sbp:AggregateAny`` の IRI。

        Raises:
            ReasoningError: 未知の集約規則。
        """
        from sbp.core import vocab  # noqa: PLC0415 — 生成物への依存を局所化する

        if iri == vocab.core.AggregateAll:
            return cls.ALL
        if iri == vocab.core.AggregateAny:
            return cls.ANY
        raise ReasoningError(
            f"unknown aggregation rule {iri!r}; declare it in ontology/core as an "
            "sbp:AggregationRule individual"
        )


@dataclass(frozen=True, slots=True)
class PartWhole:
    """部分−全体の結合 1 本（``sbp:partOf``）。

    Attributes:
        whole: 全体の実体 IRI。
        parts: 部分の実体 IRI。
        fluent: 結合するフルーエント型 IRI。
        rule: 集約規則（オントロジーの ``aggregatesAs`` から読む）。
        strength: 結合の強さ（0〜1）。1 は厳密な制約、小さいほど緩い。
    """

    whole: str
    parts: tuple[str, ...]
    fluent: str
    rule: AggregationRule
    strength: float = 0.8

    def __post_init__(self) -> None:
        """結合が意味を持つことを検証する。"""
        if not self.parts:
            raise ReasoningError(f"part-whole coupling for {self.whole} has no parts")
        if not 0.0 < self.strength <= 1.0:
            raise ReasoningError(f"coupling strength must be in (0, 1], got {self.strength}")


@dataclass(frozen=True, slots=True)
class SituationLink:
    """同一 Situation に参加する実体どうしの結合（``sbp:inSituation``）。

    同じ出来事に参加している実体は状態が揃いやすい（同じラインの設備が一斉に止まる、同じ
    作業で複数の設備が保全に入る）。部分−全体と違って**向きが無い**ので、対称な引き寄せに
    なる。

    Attributes:
        situation: Situation の IRI。
        members: 参加実体の IRI。
        fluent: 結合するフルーエント型 IRI。
        strength: 結合の強さ（0〜1）。部分−全体より弱く設定するのが普通である。
    """

    situation: str
    members: tuple[str, ...]
    fluent: str
    strength: float = 0.3

    def __post_init__(self) -> None:
        """結合が意味を持つことを検証する。"""
        if len(self.members) < 2:
            raise ReasoningError(
                f"situation {self.situation} couples {len(self.members)} member(s); "
                "a coupling needs at least two"
            )
        if not 0.0 < self.strength <= 1.0:
            raise ReasoningError(f"coupling strength must be in (0, 1], got {self.strength}")


@dataclass(frozen=True, slots=True)
class CouplingResult:
    """メッセージパッシング 1 周の結果。

    Attributes:
        posteriors: ``(実体, フルーエント)`` → 更新後の確率。
        iterations: 実際に回した反復回数。
        converged: 収束したか。``False`` のとき信念を永続化してよいかは呼び出し側の判断。
        max_delta: 最後の反復での最大変化量（確率の絶対差）。
        messages: 診断用。``(送り元, 宛先, フルーエント)`` → 対数オッズのメッセージ。
    """

    posteriors: dict[tuple[str, str], float]
    iterations: int
    converged: bool
    max_delta: float
    messages: dict[tuple[str, str, str], float] = field(default_factory=dict)

    def probability(self, entity: str, fluent: str) -> float:
        """更新後の確率を取り出す。

        Args:
            entity: 実体 IRI。
            fluent: フルーエント型 IRI。

        Raises:
            ReasoningError: その組が結合に含まれていない場合。
        """
        try:
            return self.posteriors[entity, fluent]
        except KeyError as exc:
            raise ReasoningError(
                f"{entity} has no coupled belief about {fluent} in this result"
            ) from exc


def _log_odds(probability: float) -> float:
    """確率を対数オッズにする（両端は飽和させる）。"""
    clipped = min(max(probability, _EPSILON), 1.0 - _EPSILON)
    return min(_LOG_ODDS_CLAMP, max(-_LOG_ODDS_CLAMP, math.log(clipped / (1.0 - clipped))))


def _probability(log_odds: float) -> float:
    """対数オッズを確率に戻す。"""
    return 1.0 / (1.0 + math.exp(-min(_LOG_ODDS_CLAMP, max(-_LOG_ODDS_CLAMP, log_odds))))


def _part_to_whole_message(
    part_probabilities: Sequence[float], *, rule: AggregationRule, strength: float
) -> float:
    """部分 → 全体のメッセージ（対数オッズ）。

    ``ALL`` は積（全部成立の確率）、``ANY`` は noisy-OR（どれか成立の確率）。確率は log 空間で
    扱う（CLAUDE.md 数値規約）が、ここは積と補数の積なので素直に計算して最後に対数オッズへ
    移す方が読みやすい。
    """
    if rule is AggregationRule.ALL:
        implied = math.prod(part_probabilities)
    else:
        implied = 1.0 - math.prod(1.0 - p for p in part_probabilities)
    return strength * _log_odds(implied)


def _whole_to_part_message(
    whole_probability: float,
    *,
    rule: AggregationRule,
    sibling_probabilities: Sequence[float],
    strength: float,
) -> float:
    """全体 → 部分のメッセージ（対数オッズ）。

    ``ALL`` では「全体が成立しているなら部分も成立している」が強い含意になる（全体が真 →
    全部分が真）。逆向き（全体が偽）は「どれか 1 つが偽」でしかなく、どの部分かは特定できない
    ——兄弟が揃って真なら残りの 1 つが疑わしい、という形でのみ効く。

    ``ANY`` はこれが鏡写しになる（全体が偽 → 全部分が偽が強い含意）。
    """
    whole_log_odds = _log_odds(whole_probability)
    siblings_present = math.prod(sibling_probabilities) if sibling_probabilities else 1.0
    siblings_absent = (
        math.prod(1.0 - p for p in sibling_probabilities) if sibling_probabilities else 1.0
    )
    if rule is AggregationRule.ALL:
        if whole_log_odds >= 0.0:
            # 全体が真 → 部分も真（強い）
            return strength * whole_log_odds
        # 全体が偽 → 兄弟が揃って真なら、この部分が偽である疑いが強まる
        return strength * whole_log_odds * siblings_present
    if whole_log_odds <= 0.0:
        # 全体が偽 → 部分も偽（強い）
        return strength * whole_log_odds
    # 全体が真 → 兄弟が揃って偽なら、この部分が真である疑いが強まる
    return strength * whole_log_odds * siblings_absent


def propagate(
    priors: Mapping[tuple[str, str], float],
    *,
    part_wholes: Sequence[PartWhole] = (),
    situations: Sequence[SituationLink] = (),
    max_iterations: int = 5,
    damping: float = 0.5,
    tolerance: float = 1e-3,
) -> CouplingResult:
    """強い関係に沿って信念を伝播する（loopy BP、設計書 §8.1）。

    各実体の事前（実体単位の前向きフィルタの事後）を入力に取り、関係因子からのメッセージを
    足して更新する。**減衰（damping）を入れるのが要点**で、減衰無しの同期更新はループを含む
    グラフで振動しやすい。

    Args:
        priors: ``(実体, フルーエント)`` → 実体単位フィルタの事後確率。
        part_wholes: 部分−全体の結合。
        situations: 同一 Situation の結合。
        max_iterations: 反復上限（``Settings.bp_max_iterations``）。
        damping: 減衰係数（0 で更新なし、1 で減衰なし）。
        tolerance: 収束判定の閾値（確率の最大変化量）。

    Returns:
        更新後の確率と収束の診断。

    Raises:
        ReasoningError: 結合が参照する実体の事前が無い、または引数が範囲外の場合。
    """
    if not 0.0 < damping <= 1.0:
        raise ReasoningError(f"damping must be in (0, 1], got {damping}")
    if max_iterations < 1:
        raise ReasoningError(f"max_iterations must be at least 1, got {max_iterations}")

    _check_coverage(priors, part_wholes=part_wholes, situations=situations)

    base = {key: _log_odds(value) for key, value in priors.items()}
    current = dict(base)
    messages: dict[tuple[str, str, str], float] = {}
    max_delta = 0.0
    iterations = 0
    converged = False

    for iteration in range(1, max_iterations + 1):
        iterations = iteration
        incoming: dict[tuple[str, str], float] = dict.fromkeys(current, 0.0)
        messages = {}

        for coupling in part_wholes:
            fluent = coupling.fluent
            part_probabilities = [_probability(current[part, fluent]) for part in coupling.parts]
            message = _part_to_whole_message(
                part_probabilities, rule=coupling.rule, strength=coupling.strength
            )
            incoming[coupling.whole, fluent] += message
            messages[coupling.parts[0], coupling.whole, fluent] = message

            whole_probability = _probability(current[coupling.whole, fluent])
            for index, part in enumerate(coupling.parts):
                siblings = [p for i, p in enumerate(part_probabilities) if i != index]
                down = _whole_to_part_message(
                    whole_probability,
                    rule=coupling.rule,
                    sibling_probabilities=siblings,
                    strength=coupling.strength,
                )
                incoming[part, fluent] += down
                messages[coupling.whole, part, fluent] = down

        for link in situations:
            fluent = link.fluent
            for member in link.members:
                others = [
                    _probability(current[other, fluent])
                    for other in link.members
                    if other != member
                ]
                # 同一 Situation の引き寄せは平均への寄せ（向きが無い対称な結合）
                average = sum(others) / len(others)
                message = link.strength * _log_odds(average)
                incoming[member, fluent] += message
                messages[link.situation, member, fluent] = message

        updated: dict[tuple[str, str], float] = {}
        max_delta = 0.0
        for key, prior_log_odds in base.items():
            target = prior_log_odds + incoming[key]
            blended = (1.0 - damping) * current[key] + damping * target
            max_delta = max(max_delta, abs(_probability(blended) - _probability(current[key])))
            updated[key] = blended
        current = updated

        if max_delta < tolerance:
            converged = True
            break

    if not converged:
        log.info(
            "reasoning.coupling_did_not_converge",
            iterations=iterations,
            max_delta=max_delta,
            nodes=len(current),
        )
    return CouplingResult(
        posteriors={key: _probability(value) for key, value in current.items()},
        iterations=iterations,
        converged=converged,
        max_delta=max_delta,
        messages=messages,
    )


def _check_coverage(
    priors: Mapping[tuple[str, str], float],
    *,
    part_wholes: Sequence[PartWhole],
    situations: Sequence[SituationLink],
) -> None:
    """結合が参照する全ノードの事前が与えられているか検証する。

    欠けたノードを 0.5 で埋める実装にしてはならない。「情報が無い」ことを「半々である」に
    すり替えると、結合が勝手に信念を作り出す。
    """
    for value in priors.values():
        if not 0.0 <= value <= 1.0:
            raise ReasoningError(f"prior must be a probability in [0, 1], got {value}")
    missing: list[tuple[str, str]] = []
    for coupling in part_wholes:
        for entity in (coupling.whole, *coupling.parts):
            if (entity, coupling.fluent) not in priors:
                missing.append((entity, coupling.fluent))
    for link in situations:
        for member in link.members:
            if (member, link.fluent) not in priors:
                missing.append((member, link.fluent))
    if missing:
        listed = ", ".join(f"{entity}@{fluent}" for entity, fluent in sorted(set(missing))[:5])
        raise ReasoningError(
            f"coupling references {len(set(missing))} belief(s) with no prior: {listed}; "
            "missing priors must be supplied, not defaulted to 0.5 (that would invent belief)"
        )
