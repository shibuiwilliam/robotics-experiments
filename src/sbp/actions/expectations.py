"""意味契約と ``ActionEvent`` から ``Expectation`` を作る（M8-20、設計書 §9.2 の 4）。

**これまで production に無かった。** 契約の効果を期待に変える手順は G5 の統合テストの中で
インラインに組まれていて、運用経路には存在しなかった（所見 89）。ここに出す。

純粋な変換である——I/O を持たず、永続化もしない（永続化は呼び出し側が
:func:`sbp.reasoning.expectation_store.to_record` で行う）。

## 区間をどこから取るか

``ExpectedEffect`` は点（``expected``）と区間（``[lower, upper]``）を要求するが、**契約が
宣言しているのは境界だけである**（``threshold``）。だから:

- ``belief_at_least`` は片側区間 ``[threshold, 1.0]``、期待値は ``threshold``
  ——**契約が満たされる最弱の値**を期待値に置く。これで ``covers(observed)`` が
  「契約の下限が保たれたか」とそのまま一致する。
- ``belief_at_most`` は ``[0.0, threshold]``、期待値は ``threshold``（同じ理由で上限側）。
- 業務効果（``record_created`` / ``record_updated``）は契約が閾値を持たないことが多い。
  **記録の存在に対する信念は情報源の信頼性で割り引かれる**ので、下端を 1.0 にすると
  信頼性モデルが働いた時点で必ず違反になる。契約が ``threshold`` を宣言していればそれを、
  無ければ ``Settings`` の既定（``SBP_ACTIONS_RECORD_EFFECT_FLOOR``）を使う。

**期待値を「契約より強い値」にしない。** たとえば下限 0.9 の契約に対して期待値 0.95 を置くと、
契約を満たしているのに期待が外れる——学習信号が「契約が守られなかった」ではなく「我々の
勝手な見積りが外れた」を意味するようになり、`Violated` の読み方が変わってしまう。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.core.errors import ActionError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc
from sbp.reasoning.expectation import Expectation, ExpectedEffect

if TYPE_CHECKING:
    from datetime import datetime

    from sbp.actions.contracts import ActionContract, Effect
    from sbp.actions.execute import ActionEvent

__all__ = ["expectation_for", "expected_effect_for"]

log = get_logger(__name__)


def expected_effect_for(
    effect: Effect,
    *,
    entity: str,
    executed_at: datetime,
    record_effect_floor: float,
) -> ExpectedEffect:
    """契約の効果 1 件を期待効果にする。

    Args:
        effect: 契約の効果。
        entity: 対象実体の IRI。
        executed_at: 実行時刻（期限の起点）。
        record_effect_floor: 業務効果の下端（契約が ``threshold`` を宣言していないとき）。
            ``Settings.actions.record_effect_floor`` を渡す。

    Returns:
        期待効果。

    Raises:
        ActionError: 下端が確率でない場合。
    """
    if not 0.0 <= record_effect_floor <= 1.0:
        raise ActionError(f"record effect floor must be a probability, got {record_effect_floor}")
    moment = ensure_utc(executed_at)
    deadline = moment + effect.deadline

    if effect.kind == "belief_at_least":
        assert effect.fluent is not None and effect.threshold is not None  # 契約が検証済み
        return ExpectedEffect(
            fluent=effect.fluent,
            entity=entity,
            expected=effect.threshold,
            lower=effect.threshold,
            upper=1.0,
            deadline=deadline,
            origin=effect.origin,
            description=effect.description,
        )
    if effect.kind == "belief_at_most":
        assert effect.fluent is not None and effect.threshold is not None
        return ExpectedEffect(
            fluent=effect.fluent,
            entity=entity,
            expected=effect.threshold,
            lower=0.0,
            upper=effect.threshold,
            deadline=deadline,
            origin=effect.origin,
            description=effect.description,
        )

    # 業務効果。記録型が期待効果の `fluent` に入る（`ExpectedEffect` の契約）
    assert effect.record_type is not None  # 契約が検証済み
    floor = effect.threshold if effect.threshold is not None else record_effect_floor
    return ExpectedEffect(
        fluent=effect.record_type,
        entity=entity,
        expected=floor,
        lower=floor,
        upper=1.0,
        deadline=deadline,
        origin=effect.origin,
        description=effect.description,
    )


def expectation_for(
    contract: ActionContract,
    event: ActionEvent,
    *,
    ontology_version: str,
    record_effect_floor: float,
    note: str = "",
) -> Expectation:
    """実行した契約から ``Active`` な期待を作る（設計書 §9.2 の 4）。

    **``Created`` を経由して ``activate`` する。** 直接 ``Active`` で組み立てないのは、
    効果が空の契約を状態機械に弾かせるためである（``activate`` が「何も期待しない期待は
    永遠に Active のままで何も教えない」として拒否する）。

    Args:
        contract: 実行した契約。
        event: 実行の記録（期限の起点と対象をここから取る）。
        ontology_version: 期待を作った時点のオントロジー版（不変条件 1）。
        record_effect_floor: 業務効果の下端（``Settings.actions.record_effect_floor``）。
        note: 遷移の理由。

    Returns:
        ``Active`` な期待。

    Raises:
        ActionError: 契約と実行の記録のアクション型が食い違う場合。**黙って片方を信じない**
            ——別の契約の効果を期待にすると、検証が無関係な面を見る。
        ReasoningError: 契約が効果を 1 件も宣言していない場合（``activate`` が拒否する）。
    """
    if contract.action_type != event.action_type:
        raise ActionError(
            f"contract {contract.action_type} does not match the executed action "
            f"{event.action_type}; refusing to build expectations from the wrong contract"
        )

    effects = tuple(
        expected_effect_for(
            effect,
            entity=event.target,
            executed_at=event.executed_at,
            record_effect_floor=record_effect_floor,
        )
        for effect in contract.all_effects
    )
    created = Expectation(
        action_event=event.event_id,
        action_type=contract.action_type,
        target=event.target,
        created_at=event.executed_at,
        ontology_version=ontology_version,
    )
    expectation = created.activate(effects, at=event.executed_at, note=note)
    log.info(
        "actions.expectation_created",
        correlation_id=str(event.correlation_id) if event.correlation_id else None,
        event_id=str(event.event_id),
        entity=event.target,
        action_type=contract.action_type,
        expectation_id=str(expectation.expectation_id),
        symbolic_effects=len(expectation.effects_of("symbolic")),
        rollout_effects=len(expectation.effects_of("rollout")),
    )
    return expectation
