"""S8 キネティック層の統合: 送信基準の拒否・custody 再構成・SHACL 適合・agent stub smoke。"""

from __future__ import annotations

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.providers import ProviderConfig, make_llm_client
from orx.common.seeding import SeedTree
from orx.exp.act_loop import reconstruct_custody
from orx.exp.suites.s8_fulfillment import reference
from orx.exp.suites.s8_fulfillment.agent import AGENT_CONDITIONS, run_condition_llm
from orx.exp.suites.s8_fulfillment.generator import (
    box_positions,
    build_ontology_view,
    build_truth,
    build_vendor_view,
)
from orx.exp.suites.s8_fulfillment.validation import or_validator
from orx.skills.action import ValidationResult
from orx.skills.server import SkillRequest

WORLD = repo_root() / "configs" / "world" / "s8_fulfillment.yaml"


def _world() -> WorldConfig:
    return load_config(WORLD, WorldConfig)


def test_or_validator_rejects_underpowered_robot() -> None:
    world = _world()
    ov = build_ontology_view(world)
    validate = or_validator(ov)
    pos = box_positions(world)
    bad = SkillRequest(
        robot_id="r_light",
        skill="pick_and_place",
        target_barcode="BC-HV",
        target_position=pos["BC-HV"],
        dest_zone="dock",
    )
    verdict: ValidationResult = validate(bad)
    assert verdict.ok is False
    assert "能力不足" in verdict.reason


def test_or_validator_rejects_forbidden_zone() -> None:
    world = _world()
    validate = or_validator(build_ontology_view(world))
    pos = box_positions(world)
    bad = SkillRequest(
        robot_id="r_heavy",
        skill="pick_and_place",
        target_barcode="BC-RG",
        target_position=pos["BC-RG"],
        dest_zone="atrium",  # 規制物を禁止ゾーンへ
    )
    verdict = validate(bad)
    assert verdict.ok is False
    assert "規範違反" in verdict.reason


def test_or_full_custody_chain_is_reconstructable() -> None:
    """OR-full は全 move の custody を残し、SPARQL で監査連鎖を再構成できる（H8）。"""
    world = _world()
    ov, vv = build_ontology_view(world), build_vendor_view(world)
    _final, _receipts, _wb, graph, _rec = reference.run_condition(
        "OR-full", world, ov, vv, SeedTree(101)
    )
    chains = reconstruct_custody(graph, at_time=100.0)
    from orx.common import iri

    for bc in ("BC-HV", "BC-RG", "BC-PI"):
        assert iri.entity("object", bc) in chains
    # OR-full グラフは ActionExecution/CustodyStep shape に適合
    conforms, report = graph.validate_shacl()
    assert conforms, report


def test_baseline_leaves_no_custody() -> None:
    world = _world()
    ov, vv = build_ontology_view(world), build_vendor_view(world)
    _final, _receipts, _wb, graph, _rec = reference.run_condition(
        "B1", world, ov, vv, SeedTree(101)
    )
    assert reconstruct_custody(graph, at_time=100.0) == {}


def test_agent_stub_smoke_runs() -> None:
    """agent 経路（K2）が stub で例外なく走り、採点スキーマを返す（配線 smoke）。"""
    world = _world()
    ov, vv, truth = build_ontology_view(world), build_vendor_view(world), build_truth(world)
    llm = make_llm_client(ProviderConfig(mode="stub"))
    for cond in AGENT_CONDITIONS:
        score, tokens = run_condition_llm(cond, world, ov, vv, llm, SeedTree(101), truth)
        assert 0.0 <= score.completion <= 1.0
        assert tokens >= 0
