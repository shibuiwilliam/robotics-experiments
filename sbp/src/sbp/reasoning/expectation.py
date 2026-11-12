"""期待オブジェクト検証器（設計書 §8.5、付録 A.3、M5-04）。

アクションを実行したら「何が起こるべきか」を :class:`Expectation` として作り、期限までに検証
する。状態機械は設計書 §8.5 の通り::

    Created → Active → {Fulfilled, Violated, Undetermined} → Learned

**記号側期待と潜在側期待を別々に検証するのが要点である**（設計書 §8.5）。業務効果は記号側
（プロセスオントロジー T 由来）、物理効果は潜在側（WM の rollout 由来）。まとめて判定すると
「WM が外れたのか T が外れたのか」が分からず、検証結果が学習信号にならない。

**検証方法を記録する。** conformal 被覆とベイズ因子は違う問いに答える。前者は「観測が予測区間に
入ったか」、後者は「期待した分布と期待しない分布のどちらが観測を説明するか」。どちらで判定した
かを残さないと、後から「なぜ違反と判定されたのか」が再構成できない。

**期限切れは ``Violated`` ではなく ``Undetermined``。** 証拠が足りないことと、期待が外れたことは
別である。前者はスケジューラへ観測要求を出す（観測を増やせば判定できる）。混同すると、観測の
穴が「アクションの失敗」として記録され、信頼性モデルと学習が歪む。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.time import UtcDatetime, ensure_utc

__all__ = [
    "Expectation",
    "ExpectationStatus",
    "ExpectedEffect",
    "VerificationInputs",
    "VerificationMethod",
    "VerificationResult",
    "bayes_factor",
    "conformal_covered",
    "verify_expectation",
]

log = get_logger(__name__)

_MIN_BAYES_FACTOR = 3.0
"""違反と判定するベイズ因子の下限（Jeffreys の目安で「相当の証拠」）。"""


class ExpectationStatus(StrEnum):
    """期待の状態（設計書 §8.5、付録 A.3）。"""

    CREATED = "Created"
    """アクション実行直後。期待効果の分布と期限はまだ無い。"""
    ACTIVE = "Active"
    """T と rollout から期待効果分布・期限を生成した。検証待ち。"""
    FULFILLED = "Fulfilled"
    """観測信念が期待分布内（被覆検定を通った）。"""
    VIOLATED = "Violated"
    """期待外の信念が閾値以上で成立した。"""
    UNDETERMINED = "Undetermined"
    """期限切れまたは証拠不足。**違反ではない**。"""
    LEARNED = "Learned"
    """介入データとして登録した（終端）。"""

    @property
    def is_terminal(self) -> bool:
        """終端状態か。"""
        return self is ExpectationStatus.LEARNED

    @property
    def is_verdict(self) -> bool:
        """検証が済んだ状態か。"""
        return self in {
            ExpectationStatus.FULFILLED,
            ExpectationStatus.VIOLATED,
            ExpectationStatus.UNDETERMINED,
        }

    @property
    def rank(self) -> int:
        """ライフサイクル上の位置（進むほど大きい）。

        **永続化での後退検出に使う**（M8-18。不一致の ``DiscrepancyStatus.rank`` と同じ理由）。
        ``transition()`` は遷移表で守るが、表に書くときは複数の経路が同じ行を触るので、片方が
        古い状態で上書きする事故が起こりうる（所見 80）。

        3 つの判定（``Fulfilled`` / ``Violated`` / ``Undetermined``）は**同じ位置**である
        ——どれも ``Active`` の次で、互いに遷移しない。
        """
        return {
            ExpectationStatus.CREATED: 0,
            ExpectationStatus.ACTIVE: 1,
            ExpectationStatus.FULFILLED: 2,
            ExpectationStatus.VIOLATED: 2,
            ExpectationStatus.UNDETERMINED: 2,
            ExpectationStatus.LEARNED: 3,
        }[self]


_ALLOWED: dict[ExpectationStatus, frozenset[ExpectationStatus]] = {
    ExpectationStatus.CREATED: frozenset({ExpectationStatus.ACTIVE}),
    ExpectationStatus.ACTIVE: frozenset(
        {
            ExpectationStatus.FULFILLED,
            ExpectationStatus.VIOLATED,
            ExpectationStatus.UNDETERMINED,
        }
    ),
    ExpectationStatus.FULFILLED: frozenset({ExpectationStatus.LEARNED}),
    ExpectationStatus.VIOLATED: frozenset({ExpectationStatus.LEARNED}),
    ExpectationStatus.UNDETERMINED: frozenset({ExpectationStatus.LEARNED}),
    ExpectationStatus.LEARNED: frozenset(),
}
"""許可された遷移（設計書 §8.5 の状態機械図）。"""


class VerificationMethod(StrEnum):
    """判定方法（付録 A.3 の ``verification.method``）。"""

    CONFORMAL = "conformal"
    """予測区間の被覆で判定する。区間の作り方は較正済みの分位点。"""
    BAYES_FACTOR = "bayes_factor"
    """期待分布と対立分布の尤度比で判定する。"""


def conformal_covered(observed: float, *, lower: float, upper: float) -> bool:
    """観測が予測区間に入っているか（右閉区間で判定する）。

    時間区間（不変条件 8 の右開）と違い、**予測区間は右閉**である。分位点の上端は「その値まで
    は期待通り」を意味し、上端ちょうどを外とすると被覆率が系統的に下振れする。

    Args:
        observed: 観測値。
        lower: 区間の下端。
        upper: 区間の上端。

    Raises:
        ReasoningError: 区間が逆順の場合。
    """
    if upper < lower:
        raise ReasoningError(f"prediction interval is inverted: [{lower}, {upper}]")
    return lower <= observed <= upper


def bayes_factor(
    observed: float, *, expected: float, alternative: float, scale: float = 0.1
) -> float:
    """期待分布と対立分布のベイズ因子（対立 / 期待）。

    1 より大きいほど「期待していなかった方」を支持する。ガウス尤度で近似するのは、観測が
    確率（0〜1）であり、較正済みの確信度に対しては十分な近似だからである。

    Args:
        observed: 観測された確率。
        expected: 期待した確率。
        alternative: 対立仮説の確率（期待の裏）。
        scale: 尤度の標準偏差。小さすぎると微小な差で違反判定になる。

    Returns:
        ``P(観測 | 対立) / P(観測 | 期待)``。

    Raises:
        ReasoningError: ``scale`` が非正の場合。
    """
    if scale <= 0.0:
        raise ReasoningError(f"scale must be positive, got {scale}")
    expected_likelihood = math.exp(-((observed - expected) ** 2) / (2.0 * scale**2))
    alternative_likelihood = math.exp(-((observed - alternative) ** 2) / (2.0 * scale**2))
    return alternative_likelihood / max(expected_likelihood, 1e-12)


class ExpectedEffect(BaseModel):
    """期待する効果 1 件（付録 A.3 の ``expected_effects``）。

    Attributes:
        fluent: 対象フルーエント型 IRI（業務効果では記録型 IRI）。
        entity: 対象実体の IRI。
        expected: 期待する確率（物理効果の閾値、業務効果では 1.0）。
        lower: 予測区間の下端。
        upper: 予測区間の上端。
        deadline: この効果の期限。
        origin: 期待の由来。``symbolic`` は T 由来、``rollout`` は WM 由来。
        description: 人が読む説明（契約から引き継ぐ）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fluent: str
    entity: str
    expected: float = Field(ge=0.0, le=1.0)
    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)
    deadline: UtcDatetime
    origin: Literal["symbolic", "rollout"]
    description: str = ""

    @model_validator(mode="after")
    def _interval_contains_the_expectation(self) -> Self:
        """区間が逆順でなく、期待値を含むことを検証する。"""
        if self.upper < self.lower:
            raise ReasoningError(
                f"expected effect on {self.fluent} has an inverted interval "
                f"[{self.lower}, {self.upper}]"
            )
        if not self.lower <= self.expected <= self.upper:
            raise ReasoningError(
                f"expected effect on {self.fluent} has expectation {self.expected} outside its "
                f"own interval [{self.lower}, {self.upper}]; the interval would reject the very "
                "value the action is supposed to produce"
            )
        return self

    def covers(self, observed: float) -> bool:
        """観測がこの効果の区間に入っているか。

        Args:
            observed: 観測された確率。
        """
        return conformal_covered(observed, lower=self.lower, upper=self.upper)


class VerificationResult(BaseModel):
    """検証 1 回の結果（付録 A.3 の ``verification``）。

    Attributes:
        method: 判定方法。
        verified_at: 判定時刻。
        symbolic_passed: 記号側期待が通ったか（``None`` なら記号側の期待が無い）。
        rollout_passed: 潜在側期待が通ったか（``None`` なら潜在側の期待が無い）。
        observations: フルーエント IRI → 観測した確率。
        statistic: 判定に使った統計量（ベイズ因子など）。
        note: 人が読む説明。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: VerificationMethod
    verified_at: UtcDatetime
    symbolic_passed: bool | None = None
    rollout_passed: bool | None = None
    observations: dict[str, float] = Field(default_factory=dict)
    statistic: float | None = None
    note: str = ""

    @property
    def blames_the_world_model(self) -> bool:
        """潜在側だけが外れたか（WM の問題を示す）。"""
        return self.rollout_passed is False and self.symbolic_passed is not False

    @property
    def blames_the_process_ontology(self) -> bool:
        """記号側だけが外れたか（プロセスオントロジー T の問題を示す）。"""
        return self.symbolic_passed is False and self.rollout_passed is not False


class Expectation(BaseModel):
    """アクション実行が生成する期待（付録 A.3）。

    Attributes:
        expectation_id: 識別子。
        action_event: 起因となった ``ActionEvent`` の ID。
        action_type: アクション型 IRI。
        target: 対象実体の IRI。
        expected_effects: 期待する効果。
        status: 状態。
        verification: 検証結果。
        created_at: 生成時刻。
        history: 状態遷移の履歴（``(遷移先, 時刻, 理由)``）。
        ontology_version: 生成時のオントロジー版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    expectation_id: UUID = Field(default_factory=uuid4)
    action_event: UUID
    action_type: str
    target: str
    expected_effects: tuple[ExpectedEffect, ...] = ()
    status: ExpectationStatus = ExpectationStatus.CREATED
    verification: VerificationResult | None = None
    created_at: UtcDatetime
    history: tuple[tuple[str, UtcDatetime, str], ...] = ()
    ontology_version: str

    @model_validator(mode="after")
    def _status_matches_its_payload(self) -> Self:
        """状態と中身の整合を検証する。"""
        if self.status is not ExpectationStatus.CREATED and not self.expected_effects:
            raise ReasoningError(
                f"an expectation in {self.status.value} must carry its expected effects; "
                "without a distribution and a deadline there is nothing to verify (design §8.5)"
            )
        if self.status.is_verdict and self.verification is None:
            raise ReasoningError(
                f"an expectation in {self.status.value} must carry its verification result "
                "(method and observations) so the verdict can be re-explained"
            )
        return self

    @property
    def deadline(self) -> datetime | None:
        """最も遅い期限（全体の期限）。"""
        if not self.expected_effects:
            return None
        return max(effect.deadline for effect in self.expected_effects)

    def effects_of(self, origin: Literal["symbolic", "rollout"]) -> tuple[ExpectedEffect, ...]:
        """由来で絞った期待効果。

        Args:
            origin: ``symbolic`` または ``rollout``。
        """
        return tuple(effect for effect in self.expected_effects if effect.origin == origin)

    def activate(
        self, effects: tuple[ExpectedEffect, ...], *, at: datetime, note: str = ""
    ) -> Expectation:
        """期待効果の分布と期限を与えて ``Active`` にする（設計書 §8.5）。

        Args:
            effects: 期待効果。
            at: 遷移時刻。
            note: 理由。

        Raises:
            ReasoningError: 効果が空、または状態が ``Created`` でない場合。
        """
        if not effects:
            raise ReasoningError(
                "activating an expectation requires at least one expected effect; an expectation "
                "with nothing expected stays Active forever and never teaches anything"
            )
        return self._transition(
            ExpectationStatus.ACTIVE, at=at, note=note, updates={"expected_effects": effects}
        )

    def verify(self, result: VerificationResult, *, at: datetime) -> Expectation:
        """検証結果から判定を下す（設計書 §8.5）。

        両側が通れば ``Fulfilled``、どちらかが明確に外れていれば ``Violated``、判定できない
        ものが残れば ``Undetermined``。

        Args:
            result: 検証結果。
            at: 遷移時刻。

        Raises:
            ReasoningError: 状態が ``Active`` でない場合。
        """
        # 宣言されている面だけを見る。**効果があるのに判定が None の面があれば Fulfilled に
        # しない**——観測できていない期待を「成立」と呼ぶと、検証していないものが学習信号に
        # 混ざる（初版はここで片側が未観測でも Fulfilled にしていた）。
        declared = {
            "symbolic": bool(self.effects_of("symbolic")),
            "rollout": bool(self.effects_of("rollout")),
        }
        verdicts = {
            "symbolic": result.symbolic_passed,
            "rollout": result.rollout_passed,
        }
        relevant = [verdicts[face] for face, has_effects in declared.items() if has_effects]
        if any(value is False for value in relevant):
            status = ExpectationStatus.VIOLATED
        elif relevant and all(value is True for value in relevant):
            status = ExpectationStatus.FULFILLED
        else:
            status = ExpectationStatus.UNDETERMINED
        return self._transition(status, at=at, note=result.note, updates={"verification": result})

    def expire(self, *, at: datetime, note: str = "期限切れ（証拠不足）") -> Expectation:
        """期限切れで ``Undetermined`` にする。

        **``Violated`` にしない。** 証拠が足りないことと期待が外れたことは別で、前者は観測を
        増やせば判定できる。混同すると観測の穴が「アクションの失敗」として記録される。

        Args:
            at: 遷移時刻。
            note: 理由。
        """
        result = VerificationResult(
            method=VerificationMethod.CONFORMAL,
            verified_at=ensure_utc(at),
            note=note,
        )
        return self._transition(
            ExpectationStatus.UNDETERMINED, at=at, note=note, updates={"verification": result}
        )

    def mark_learned(self, *, at: datetime, note: str) -> Expectation:
        """介入データとして登録済みにする（終端）。

        Args:
            at: 遷移時刻。
            note: 何を学習信号にしたか。

        Raises:
            ReasoningError: 理由が空、または状態が判定済みでない場合。
        """
        if not note:
            raise ReasoningError(
                "marking an expectation Learned requires stating what was learned; otherwise the "
                "count of learned expectations is a lie"
            )
        return self._transition(ExpectationStatus.LEARNED, at=at, note=note)

    def is_overdue(self, *, at: datetime) -> bool:
        """期限を過ぎているか。

        Args:
            at: 判定時刻。
        """
        deadline = self.deadline
        return deadline is not None and ensure_utc(at) > deadline

    def _transition(
        self,
        to: ExpectationStatus,
        *,
        at: datetime,
        note: str,
        updates: dict[str, object] | None = None,
    ) -> Expectation:
        """状態を進める（許可された遷移のみ）。"""
        if to not in _ALLOWED[self.status]:
            allowed = sorted(status.value for status in _ALLOWED[self.status])
            raise ReasoningError(
                f"cannot move expectation {self.expectation_id} from {self.status.value} to "
                f"{to.value}; allowed: {allowed or ['(terminal)']}"
            )
        moment = ensure_utc(at)
        payload = {
            "status": to,
            "history": (*self.history, (to.value, moment, note)),
            **(updates or {}),
        }
        updated = Expectation.model_validate(self.model_copy(update=payload).model_dump())
        log.info(
            "reasoning.expectation_transition",
            expectation=str(self.expectation_id),
            action_type=self.action_type,
            from_status=self.status.value,
            to_status=to.value,
        )
        return updated


@dataclass(frozen=True, slots=True)
class VerificationInputs:
    """検証に必要な観測（呼び出し側が集める）。

    Attributes:
        at: 検証時刻。
        beliefs: フルーエント型 IRI → 観測された確率（信念更新の出力）。
        records_present: 記録型 IRI → その記録が存在するか。
    """

    at: datetime
    beliefs: dict[str, float] = field(default_factory=dict)
    records_present: dict[str, bool] = field(default_factory=dict)


def verify_expectation(
    expectation: Expectation,
    *,
    inputs: VerificationInputs,
    method: VerificationMethod = VerificationMethod.CONFORMAL,
    bayes_threshold: float = _MIN_BAYES_FACTOR,
) -> Expectation:
    """記号側・潜在側を別々に検証して判定する（設計書 §8.5）。

    期限を過ぎていて観測が足りない効果があれば ``Undetermined`` になる。

    Args:
        expectation: ``Active`` の期待。
        inputs: 観測。
        method: 判定方法。
        bayes_threshold: ベイズ因子で違反とみなす下限。

    Returns:
        判定後の期待。

    Raises:
        ReasoningError: 状態が ``Active`` でない場合。
    """
    if expectation.status is not ExpectationStatus.ACTIVE:
        raise ReasoningError(
            f"only an Active expectation can be verified, got {expectation.status.value}"
        )

    observations: dict[str, float] = {}
    statistic: float | None = None
    notes: list[str] = []

    def judge(effects: tuple[ExpectedEffect, ...]) -> bool | None:
        """1 つの由来の期待をまとめて判定する（``None`` は判定不能）。"""
        nonlocal statistic
        if not effects:
            return None
        verdicts: list[bool] = []
        for effect in effects:
            observed = _observe(effect, inputs)
            if observed is None:
                notes.append(f"{effect.description or effect.fluent}: 観測なし")
                return None
            observations[effect.fluent] = observed
            if method is VerificationMethod.CONFORMAL:
                passed = effect.covers(observed)
            else:
                factor = bayes_factor(
                    observed, expected=effect.expected, alternative=1.0 - effect.expected
                )
                statistic = factor if statistic is None else max(statistic, factor)
                passed = factor < bayes_threshold
            verdicts.append(passed)
            if not passed:
                notes.append(
                    f"{effect.description or effect.fluent}: 観測 {observed:.3f} が期待 "
                    f"[{effect.lower:.3f}, {effect.upper:.3f}] の外"
                )
        return all(verdicts)

    symbolic = judge(expectation.effects_of("symbolic"))
    rollout = judge(expectation.effects_of("rollout"))

    # 期限切れの判定不能は ``Undetermined``。**ただし宣言された面だけを数え、外れた面があれば
    # 違反を優先する**——効果を持たない面は「観測できない」ではなく「期待していない」で、
    # 外れが分かっている期待を証拠不足にすると違反が学習信号に出ない（所見 119）。
    declared = {
        "symbolic": (symbolic, bool(expectation.effects_of("symbolic"))),
        "rollout": (rollout, bool(expectation.effects_of("rollout"))),
    }
    verdicts = [value for value, has_effects in declared.values() if has_effects]
    unobserved = any(value is None for value in verdicts)
    refuted = any(value is False for value in verdicts)
    if unobserved and not refuted and expectation.is_overdue(at=inputs.at):
        return expectation.expire(
            at=inputs.at, note="期限切れ: " + "; ".join(notes or ["証拠不足"])
        )

    result = VerificationResult(
        method=method,
        verified_at=ensure_utc(inputs.at),
        symbolic_passed=symbolic,
        rollout_passed=rollout,
        observations=observations,
        statistic=statistic,
        note="; ".join(notes) if notes else "期待どおり",
    )
    verified = expectation.verify(result, at=inputs.at)
    if result.blames_the_world_model:
        log.info(
            "reasoning.expectation_blames_world_model",
            expectation=str(expectation.expectation_id),
            action_type=expectation.action_type,
        )
    elif result.blames_the_process_ontology:
        log.info(
            "reasoning.expectation_blames_process_ontology",
            expectation=str(expectation.expectation_id),
            action_type=expectation.action_type,
        )
    return verified


def _observe(effect: ExpectedEffect, inputs: VerificationInputs) -> float | None:
    """効果に対応する観測値を取り出す（無ければ ``None``）。"""
    if effect.origin == "symbolic":
        present = inputs.records_present.get(effect.fluent)
        return None if present is None else (1.0 if present else 0.0)
    return inputs.beliefs.get(effect.fluent)
