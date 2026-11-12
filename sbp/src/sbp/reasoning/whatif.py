"""シミュレーション・What-if（設計書 §8.4、M5-06）。

> 介入：ActionType インスタンスを介入エンコーダで WM 入力に変換し、rollout を実行。結果は
> hypothetical 様相のシナリオグラフに格納。
> シナリオの主信念への昇格は明示的操作のみ。

**``hypothetical`` は隔離する**（不変条件 3）。What-if の結果はシナリオ用名前付きグラフに入れ、
:func:`promote_scenario` 以外で主信念に入らない。ここを緩めると「もし保全を延期したら」という
検討結果が、検討したという事実によって信念になる。

**反実仮想には妥当性スコアを併記する**（設計書 §8.4）。「もしあのとき停止していなかったら」は
因果の問いであり、答えの信用度は**その介入を実際に観測した量**に依存する。介入データが少ない
領域での反実仮想は、形は同じでも中身が外挿である。スコアを併記しないと、使う側がその違いを
見分けられない。

**介入エンコーダは WM が介入付きで学習されていることを要求する。** koopman / jepa_ts はどちらも、
学習時に介入を与えていなければ ``step(action=...)`` を拒否する。この層でそれを迂回して「介入
なしの rollout」を What-if として返すと、介入の効果が常に 0 の嘘になる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import numpy as np

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

    from sbp.core.assertion import Assertion
    from sbp.worldmodel.base import LatentState, Trajectory, WorldModel

__all__ = [
    "InterventionEncoding",
    "Scenario",
    "ScenarioKind",
    "ValidityScore",
    "encode_intervention",
    "promote_scenario",
    "run_scenario",
    "validity_from_support",
]

log = get_logger(__name__)

_MIN_SUPPORT = 5
"""妥当性スコアで「観測された介入」とみなす最小件数。これ未満は外挿として扱う。"""


class ScenarioKind(StrEnum):
    """シナリオの種類（設計書 §8.4）。"""

    INTERVENTION = "intervention"
    """アクションを実行したらどうなるか（前向き）。"""
    PLAN_DIFF = "plan_diff"
    """計画の代替案（受注前倒し、保全繰り延べ）。"""
    COUNTERFACTUAL = "counterfactual"
    """解決済み Situation に対し介入を差し替える（後ろ向き、因果の問い）。"""

    @property
    def is_causal(self) -> bool:
        """因果の主張を含むか（妥当性スコアが必須になる）。"""
        return self is ScenarioKind.COUNTERFACTUAL


@dataclass(frozen=True, slots=True)
class InterventionEncoding:
    """ActionType を WM の介入入力に写したもの（設計書 §8.4 の介入エンコーダ）。

    Attributes:
        action_type: 元のアクション型 IRI。
        vector: WM に渡す介入ベクトル。
        horizon: 介入を効かせる刻み数。
        description: 人が読む説明。
    """

    action_type: str
    vector: NDArray[np.float64]
    horizon: int
    description: str = ""

    def __post_init__(self) -> None:
        """地平が正であることを検証する。"""
        if self.horizon < 1:
            raise ReasoningError(
                f"an intervention must act for at least 1 step, got {self.horizon}"
            )
        if self.vector.ndim != 1:
            raise ReasoningError(
                f"an intervention vector must be 1-D, got shape {self.vector.shape}"
            )

    def as_sequence(self) -> tuple[NDArray[np.float64], ...]:
        """各刻みの介入列（rollout に渡す形）。"""
        return tuple(self.vector.copy() for _ in range(self.horizon))


@dataclass(frozen=True, slots=True)
class ValidityScore:
    """反実仮想の妥当性（設計書 §8.4）。

    Attributes:
        support: この介入を実際に観測した件数。
        score: 0〜1 の妥当性。
        note: 人が読む説明。
    """

    support: int
    score: float
    note: str

    @property
    def is_extrapolation(self) -> bool:
        """介入データが足りず外挿になっているか。"""
        return self.support < _MIN_SUPPORT


def validity_from_support(support: int, *, minimum: int = _MIN_SUPPORT) -> ValidityScore:
    """観測された介入の件数から妥当性スコアを作る。

    件数が少ないほど 0 に近づける。**件数 0 で 0 にする**のが要点で、「介入を一度も観測して
    いない反実仮想」は形だけ同じ外挿であり、使う側にそれが見える必要がある。

    Args:
        support: この介入を観測した件数。
        minimum: 「観測された」とみなす下限。

    Raises:
        ReasoningError: 件数が負の場合。
    """
    if support < 0:
        raise ReasoningError(f"support must be non-negative, got {support}")
    score = min(1.0, support / max(minimum, 1))
    if support == 0:
        note = "この介入は一度も観測されていない。結果は外挿であり因果的な主張としては使えない"
    elif support < minimum:
        note = f"観測は {support} 件（下限 {minimum}）。因果的妥当性は低い"
    else:
        note = f"観測は {support} 件。介入データに支えられている"
    return ValidityScore(support=support, score=score, note=note)


@dataclass(frozen=True, slots=True)
class Scenario:
    """What-if の 1 本（設計書 §8.4）。

    Attributes:
        scenario_id: 識別子。シナリオグラフの名前に使う。
        kind: 種類。
        entity: 対象実体の IRI。
        origin: 起点時刻。
        trajectories: WM の rollout 結果。
        intervention: 適用した介入（計画差分のみの場合は ``None``）。
        plan_diff: 計画の差分（``PlanItem`` IRI → 変更内容）。
        validity: 反実仮想の妥当性（``COUNTERFACTUAL`` では必須）。
        wm_version: 使った WM の版 IRI。
    """

    scenario_id: UUID
    kind: ScenarioKind
    entity: str
    origin: datetime
    trajectories: tuple[Trajectory, ...]
    intervention: InterventionEncoding | None = None
    plan_diff: dict[str, str] = field(default_factory=dict)
    validity: ValidityScore | None = None
    wm_version: str = ""

    def __post_init__(self) -> None:
        """シナリオとして成立しているか検証する。"""
        object.__setattr__(self, "origin", ensure_utc(self.origin))
        if not self.trajectories:
            raise ReasoningError("a scenario must contain at least one trajectory")
        if self.kind.is_causal and self.validity is None:
            raise ReasoningError(
                "a counterfactual scenario must carry its validity score; a causal claim whose "
                "support is unknown cannot be told apart from extrapolation (design §8.4)"
            )
        if self.kind is ScenarioKind.INTERVENTION and self.intervention is None:
            raise ReasoningError("an intervention scenario must carry the encoded intervention")
        if self.kind is ScenarioKind.PLAN_DIFF and not self.plan_diff:
            raise ReasoningError("a plan-diff scenario must state what changed in the plan")

    @property
    def graph_name(self) -> str:
        """シナリオ用名前付きグラフの名前（不変条件 3 の隔離先）。"""
        return f"urn:sbp:scenario:{self.scenario_id}"

    def mean_at(self, lead_index: int) -> NDArray[np.float64]:
        """ある地平での潜在の平均（軌道をまたいだ平均）。

        Args:
            lead_index: 地平の添字（0 が最初の刻み）。

        Raises:
            ReasoningError: その地平を持たない軌道がある場合。
        """
        for trajectory in self.trajectories:
            if lead_index >= len(trajectory.states):
                raise ReasoningError(
                    f"lead index {lead_index} is beyond this scenario's horizon "
                    f"({len(trajectory.states)} steps)"
                )
        return np.mean(
            [trajectory.states[lead_index].z for trajectory in self.trajectories], axis=0
        )

    def to_assertions(
        self,
        *,
        fluent: str,
        probabilities: Sequence[float],
        ontology_version: str,
    ) -> tuple[Assertion, ...]:
        """シナリオの結果を ``hypothetical`` アサーションにする（不変条件 3）。

        様相は必ず ``hypothetical``。呼び出し側がこれを変えられないようにしてあるのは、
        「検討したという事実によって信念になる」経路を作らないためである。

        Args:
            fluent: 対象フルーエント型 IRI。
            probabilities: 各地平でのプローブ出力（``trajectories[0].states`` と同じ長さ）。
            ontology_version: オントロジー版。

        Raises:
            ReasoningError: 長さが合わない場合。
        """
        from sbp.core.assertion import Assertion  # noqa: PLC0415 — 循環 import 回避
        from sbp.core.belief import BooleanBelief  # noqa: PLC0415

        states = self.trajectories[0].states
        if len(probabilities) != len(states):
            raise ReasoningError(
                f"{len(states)} horizon steps but {len(probabilities)} probabilities"
            )
        return tuple(
            Assertion(
                subject=self.entity,
                predicate=fluent,
                object=True,
                valid_from=state.at,
                tx_from=self.origin,
                modality=Modality.HYPOTHETICAL,
                # シナリオ IRI は不変条件 3 のガード（core.Assertion が必須にしている）
                scenario=self.graph_name,
                belief=BooleanBelief(p=min(0.99, max(0.01, probability))),
                provenance=f"scenario:{self.scenario_id}",
                ontology_version=ontology_version,
            )
            for state, probability in zip(states, probabilities, strict=True)
        )


def encode_intervention(
    action_type: str,
    *,
    action_dim: int,
    horizon: int,
    magnitude: float = 1.0,
    channel: int = 0,
) -> InterventionEncoding:
    """ActionType を WM の介入ベクトルに写す（設計書 §8.4 の介入エンコーダ）。

    初期実装は「アクション型ごとに 1 チャンネルを立てる」one-hot 的な符号化である。学習した
    埋め込みにしないのは、**介入の効果が解釈できる**必要があるからで、どのチャンネルを立てた
    結果どう動いたかを追えないと What-if の説明ができない。

    **契約（``ActionContract``）ではなくアクション型 IRI を取る。** ``reasoning`` は ``actions``
    を import できない（層の向きが逆）。それ以上に、介入の符号化に必要なのは「どのアクション型か」
    だけで、前提や効果は要らない——契約を受け取る設計にすると、この関数が契約の変更に
    引きずられる。

    Args:
        action_type: 介入するアクション型の IRI。
        action_dim: WM が期待する介入次元。
        horizon: 介入を効かせる刻み数。
        magnitude: 介入の強さ。
        channel: 立てるチャンネルの添字。

    Raises:
        ReasoningError: チャンネルが次元の外、または次元が正でない場合。
    """
    if action_dim < 1:
        raise ReasoningError(f"action_dim must be at least 1, got {action_dim}")
    if not 0 <= channel < action_dim:
        raise ReasoningError(
            f"channel {channel} is outside the action space of dimension {action_dim}"
        )
    vector = np.zeros(action_dim, dtype=np.float64)
    vector[channel] = magnitude
    return InterventionEncoding(
        action_type=action_type,
        vector=vector,
        horizon=horizon,
        description=f"{action_type} をチャンネル {channel} に強さ {magnitude} で印加",
    )


def run_scenario(
    model: WorldModel,
    state: LatentState,
    *,
    kind: ScenarioKind,
    entity: str,
    intervention: InterventionEncoding | None = None,
    plan_diff: Mapping[str, str] | None = None,
    context: NDArray[np.float64] | None = None,
    samples: int = 3,
    support: int | None = None,
    seed: int,
) -> Scenario:
    """介入または計画差分の下で rollout を実行する（設計書 §8.4）。

    Args:
        model: 使う WM。
        state: 起点の潜在。
        kind: シナリオの種類。
        entity: 対象実体の IRI。
        intervention: 介入エンコーディング。
        plan_diff: 計画の差分。
        context: 記号条件付けベクトル。
        samples: 軌道の本数。
        support: この介入を観測した件数（反実仮想では必須）。
        seed: 乱数シード。

    Returns:
        シナリオ。

    Raises:
        ReasoningError: 反実仮想で ``support`` が無い、または介入が要る種類で無い場合。
    """
    if kind.is_causal and support is None:
        raise ReasoningError(
            "a counterfactual needs the number of observed interventions (support) so its "
            "validity can be scored; passing none would hide extrapolation (design §8.4)"
        )
    if kind is ScenarioKind.INTERVENTION and intervention is None:
        raise ReasoningError("an intervention scenario needs an encoded intervention")

    horizon = intervention.horizon if intervention is not None else 1
    actions = None if intervention is None else list(intervention.as_sequence())
    trajectories = model.rollout(
        state,
        horizon=horizon,
        samples=samples,
        actions=actions,
        context=context,
        seed=seed,
    )
    scenario = Scenario(
        scenario_id=uuid4(),
        kind=kind,
        entity=entity,
        origin=state.at,
        trajectories=trajectories,
        intervention=intervention,
        plan_diff=dict(plan_diff or {}),
        validity=None if support is None else validity_from_support(support),
        wm_version=state.wm_version,
    )
    log.info(
        "reasoning.scenario_run",
        scenario=str(scenario.scenario_id),
        kind=kind.value,
        entity=entity,
        horizon=horizon,
        samples=samples,
        wm_version=state.wm_version,
        validity=None if scenario.validity is None else scenario.validity.score,
    )
    return scenario


def promote_scenario(
    scenario: Scenario,
    assertions: Sequence[Assertion],
    *,
    promoted_by: str,
    reason: str,
    at: datetime,
    modality: Modality = Modality.PLANNED,
) -> tuple[Assertion, ...]:
    """シナリオの結果を主信念に昇格する（設計書 §8.4、不変条件 3）。

    **これが唯一の経路である。** ``hypothetical`` のアサーションは他のどこからも主信念に入らない。
    昇格先の様相を ``planned`` に限っているのは、設計書 §8.4 の「決定として採用した計画は
    planned に昇格」に対応する——シナリオの結果が ``observed`` や ``inferred`` になることは無い
    （観測していないし、観測から推定したものでもない）。

    Args:
        scenario: 昇格するシナリオ。
        assertions: 昇格対象の ``hypothetical`` アサーション。
        promoted_by: 昇格を決めた主体の IRI。
        reason: 昇格の理由。
        at: 昇格時刻。
        modality: 昇格先の様相（``planned`` のみ）。

    Returns:
        昇格後のアサーション（``tx_from`` は昇格時刻）。

    Raises:
        ReasoningError: 理由が空、様相が ``planned`` 以外、入力が ``hypothetical`` でない、
            または反実仮想を昇格しようとした場合。
    """
    if not reason:
        raise ReasoningError(
            "promoting a scenario requires a recorded reason; an unexplained promotion turns a "
            "thought experiment into a belief with no trace of the decision"
        )
    if modality is not Modality.PLANNED:
        raise ReasoningError(
            f"a scenario can only be promoted to planned, not {modality.value}; its content was "
            "never observed and was not inferred from observation (invariant 3)"
        )
    if scenario.kind.is_causal:
        raise ReasoningError(
            "a counterfactual scenario cannot be promoted; it describes a world that did not "
            "happen (design §8.4)"
        )
    for assertion in assertions:
        if assertion.modality is not Modality.HYPOTHETICAL:
            raise ReasoningError(
                f"only hypothetical assertions can be promoted, got {assertion.modality.value}"
            )
        if assertion.provenance != f"scenario:{scenario.scenario_id}":
            raise ReasoningError(
                f"assertion {assertion.assertion_id} does not come from scenario "
                f"{scenario.scenario_id}; promoting across scenarios loses the link between the "
                "decision and what was simulated"
            )

    moment = ensure_utc(at)
    promoted = tuple(
        assertion.model_copy(
            update={
                "modality": modality,
                # planned は scenario を持てない（不変条件 3）。隔離から出たことを表す
                "scenario": None,
                "tx_from": moment,
                "provenance": (
                    f"promotion:{scenario.scenario_id}?by={promoted_by}&reason={reason}"
                ),
            }
        )
        for assertion in assertions
    )
    log.info(
        "reasoning.scenario_promoted",
        scenario=str(scenario.scenario_id),
        kind=scenario.kind.value,
        assertions=len(promoted),
        promoted_by=promoted_by,
        reason=reason,
    )
    return promoted
