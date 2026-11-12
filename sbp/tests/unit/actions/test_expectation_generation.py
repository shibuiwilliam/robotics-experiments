"""契約から期待を作る（M8-20）。

**ここが運用経路に無かった。** 契約の効果を期待に変える手順は G5 の統合テストの中にだけ
あり、production には存在しなかった（所見 89）。

重点:

1. **区間は契約の境界から作る** — `belief_at_least` なら `[threshold, 1.0]`。
2. **期待値を契約より強くしない** — 契約を満たしているのに外れる期待を作らない。
3. **業務効果の下端は呼び出し側が渡す** — 既定を関数に埋めない（`Settings` と二重になる）。
4. **違う契約の効果を期待にしない** — 検証が無関係な面を見る。
5. **効果が無い契約は拒否する** — 永遠に `Active` な期待を作らない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sbp.actions.contracts import ActionContract, Effect
from sbp.actions.execute import ActionEvent
from sbp.actions.expectations import expectation_for, expected_effect_for
from sbp.core.errors import ActionError
from sbp.core.ids import new_event_id
from sbp.reasoning.expectation import ExpectationStatus

T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
ACTION = "https://sbp.example/ent#CreateEmergencyInspection"
OTHER = "https://sbp.example/ent#StopAsset"
INSPECTED = "https://sbp.example/ent#Inspected"
FAULTED = "https://sbp.example/ent#Faulted"
WORK_ORDER = "https://sbp.example/ent#WorkOrder"
FLOOR = 0.5


def _physical(*, kind: str = "belief_at_least", threshold: float = 0.9) -> Effect:
    return Effect.model_validate(
        {
            "kind": kind,
            "description": "24 時間以内に点検済みになる",
            "within": "PT24H",
            "fluent": INSPECTED if kind == "belief_at_least" else FAULTED,
            "threshold": threshold,
        }
    )


def _business(*, threshold: float | None = None) -> Effect:
    payload: dict[str, object] = {
        "kind": "record_created",
        "description": "5 分以内に点検作業指示が起票される",
        "within": "PT5M",
        "record_type": WORK_ORDER,
    }
    if threshold is not None:
        payload["threshold"] = threshold
    return Effect.model_validate(payload)


def _contract(**overrides: object) -> ActionContract:
    defaults: dict[str, object] = {
        "action_type": ACTION,
        "targets": "https://sbp.example/ent#Pump",
        "risk_class": "medium",
        "compensation": "https://sbp.example/ent#CancelEmergencyInspection",
        "preconditions": {},
        "effects": {"business": (_business(),), "physical": (_physical(),)},
        "approvals": {
            "required_if": "always",
            "role": "https://sbp.example/ent#MaintenanceLead",
        },
    }
    return ActionContract.model_validate(defaults | overrides)


def _event(*, action_type: str = ACTION) -> ActionEvent:
    return ActionEvent(
        event_id=new_event_id(),
        action_type=action_type,
        target=PUMP,
        command_id=uuid4(),
        executed_at=T0,
        executed_by="https://sbp.example/id/person/lead",
        approval=None,
        override=None,
        precondition_note="三面すべて満たした",
        correlation_id=uuid4(),
    )


class TestIntervalsComeFromTheContract:
    """区間は契約の境界から作る。"""

    def test_a_lower_bound_becomes_a_one_sided_interval(self) -> None:
        """``belief_at_least`` は ``[threshold, 1.0]`` になる。"""
        effect = expected_effect_for(
            _physical(threshold=0.9),
            entity=PUMP,
            executed_at=T0,
            record_effect_floor=FLOOR,
        )

        assert (effect.lower, effect.expected, effect.upper) == (0.9, 0.9, 1.0)
        assert effect.fluent == INSPECTED
        assert effect.deadline == T0 + timedelta(hours=24)
        assert effect.origin == "rollout"

    def test_an_upper_bound_becomes_the_mirrored_interval(self) -> None:
        """``belief_at_most`` は ``[0.0, threshold]`` になる。"""
        effect = expected_effect_for(
            _physical(kind="belief_at_most", threshold=0.2),
            entity=PUMP,
            executed_at=T0,
            record_effect_floor=FLOOR,
        )

        assert (effect.lower, effect.expected, effect.upper) == (0.0, 0.2, 0.2)

    def test_the_expectation_is_never_stronger_than_the_contract(self) -> None:
        """期待値が契約の境界そのものである（契約より強い値を置かない）。

        下限 0.9 の契約に期待値 0.95 を置くと、**契約を満たしているのに期待が外れる**——
        `Violated` が「契約が守られなかった」ではなく「我々の見積りが外れた」を意味する
        ようになり、学習信号の読み方が変わる。
        """
        for threshold in (0.5, 0.75, 0.9, 1.0):
            effect = expected_effect_for(
                _physical(threshold=threshold),
                entity=PUMP,
                executed_at=T0,
                record_effect_floor=FLOOR,
            )
            # 契約が満たされる最弱の値がちょうど区間に入る
            assert effect.covers(threshold), threshold

    def test_a_business_effect_carries_the_record_type_as_its_fluent(self) -> None:
        """業務効果は記録型が ``fluent`` に入る（``ExpectedEffect`` の契約）。"""
        effect = expected_effect_for(
            _business(), entity=PUMP, executed_at=T0, record_effect_floor=FLOOR
        )

        assert effect.fluent == WORK_ORDER
        assert effect.origin == "symbolic"
        assert effect.deadline == T0 + timedelta(minutes=5)


class TestTheBusinessEffectFloor:
    """業務効果の下端。"""

    def test_the_caller_supplies_the_floor(self) -> None:
        """呼び出し側が渡した下端が使われる（関数に既定を埋めない）。"""
        effect = expected_effect_for(
            _business(), entity=PUMP, executed_at=T0, record_effect_floor=0.3
        )

        assert (effect.lower, effect.expected, effect.upper) == (0.3, 0.3, 1.0)

    def test_the_contract_wins_over_the_floor(self) -> None:
        """契約が ``threshold`` を宣言していればそれを使う。

        下端は「契約が何も言っていないとき」の値であり、契約の宣言を上書きしない。
        """
        effect = expected_effect_for(
            _business(threshold=0.95), entity=PUMP, executed_at=T0, record_effect_floor=0.3
        )

        assert effect.lower == 0.95

    def test_a_floor_outside_the_unit_interval_is_refused(self) -> None:
        """確率でない下端を拒否する。"""
        with pytest.raises(ActionError, match="must be a probability"):
            expected_effect_for(_business(), entity=PUMP, executed_at=T0, record_effect_floor=1.5)


class TestBuildingTheExpectation:
    """``ActionEvent`` から期待を組む。"""

    def test_the_expectation_is_active_with_both_faces(self) -> None:
        """両面の期待効果を持つ ``Active`` な期待になる（設計書 §9.2 の 4）。"""
        event = _event()

        expectation = expectation_for(
            _contract(), event, ontology_version="0.5.0", record_effect_floor=FLOOR
        )

        assert expectation.status is ExpectationStatus.ACTIVE
        assert expectation.action_event == event.event_id
        assert expectation.target == PUMP
        assert expectation.created_at == T0
        assert len(expectation.effects_of("symbolic")) == 1
        assert len(expectation.effects_of("rollout")) == 1

    def test_the_overall_deadline_is_the_latest_effect(self) -> None:
        """全体の期限は最も遅い効果（記録 5 分 / 物理 24 時間 → 24 時間）。"""
        expectation = expectation_for(
            _contract(), _event(), ontology_version="0.5.0", record_effect_floor=FLOOR
        )

        assert expectation.deadline == T0 + timedelta(hours=24)

    def test_the_wrong_contract_is_refused(self) -> None:
        """実行した型と違う契約から期待を作らない。

        **黙って片方を信じない**——別の契約の効果を期待にすると、検証が無関係な面を見る。
        """
        with pytest.raises(ActionError, match="does not match the executed action"):
            expectation_for(
                _contract(),
                _event(action_type=OTHER),
                ontology_version="0.5.0",
                record_effect_floor=FLOOR,
            )

    def test_an_effectless_contract_never_reaches_here(self) -> None:
        """効果を宣言していない契約は**登録の時点で**拒否される。

        だから `expectation_for` に効果の無い契約は来ない。`activate` の「効果が空なら拒否」は
        二重の歯止めとして残してある（契約の検証が緩められたときに、永遠に ``Active`` な期待が
        作られるのを止める）——その歯止めが生きていることは
        `tests/unit/reasoning/test_expectation.py` が確かめている。
        """
        with pytest.raises(ActionError, match="declares no effects"):
            _contract(effects={})
