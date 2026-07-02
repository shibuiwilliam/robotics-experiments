"""コスト概算・live プリフライトのテスト（R-1, IMPROVEMENT.md）。

完全オフライン（実APIを叩かない・決定的）。概算は作業単位を実ジェネレータで数え、
プリフライトは mode/APIキーの整合のみを検証する。
"""

from __future__ import annotations

import pytest

from orx.exp.cost import (
    PreflightError,
    TokenModel,
    estimate_experiment,
    preflight_live,
    render_estimate,
)
from orx.exp.runner import load_experiment

CONFIGS = "configs/experiments"


def _load(name: str):
    from orx.common.paths import repo_root

    return load_experiment(repo_root() / CONFIGS / name)


# ----------------------------------------------------------------- estimator


def test_t2_estimate_counts_questions_exactly() -> None:
    """T2: 3シード × 18問 = 54問を実ジェネレータで厳密に数える。"""
    est = estimate_experiment(_load("t2_business_live.yaml"))
    assert est.units_exact is True
    assert est.units["questions"] == 54
    # OR-reference は決定的（LLM不要）→ agent 条件から除外。
    assert est.agent_conditions == ["OR-full", "B1", "B0"]
    assert est.llm_calls > 0
    assert est.est_total_tokens > 0
    assert est.usd_low <= est.usd_mid <= est.usd_high


def test_t7_estimate_counts_queries_and_docs() -> None:
    est = estimate_experiment(_load("t7_sop_live.yaml"))
    assert est.units_exact is True
    assert est.units["queries"] > 0
    assert est.units["docs"] > 0
    assert est.agent_conditions == ["vector-rag"]
    assert est.llm_calls == 0  # T7 は埋め込みのみ
    assert est.embedding_calls > 0


def test_t5_estimate_includes_llm_condition() -> None:
    est = estimate_experiment(_load("t5_onboarding_live.yaml"))
    assert est.units["schemas"] == 20  # 5シード × 4
    assert est.agent_conditions == ["llm"]
    assert est.llm_calls == 20


def test_stub_config_estimate_zero_for_offline_only_conditions() -> None:
    """stub の T5（llm 条件なし）は LLM 呼び出し 0・費用 0。"""
    est = estimate_experiment(_load("t5_onboarding.yaml"))
    assert est.agent_conditions == []
    assert est.llm_calls == 0
    assert est.usd_high == 0.0


def test_price_overrides_change_cost_monotonically() -> None:
    base = estimate_experiment(_load("t2_business_live.yaml"))
    pricier = estimate_experiment(
        _load("t2_business_live.yaml"),
        TokenModel(price_in_per_mtok=6.0, price_out_per_mtok=24.0),
    )
    assert pricier.usd_mid > base.usd_mid
    assert pricier.est_total_tokens == base.est_total_tokens  # トークンは価格非依存


def test_render_estimate_is_human_readable() -> None:
    lines = render_estimate(estimate_experiment(_load("t2_business_live.yaml")))
    text = "\n".join(lines)
    assert "コスト概算" in text
    assert "推定費用" in text
    assert "前提" in text


# ----------------------------------------------------------------- preflight


def test_preflight_passes_for_stub_mode() -> None:
    """stub モードはキー不要 — 常に通る。"""
    preflight_live(_load("t2_business.yaml"))  # 例外なし


def test_preflight_blocks_openai_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """mode=openai でキー不在なら、実行前に PreflightError で止まる。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PreflightError) as exc:
        preflight_live(_load("t2_business_live.yaml"))
    assert "OPENAI_API_KEY" in str(exc.value)


def test_preflight_passes_openai_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    preflight_live(_load("t2_business_live.yaml"))  # 例外なし


def test_placeholder_models_detects_sentinel() -> None:
    """番兵モデル名（SET-...）は検出され、実モデル名は検出されない。"""
    from orx.common.providers import ProviderConfig, placeholder_models

    assert placeholder_models(ProviderConfig(llm_model="SET-SECOND-MODEL-SNAPSHOT")) == [
        "SET-SECOND-MODEL-SNAPSHOT"
    ]
    assert placeholder_models(
        ProviderConfig(text_embedding_model="SET-SECOND-EMBEDDING-SNAPSHOT")
    ) == ["SET-SECOND-EMBEDDING-SNAPSHOT"]
    assert placeholder_models(ProviderConfig(llm_model="gpt-5.4-mini")) == []


def test_preflight_blocks_placeholder_model_even_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """プレースホルダ番兵のまま mode=openai は、API キーがあっても課金前に遮断（ガードを直接検証）。

    モデル名を捏造せず実スナップショット名の指定を促す（CLAUDE.md・IMPROVEMENT.md §1）。
    注: 実体の `*_live_m2.yaml` は実モデルに設定済みのため、ここでは番兵を直接与えて
    ガード関数を検証する（実モデルでは遮断しない＝false positive 無し）。"""
    from orx.common.providers import ProviderConfig
    from orx.exp.cost import _assert_no_placeholder_model

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    with pytest.raises(PreflightError) as exc:
        _assert_no_placeholder_model(
            ProviderConfig(mode="openai", llm_model="SET-SECOND-MODEL-SNAPSHOT")
        )
    assert "プレースホルダ" in str(exc.value)
    assert "SET-SECOND-MODEL-SNAPSHOT" in str(exc.value)
    # 実モデルが設定されていれば遮断しない（false positive 無し）。
    _assert_no_placeholder_model(ProviderConfig(mode="openai", llm_model="gpt-5.4-2026-03-05"))


# ----------------------------------------------------------- scope/report labels


def test_scope_section_reflects_live_vs_stub() -> None:
    """live レポートは agent 射程を「実行済み」と表示し、stub は「未実行」と表示する。"""
    from orx.exp import scope

    live = scope.scope_section([scope.CEILING, scope.AGENT], scope.agent_status_for("openai"))
    assert "実行済み" in live
    assert "未実施" not in live  # live で「未実施」注記が出てはならない

    stub = scope.scope_section([scope.CEILING, scope.AGENT], scope.agent_status_for("stub"))
    assert "未実行" in stub
    assert "意味を持たない" in stub  # stub は無意味警告を出す

    assert "cache" in scope.agent_status_for("cache")
