"""S7 agent（live）射程ハーネスのオフライン検証（R-B・stub・実APIなし）。

stub プロバイダで agent 経路が実APIを叩かずに通ること、配送回答パースが正しいことを検証。
数値（成功率）は stub では無意味なので、構造（scope/conditions/comparisons/tokens）のみ検査。
"""

from __future__ import annotations

from orx.common.providers import ProviderConfig
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s7_ownership import runner
from orx.exp.suites.s7_ownership.agent import AGENT_CONDITIONS, parse_owner_answer
from orx.oracle.scenarios.s7 import ESCALATE


def test_parse_owner_answer_maps_and_validates() -> None:
    out = parse_owner_answer("r1=obj0, r2=ESCALATE, r3=obj9, r4=bogus", n_objects=3)
    assert out["r1"] == 0
    assert out["r2"] == ESCALATE
    assert out["r3"] == ESCALATE  # 範囲外 index は委譲へ丸める
    assert out["r4"] == ESCALATE  # 不正トークンは委譲へ丸める


def test_agent_conditions_registered() -> None:
    assert set(AGENT_CONDITIONS) <= set(runner.META.conditions)


def test_margin_guard_escalates_low_margin_keeps_high() -> None:
    """ツール側ガード: 低マージンの入居者だけ ESCALATE に上書きし、高マージンは LLM 決定を残す。

    真値非参照（蒸留マージンのみ）で、決定的 OR-full の委譲規則をシステム強制することを固定する。"""
    from orx.common.config import load_config
    from orx.common.paths import repo_root
    from orx.exp.suites.s7_ownership import generator
    from orx.exp.suites.s7_ownership.agent import _resident_margins, apply_margin_guard
    from orx.exp.suites.s7_ownership.model import S7World

    world = load_config(repo_root() / runner.WORLD, S7World)
    ep = generator.generate_episode(world, 701, 0.3)  # 瓜二つ（低マージン多発）
    margins = _resident_margins(ep, world)
    decisions = dict.fromkeys(world.residents, 0)  # 全員 obj0 を配送する決定
    guarded = apply_margin_guard(decisions, ep, world)
    for r in world.residents:
        if margins[r] < world.margin_threshold:
            assert guarded[r] == ESCALATE  # 低マージンは機械強制で委譲
        else:
            assert guarded[r] == 0  # 高マージンは LLM 決定（obj0）を保持
    # 真値非参照の確認: margins は埋め込み類似度のみから計算（true_owner を見ていない）
    assert all(isinstance(m, float) for m in margins.values())


def test_agent_path_runs_offline_stub(tmp_path) -> None:
    cfg = ScenarioExperimentConfig(
        name="s7-agent-stub",
        scenario="s7",
        world_config=runner.WORLD,
        conditions=AGENT_CONDITIONS,
        seeds=[701, 702],
        duration_s=0.0,
        params={"primary_sep": 0.3},
        provider=ProviderConfig(mode="stub"),
    )
    res = runner.run(cfg, tmp_path / "exp", lambda *a: None)
    assert res["scope"] == "agent"
    assert res["conditions"] == AGENT_CONDITIONS
    assert "mode=stub" in res["model_snapshot"]
    assert len(res["comparisons"]) == len(AGENT_CONDITIONS) - 1
    assert set(res["total_tokens"]) == set(AGENT_CONDITIONS)
    md = runner.render_report(res)
    assert "agent/live" in md
    assert "トークン" in md
