"""アクションの意味契約（設計書 §9.1、M5-01）。

契約は ``actions/*.yaml`` に宣言し、ここで pydantic により検証する。**補償が宣言されていない
アクション型は登録を拒否する**（アクション層ルール）——取り消せないアクションを基盤から出さない
ためで、既定値を与えずに必須フィールドにしている。

**前提が三面に分かれている理由**（設計書 §9.2）。記号（帳簿の状態）だけ見ると「書類上は問題
ないが物理的に危険」なアクションが通り、物理（信念）だけ見ると「安全だが規程違反」が通る。
三つを AND で要求し、**どの面で落ちたかを結果に残す**（人が上書きするときの判断材料になる）。

**効果が期限付きである理由**（設計書 §8.5）。「点検されるはず」では検証できない。期限と閾値が
なければ Expectation は永遠に ``Active`` のままで、検証も学習も起きない。

閾値は YAML 側に置く。オントロジーに置くと、閾値の調整がオントロジーの版上げと再接地を伴う。
"""

from __future__ import annotations

import re
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import ActionError, CompensationMissingError
from sbp.core.logging import get_logger

__all__ = [
    "ActionContract",
    "ActionRegistry",
    "ApprovalPolicy",
    "ApprovalTrigger",
    "Effect",
    "Precondition",
    "RateLimit",
    "RiskClass",
    "parse_duration",
]

log = get_logger(__name__)

_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
"""ISO 8601 の期間（``PT5M``, ``PT24H``, ``P1D`` など）。年・月は扱わない——長さが文脈依存で、
期限の計算が曖昧になる。"""


def parse_duration(value: str) -> timedelta:
    """ISO 8601 の期間を ``timedelta`` にする。

    Args:
        value: ``PT5M`` のような文字列。

    Raises:
        ActionError: 解釈できない、または長さ 0 の場合（期限 0 の効果は検証できない）。
    """
    match = _DURATION.match(value)
    if match is None:
        raise ActionError(
            f"cannot parse duration {value!r}; use ISO 8601 without years or months "
            "(their length depends on context, which makes deadlines ambiguous)"
        )
    parts = {key: int(group) for key, group in match.groupdict(default="0").items()}
    duration = timedelta(
        days=parts["days"],
        hours=parts["hours"],
        minutes=parts["minutes"],
        seconds=parts["seconds"],
    )
    if duration <= timedelta(0):
        raise ActionError(
            f"duration {value!r} is not positive; an effect with no deadline cannot be verified"
        )
    return duration


class RiskClass(StrEnum):
    """アクションのリスク級（設計書 §9.3）。"""

    LOW = "low"
    """誤って実行しても業務が止まらない。"""
    MEDIUM = "medium"
    """業務・コストに影響する。**承認必須**（設計書 §9.3）。"""
    HIGH = "high"
    """安全・法令に関わる。承認必須かつ人の立会いを想定する。"""

    @property
    def requires_approval(self) -> bool:
        """承認が必須か。``medium`` 以上は無条件に必須（設計書 §9.3）。"""
        return self in {RiskClass.MEDIUM, RiskClass.HIGH}


class ApprovalTrigger(StrEnum):
    """承認を要求する条件。"""

    NEVER = "never"
    """承認不要（``risk_class`` が ``low`` のときのみ有効）。"""
    SEVERITY_AT_LEAST_HIGH = "severity_at_least_high"
    """起因となった不一致の深刻度が高いときに承認を要求する。"""
    ALWAYS = "always"
    """常に承認を要求する。"""


class Precondition(BaseModel):
    """前提の 1 条件。

    Attributes:
        kind: 条件の種別。
        description: 人が読む説明（承認画面と来歴に出る）。
        fluent: 物理条件が見るフルーエント型 IRI。
        threshold: 物理条件の閾値。
        record_type: 記号条件が見る記録型 IRI。
        where: 記号条件の絞り込み（列 → 値）。
        rule_ref: 規範条件が参照する規則名（``ontology/norms/*.lp``）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "belief_at_least",
        "belief_at_most",
        "absent_record",
        "present_record",
        "norm_applies",
    ]
    description: str = Field(min_length=1)
    fluent: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    record_type: str | None = None
    where: dict[str, Any] = Field(default_factory=dict)
    rule_ref: str | None = None

    @model_validator(mode="after")
    def _kind_has_its_fields(self) -> Self:
        """種別ごとに必要なフィールドが揃っているか検証する。"""
        if self.kind.startswith("belief_") and (self.fluent is None or self.threshold is None):
            raise ActionError(
                f"a {self.kind} precondition needs both fluent and threshold ({self.description!r})"
            )
        if self.kind.endswith("_record") and self.record_type is None:
            raise ActionError(
                f"a {self.kind} precondition needs a record_type ({self.description!r})"
            )
        if self.kind == "norm_applies" and not self.rule_ref:
            raise ActionError(
                f"a norm_applies precondition needs a rule_ref so the rule engine can evaluate "
                f"it ({self.description!r})"
            )
        return self

    @property
    def face(self) -> Literal["symbolic", "physical", "normative"]:
        """この条件がどの面に属するか。"""
        if self.kind.startswith("belief_"):
            return "physical"
        if self.kind == "norm_applies":
            return "normative"
        return "symbolic"


class Effect(BaseModel):
    """期待される効果の 1 件（期限付き）。

    Attributes:
        kind: 効果の種別。
        description: 人が読む説明。
        within: 期限（ISO 8601 の期間）。
        fluent: 物理効果が対象とするフルーエント型 IRI。
        threshold: 物理効果の閾値。
        record_type: 業務効果が対象とする記録型 IRI。
        where: 業務効果の条件（列 → 値）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["belief_at_least", "belief_at_most", "record_created", "record_updated"]
    description: str = Field(min_length=1)
    within: str
    fluent: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    record_type: str | None = None
    where: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _deadline_and_fields_are_valid(self) -> Self:
        """期限が解釈でき、種別に必要なフィールドが揃っているか検証する。"""
        parse_duration(self.within)
        if self.kind.startswith("belief_") and (self.fluent is None or self.threshold is None):
            raise ActionError(
                f"a {self.kind} effect needs both fluent and threshold ({self.description!r})"
            )
        if self.kind.startswith("record_") and self.record_type is None:
            raise ActionError(f"a {self.kind} effect needs a record_type ({self.description!r})")
        return self

    @property
    def deadline(self) -> timedelta:
        """期限（``timedelta``）。"""
        return parse_duration(self.within)

    @property
    def origin(self) -> Literal["symbolic", "rollout"]:
        """この効果がどちら由来の期待になるか（設計書 §8.5）。

        業務効果は記号側（T 由来）、物理効果は潜在側（rollout 由来）として別々に検証する
        ——WM とプロセスオントロジーのどちらが外れたかを切り分けるため。
        """
        return "symbolic" if self.kind.startswith("record_") else "rollout"


class ApprovalPolicy(BaseModel):
    """承認の方針（設計書 §9.1 の ``approvals``）。

    Attributes:
        required_if: 承認を要求する条件。
        role: 承認者のロール IRI（個人ではなくロール）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    required_if: ApprovalTrigger
    role: str = Field(min_length=1)


class RateLimit(BaseModel):
    """同一対象への反復の制限（設計書 §9.3）。

    Attributes:
        per_target_window: 制限を見る時間窓。
        per_target_max: 窓内に許す実行回数。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    per_target_window: str
    per_target_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _window_is_valid(self) -> Self:
        """窓が解釈できるか検証する。"""
        parse_duration(self.per_target_window)
        return self

    @property
    def window(self) -> timedelta:
        """時間窓（``timedelta``）。"""
        return parse_duration(self.per_target_window)


class ActionContract(BaseModel):
    """1 アクション型の意味契約（設計書 §9.1）。

    Attributes:
        action_type: アクション型 IRI。
        targets: 対象の型 IRI。
        risk_class: リスク級。
        compensation: 補償アクション型 IRI。**必須**。
        preconditions: 面ごとの前提（``symbolic`` / ``physical`` / ``normative``）。
        effects: 面ごとの効果（``business`` / ``physical``）。
        approvals: 承認方針。
        rate_limit: レート制限（``None`` なら無制限）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_type: str
    targets: str
    risk_class: RiskClass
    compensation: str
    preconditions: dict[Literal["symbolic", "physical", "normative"], tuple[Precondition, ...]]
    effects: dict[Literal["business", "physical"], tuple[Effect, ...]]
    approvals: ApprovalPolicy
    rate_limit: RateLimit | None = None

    @model_validator(mode="after")
    def _expand_and_validate(self) -> Self:
        """CURIE を展開し、契約として成立しているか検証する。"""
        from sbp.integration.mappings import expand_curie  # noqa: PLC0415 — 循環 import 回避

        for field in ("action_type", "targets", "compensation"):
            object.__setattr__(self, field, expand_curie(getattr(self, field)))
        object.__setattr__(
            self,
            "approvals",
            self.approvals.model_copy(update={"role": expand_curie(self.approvals.role)}),
        )
        # 前提・効果の中の CURIE も展開する。ここを忘れると、呼び出し側が渡す完全 IRI の
        # 信念と ``ent:Accessible`` のような CURIE が一致せず、**前提が常に「信念が無い」
        # 扱いになって全アクションが実行不能になる**（しかもエラーではなく「未達」として
        # 静かに落ちる）。
        object.__setattr__(
            self,
            "preconditions",
            {
                face: tuple(_expand_condition(condition) for condition in conditions)
                for face, conditions in self.preconditions.items()
            },
        )
        object.__setattr__(
            self,
            "effects",
            {
                face: tuple(_expand_effect(effect) for effect in effects)
                for face, effects in self.effects.items()
            },
        )
        if not self.compensation:
            raise CompensationMissingError(
                f"{self.action_type} declares no compensation; actions that cannot be undone are "
                "not registered (design §9.1)"
            )
        if self.compensation == self.action_type:
            raise CompensationMissingError(
                f"{self.action_type} names itself as its compensation; an action cannot undo itself"
            )
        if not any(self.effects.values()):
            raise ActionError(
                f"{self.action_type} declares no effects; an action with no expected effect "
                "cannot be verified and produces no learning signal (design §8.5)"
            )
        if self.risk_class.requires_approval and self.approvals.required_if is (
            ApprovalTrigger.NEVER
        ):
            raise ActionError(
                f"{self.action_type} is risk class {self.risk_class.value} but requires no "
                "approval; medium and above always need one (design §9.3)"
            )
        for face in ("symbolic", "physical", "normative"):
            for condition in self.preconditions.get(face, ()):
                if condition.face != face:
                    raise ActionError(
                        f"{self.action_type}: a {condition.kind} precondition belongs to the "
                        f"{condition.face} face but is declared under {face}"
                    )
        return self

    @property
    def all_preconditions(self) -> tuple[Precondition, ...]:
        """全面の前提を平らにして返す。"""
        return tuple(
            condition
            for face in ("symbolic", "physical", "normative")
            for condition in self.preconditions.get(face, ())
        )

    @property
    def all_effects(self) -> tuple[Effect, ...]:
        """全面の効果を平らにして返す。"""
        return tuple(
            effect for face in ("business", "physical") for effect in self.effects.get(face, ())
        )

    def requires_approval(self, *, severity: float) -> bool:
        """この実行に承認が要るか。

        ``risk_class`` が ``medium`` 以上なら深刻度に依らず必須（設計書 §9.3）。``low`` の
        場合だけ ``required_if`` が効く。

        Args:
            severity: 起因となった不一致の深刻度（0〜1）。
        """
        if self.risk_class.requires_approval:
            return True
        if self.approvals.required_if is ApprovalTrigger.ALWAYS:
            return True
        if self.approvals.required_if is ApprovalTrigger.SEVERITY_AT_LEAST_HIGH:
            return severity >= 0.7
        return False


def _expand_condition(condition: Precondition) -> Precondition:
    """前提の中の CURIE を IRI に展開する。"""
    from sbp.integration.mappings import expand_curie  # noqa: PLC0415 — 循環 import 回避

    updates: dict[str, str] = {}
    if condition.fluent is not None:
        updates["fluent"] = expand_curie(condition.fluent)
    if condition.record_type is not None:
        updates["record_type"] = expand_curie(condition.record_type)
    return condition.model_copy(update=updates) if updates else condition


def _expand_effect(effect: Effect) -> Effect:
    """効果の中の CURIE を IRI に展開する。"""
    from sbp.integration.mappings import expand_curie  # noqa: PLC0415 — 循環 import 回避

    updates: dict[str, str] = {}
    if effect.fluent is not None:
        updates["fluent"] = expand_curie(effect.fluent)
    if effect.record_type is not None:
        updates["record_type"] = expand_curie(effect.record_type)
    return effect.model_copy(update=updates) if updates else effect


class ActionRegistry:
    """アクション契約の一覧（``actions/*.yaml`` から読む）。

    補償の相互参照をロード時に検証する。片方だけが登録されている状態を許すと、実行時に
    「補償しようとしたら契約が無い」ことに気付く——取り消せない実行になる。
    """

    def __init__(self, contracts: dict[str, ActionContract]) -> None:
        """Args:
        contracts: アクション型 IRI → 契約。
        """
        self._contracts = contracts

    @classmethod
    def from_directory(cls, directory: Path) -> ActionRegistry:
        """ディレクトリ内の全契約を読み込む。

        Args:
            directory: ``actions/`` のパス。

        Raises:
            ActionError: YAML が壊れている、鍵が重複している、契約が無い場合。
            CompensationMissingError: 補償アクションの契約が登録されていない場合。
        """
        contracts: dict[str, ActionContract] = {}
        for path in sorted(directory.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            for entry in payload.get("actions", []):
                try:
                    contract = ActionContract.model_validate(entry)
                except Exception as exc:
                    raise ActionError(f"invalid action contract in {path.name}: {exc}") from exc
                if contract.action_type in contracts:
                    raise ActionError(
                        f"duplicate action contract for {contract.action_type} in {path.name}"
                    )
                contracts[contract.action_type] = contract
        if not contracts:
            raise ActionError(f"no action contracts found under {directory}")

        for contract in contracts.values():
            if contract.compensation not in contracts:
                raise CompensationMissingError(
                    f"{contract.action_type} names {contract.compensation} as its compensation "
                    "but that action has no contract; a compensation that cannot be executed is "
                    "not a compensation"
                )
        log.info("actions.contracts_loaded", count=len(contracts))
        return cls(contracts)

    def get(self, action_type: str) -> ActionContract:
        """アクション型で契約を引く。

        Args:
            action_type: アクション型 IRI。

        Raises:
            ActionError: 未登録のアクション型。
        """
        try:
            return self._contracts[action_type]
        except KeyError as exc:
            raise ActionError(
                f"unknown action type {action_type}; registered: {sorted(self._contracts)}"
            ) from exc

    def compensation_for(self, action_type: str) -> ActionContract:
        """補償アクションの契約を引く。

        Args:
            action_type: 補償される側のアクション型 IRI。
        """
        return self.get(self.get(action_type).compensation)

    def types(self) -> tuple[str, ...]:
        """登録済みのアクション型。"""
        return tuple(sorted(self._contracts))
