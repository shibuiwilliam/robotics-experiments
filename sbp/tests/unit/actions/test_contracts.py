"""アクション意味契約の単体テスト（設計書 §9.1、M5-01）。

重点:

1. **補償が無い契約は登録できない** — 取り消せないアクションを基盤から出さない。
2. **補償の相互参照をロード時に検証する** — 実行時に「補償の契約が無い」と気付くのは遅い。
3. **効果が無い契約は登録できない** — 検証も学習も起きないアクションは意味契約でない。
4. **medium 以上で承認不要にできない** — 設計書 §9.3 の安全ガードを宣言で破れないようにする。
5. **CURIE は展開される** — 展開漏れは「前提が常に未達」という静かな故障になる。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from sbp.actions import (
    ActionContract,
    ActionRegistry,
    ApprovalTrigger,
    RiskClass,
    parse_duration,
)
from sbp.core import vocab
from sbp.core.errors import ActionError, CompensationMissingError

REPO_ROOT = Path(__file__).resolve().parents[3]


def _contract(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action_type": "ent:CreateEmergencyInspection",
        "targets": "ent:Pump",
        "risk_class": "medium",
        "compensation": "ent:CancelWorkOrder",
        "preconditions": {
            "symbolic": [],
            "physical": [
                {
                    "kind": "belief_at_least",
                    "fluent": "ent:Accessible",
                    "threshold": 0.8,
                    "description": "近づけること",
                }
            ],
            "normative": [],
        },
        "effects": {
            "business": [
                {
                    "kind": "record_created",
                    "record_type": "ent:WorkOrder",
                    "within": "PT5M",
                    "description": "作業指示が起票される",
                }
            ],
            "physical": [],
        },
        "approvals": {"required_if": "always", "role": "ent:MaintenanceLead"},
    }
    base.update(overrides)
    return base


class TestDurationParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("PT5M", timedelta(minutes=5)),
            ("PT24H", timedelta(hours=24)),
            ("P1D", timedelta(days=1)),
        ],
    )
    def test_it_parses_iso_durations(self, text: str, expected: timedelta) -> None:
        assert parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["P1Y", "P1M", "5 minutes", ""])
    def test_years_and_months_are_rejected(self, text: str) -> None:
        """年・月は長さが文脈依存で、期限の計算が曖昧になる。"""
        with pytest.raises(ActionError, match="cannot parse duration"):
            parse_duration(text)

    def test_a_zero_duration_is_rejected(self) -> None:
        """期限 0 の効果は検証できない。"""
        with pytest.raises(ActionError, match="not positive"):
            parse_duration("PT0S")


class TestContractValidation:
    def test_a_valid_contract_expands_its_curies(self) -> None:
        """展開漏れは「前提が常に未達」という静かな故障になる。"""
        contract = ActionContract.model_validate(_contract())
        assert contract.action_type == vocab.ent.CreateEmergencyInspection
        assert contract.compensation == vocab.ent.CancelWorkOrder
        assert contract.approvals.role == vocab.ent.MaintenanceLead
        assert contract.all_preconditions[0].fluent == vocab.ent.Accessible
        assert contract.all_effects[0].record_type == vocab.ent.WorkOrder

    def test_a_contract_cannot_compensate_itself(self) -> None:
        with pytest.raises(CompensationMissingError, match="cannot undo"):
            ActionContract.model_validate(_contract(compensation="ent:CreateEmergencyInspection"))

    def test_a_contract_without_effects_is_rejected(self) -> None:
        """効果が無いと Expectation が作れず、検証も学習も起きない。"""
        with pytest.raises(ActionError, match="declares no effects"):
            ActionContract.model_validate(_contract(effects={"business": [], "physical": []}))

    def test_medium_risk_cannot_waive_approval(self) -> None:
        """設計書 §9.3: riskClass ≥ medium は必ず承認。"""
        with pytest.raises(ActionError, match="requires no approval"):
            ActionContract.model_validate(
                _contract(approvals={"required_if": "never", "role": "ent:MaintenanceLead"})
            )

    def test_a_condition_declared_under_the_wrong_face_is_rejected(self) -> None:
        """面の宣言と条件の種別が食い違うと、三面検証の意味が崩れる。"""
        with pytest.raises(ActionError, match="belongs to the physical face"):
            ActionContract.model_validate(
                _contract(
                    preconditions={
                        "symbolic": [
                            {
                                "kind": "belief_at_least",
                                "fluent": "ent:Accessible",
                                "threshold": 0.8,
                                "description": "近づけること",
                            }
                        ],
                        "physical": [],
                        "normative": [],
                    }
                )
            )

    def test_a_belief_condition_needs_a_threshold(self) -> None:
        with pytest.raises(ActionError, match="needs both fluent and threshold"):
            ActionContract.model_validate(
                _contract(
                    preconditions={
                        "symbolic": [],
                        "physical": [
                            {
                                "kind": "belief_at_least",
                                "fluent": "ent:Accessible",
                                "description": "閾値なし",
                            }
                        ],
                        "normative": [],
                    }
                )
            )

    def test_a_norm_condition_needs_a_rule_reference(self) -> None:
        """規則名が無いと、規則エンジンの結果と突き合わせられない。"""
        with pytest.raises(ActionError, match="needs a rule_ref"):
            ActionContract.model_validate(
                _contract(
                    preconditions={
                        "symbolic": [],
                        "physical": [],
                        "normative": [{"kind": "norm_applies", "description": "規則名なし"}],
                    }
                )
            )


class TestApprovalDecision:
    def test_medium_risk_always_needs_approval(self) -> None:
        contract = ActionContract.model_validate(_contract())
        assert contract.requires_approval(severity=0.0)
        assert contract.requires_approval(severity=1.0)

    def test_low_risk_follows_the_declared_trigger(self) -> None:
        contract = ActionContract.model_validate(
            _contract(
                risk_class="low",
                approvals={"required_if": "severity_at_least_high", "role": "ent:MaintenanceLead"},
            )
        )
        assert not contract.requires_approval(severity=0.3)
        assert contract.requires_approval(severity=0.8)

    def test_low_risk_with_never_needs_no_approval(self) -> None:
        contract = ActionContract.model_validate(
            _contract(
                risk_class="low",
                approvals={"required_if": "never", "role": "ent:MaintenanceLead"},
            )
        )
        assert not contract.requires_approval(severity=1.0)
        assert contract.approvals.required_if is ApprovalTrigger.NEVER


class TestRegistry:
    def test_the_repository_contracts_load(self) -> None:
        registry = ActionRegistry.from_directory(REPO_ROOT / "actions")
        assert vocab.ent.CreateEmergencyInspection in registry.types()
        assert len(registry.types()) >= 4

    def test_every_compensation_has_its_own_contract(self) -> None:
        """実行時に「補償しようとしたら契約が無い」と気付くのは遅すぎる。"""
        registry = ActionRegistry.from_directory(REPO_ROOT / "actions")
        for action_type in registry.types():
            compensation = registry.compensation_for(action_type)
            assert compensation.action_type == registry.get(action_type).compensation

    def test_a_dangling_compensation_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "broken.yaml").write_text(
            """
actions:
  - action_type: ent:CreateEmergencyInspection
    targets: ent:Pump
    risk_class: low
    compensation: ent:CancelWorkOrder
    preconditions: { symbolic: [], physical: [], normative: [] }
    effects:
      business:
        - kind: record_created
          record_type: ent:WorkOrder
          within: PT5M
          description: 起票される
      physical: []
    approvals: { required_if: never, role: ent:MaintenanceLead }
""",
            encoding="utf-8",
        )
        with pytest.raises(CompensationMissingError, match="has no contract"):
            ActionRegistry.from_directory(tmp_path)

    def test_an_empty_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ActionError, match="no action contracts found"):
            ActionRegistry.from_directory(tmp_path)

    def test_an_unknown_action_type_fails_loudly(self) -> None:
        registry = ActionRegistry.from_directory(REPO_ROOT / "actions")
        with pytest.raises(ActionError, match="unknown action type"):
            registry.get("https://sbp.example/ent#NotAnAction")

    def test_the_repository_contracts_declare_both_effect_origins(self) -> None:
        """記号側期待と潜在側期待を別々に検証するため（設計書 §8.5）。"""
        registry = ActionRegistry.from_directory(REPO_ROOT / "actions")
        inspection = registry.get(vocab.ent.CreateEmergencyInspection)
        origins = {effect.origin for effect in inspection.all_effects}
        assert origins == {"symbolic", "rollout"}

    def test_risk_classes_are_declared(self) -> None:
        registry = ActionRegistry.from_directory(REPO_ROOT / "actions")
        assert registry.get(vocab.ent.CreateEmergencyInspection).risk_class is RiskClass.MEDIUM
        assert registry.get(vocab.ent.CancelWorkOrder).risk_class is RiskClass.LOW
