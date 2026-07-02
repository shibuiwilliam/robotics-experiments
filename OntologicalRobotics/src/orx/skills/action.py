"""C2 — アクション型（キネティック層）: 検証→ステージング→実行→効果→アンドゥ。

`ontology.md` の Palantir アクション型を擬似VLAスキルサーバの上に実装する。
エージェント（C7）が発行する `SkillRequest` を、**世界グラフに対する送信基準**で検証し、
ステージング（人間確認へ委譲）または実行し、成功時に **EffectSink** 経由で世界状態を
変更する。実行の確率的成否は既存の `SkillServer`（唯一の真値接点）に委ねる。

import-linter 制約（CLAUDE.md §3）: 本モジュールは `orx.kg`/`orx.agent`/`orx.exp` を
import できない。よって送信基準（グラフ検証）と書戻し（Claim 生成）は**注入**で受け取り、
本モジュールは純粋に「検証関数を呼び・SkillServer を実行し・EffectSink に効果を渡す」だけを担う。
Claim 生成は `orx.kg.action_claims`、orchestration は `orx.exp.act_loop` が行う。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from orx.common.schemas import ActionReceiptRecord, ActionRequestRecord, StrictModel
from orx.skills.server import SkillOutcome, SkillRequest, SkillServer

ActionStatus = Literal["applied", "staged", "rejected"]


class ValidationResult(StrictModel):
    """送信基準（submission criteria）の評価結果。

    ok=True 即実行可 / ok=False かつ stage=False → 拒否（reject, 副作用なし）/
    stage=True → ステージング（人間確認へ ESCALATE。実行も効果も発生しない）。
    """

    ok: bool
    reason: str = ""
    stage: bool = False


def always_valid(_request: SkillRequest) -> ValidationResult:
    """検証なし（B0/B1 ベースライン: 前提条件を持たず不正アクションを実行してしまう）。"""
    return ValidationResult(ok=True)


# 送信基準は注入される（グラフ検証は agent/exp 層が供給。skills は kg に依存しない）。
Validator = Callable[[SkillRequest], ValidationResult]


class ActionReceipt(StrictModel):
    """アクション実行のレシート（監査ログ・採点・書戻しの単位）。"""

    action_id: str
    request: SkillRequest
    status: ActionStatus
    reason: str = ""  # rejected/staged の理由（グラフ検証由来）
    effect_applied: bool = False  # 物理（または truth）に効果が反映されたか
    failure_mode: str | None = None  # 実行失敗時のモード（SkillServer 由来）
    at_time: float = 0.0
    compensates: str | None = None  # アンドゥ時、補償対象アクションの action_id

    @property
    def object_id(self) -> str:
        return self.request.target_barcode

    def to_record(self) -> ActionReceiptRecord:
        """永続化用の純レコードへ変換する（replay ストリーム書込）。"""
        return ActionReceiptRecord(
            action_id=self.action_id,
            object_id=self.object_id,
            status=self.status,
            reason=self.reason,
            effect_applied=self.effect_applied,
            failure_mode=self.failure_mode,
            at_time=self.at_time,
            compensates=self.compensates,
        )

    def request_record(self) -> ActionRequestRecord:
        """発行アクション要求の純レコード（replay ストリーム書込）。"""
        return ActionRequestRecord(
            action_id=self.action_id,
            robot_id=self.request.robot_id,
            skill=self.request.skill,
            target_barcode=self.request.target_barcode,
            target_position=self.request.target_position,
            dest_zone=self.request.dest_zone,
            at_time=self.at_time,
        )


class EffectSink(Protocol):
    """アクション効果の適用先（物理 or 真値モデル）。成功で True。"""

    def apply(self, object_id: str, to_zone: str, at_time: float) -> bool: ...


class DictEffectSink:
    """object_id→zone の辞書を更新するだけの効果適用先（決定的採点・テスト用）。"""

    def __init__(self, state: dict[str, str] | None = None) -> None:
        self.state: dict[str, str] = dict(state or {})

    def apply(self, object_id: str, to_zone: str, _at_time: float) -> bool:
        self.state[object_id] = to_zone
        return True

    def zone_of(self, object_id: str) -> str | None:
        return self.state.get(object_id)


class ActionExecutor:
    """アクション型の実行器（検証→ステージング→実行→効果）。

    純粋: 送信基準（`validate`）と効果適用先（`effect`）は注入。真値には触れない
    （SkillServer が唯一の真値接点で、成否レシートと効果しか返さない — 不変条件5）。
    """

    def __init__(
        self,
        server: SkillServer,
        effect: EffectSink,
        validate: Validator = always_valid,
        action_prefix: str = "act",
    ) -> None:
        self._server = server
        self._effect = effect
        self._validate = validate
        self._prefix = action_prefix
        self._counter = 0
        self._applied: dict[str, str] = {}  # action_id -> 効果前の object のゾーン（アンドゥ用）

    def _next_id(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:04d}"

    def apply(
        self, request: SkillRequest, at_time: float, prior_zone: str | None = None
    ) -> ActionReceipt:
        """1 アクションを検証・実行する。`prior_zone` はアンドゥ用に記録する効果前ゾーン。"""
        action_id = self._next_id()
        verdict = self._validate(request)
        if not verdict.ok and not verdict.stage:
            return ActionReceipt(
                action_id=action_id,
                request=request,
                status="rejected",
                reason=verdict.reason,
                at_time=at_time,
            )
        if verdict.stage:
            return ActionReceipt(
                action_id=action_id,
                request=request,
                status="staged",
                reason=verdict.reason,
                at_time=at_time,
            )
        outcome: SkillOutcome = self._server.execute(request, sim_time=at_time)
        effect_applied = False
        if outcome.success:
            effect_applied = self._effect.apply(request.target_barcode, request.dest_zone, at_time)
            if effect_applied and prior_zone is not None:
                self._applied[action_id] = prior_zone
        return ActionReceipt(
            action_id=action_id,
            request=request,
            status="applied",
            effect_applied=effect_applied,
            failure_mode=outcome.failure_mode,
            at_time=at_time,
        )

    def compensate(self, receipt: ActionReceipt, prior_zone: str, at_time: float) -> ActionReceipt:
        """適用済みアクションをアンドゥする（逆効果＝対象を `prior_zone` へ戻す）。

        補償レシートは status="applied"・compensates=元 action_id。元 effect claim の
        失効と補償 ActionExecution の書戻しは exp/act_loop が行う。
        """
        if not receipt.effect_applied:
            raise ValueError(f"効果未適用のアクション {receipt.action_id!r} はアンドゥできない")
        comp_id = self._next_id()
        self._effect.apply(receipt.object_id, prior_zone, at_time)
        comp_request = receipt.request.model_copy(update={"dest_zone": prior_zone})
        return ActionReceipt(
            action_id=comp_id,
            request=comp_request,
            status="applied",
            effect_applied=True,
            at_time=at_time,
            compensates=receipt.action_id,
            reason="compensation",
        )


class SimEffectSink:
    """物理権威（C1）に効果を反映する EffectSink。バーコード→箱名の対応で `apply_effect` を呼ぶ。"""

    def __init__(
        self, world: object, barcode_to_box: dict[str, str], mode: str = "teleport"
    ) -> None:
        self._world = world
        self._barcode_to_box = barcode_to_box
        self._mode = mode

    def apply(self, object_id: str, to_zone: str, at_time: float) -> bool:
        box = self._barcode_to_box.get(object_id)
        if box is None:
            return False
        # SimWorld.apply_effect（C1）: tick 境界で物理反映（不変条件4）。
        self._world.apply_effect(box, to_zone, at_time, mode=self._mode)  # type: ignore[attr-defined]
        return True
