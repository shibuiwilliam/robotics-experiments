"""Acceptance tests for Scenario 1: Maintenance Multi-Actor Handoff.

From SCENARIOS.md:
- Fused query returns manual + CMMS history + skill demo
- Manipulator succeeds with recalled skill
- audit.jsonl records all phases
- Deterministic under fixed seed
"""

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.scenarios.registry import get_scenario


def _run_s1(seed: int = 0, config: dict | None = None) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=seed)
    scenario = get_scenario("maintenance_handoff")
    return scenario.run(seed=seed, config=config or {}, settings=settings)


def test_s1_completes_mock_no_key() -> None:
    """Gate: scenario run completes in mock mode with no keys."""
    result = _run_s1(seed=42)
    assert "run_id" in result
    assert "metrics" in result


def test_s1_retrieval_metrics() -> None:
    """Fused query returns manual + CMMS history + skill demo (Recall, MRR)."""
    result = _run_s1(seed=42)
    rm = result["metrics"]["retrieval"]
    assert rm["recall_at_10"] >= 0.5, f"Recall@10={rm['recall_at_10']:.2f} too low"
    assert rm["mrr"] > 0.0, "MRR should be > 0 (at least one relevant hit)"


def test_s1_relational_index_contributes() -> None:
    """IMPROVEMENT R2: the relational (scene-graph) index genuinely fires in
    the ops query, and the ablation isolates its contribution."""
    result = _run_s1(seed=42)
    assert result["metrics"]["task"]["relational_hits"] > 0
    ablation = result["metrics"]["ablation"]
    assert "all_except_relational" in ablation
    assert ablation["all_indices"]["n_indices"] == 6.0
    # Enabling relational must actually CHANGE the fused ranking vs without it
    # (direction is corpus-dependent; identity would mean it contributes nothing).
    assert ablation["all_indices"] != ablation["all_except_relational"]


def test_s1_skill_transfer() -> None:
    """Manipulator retrieves past skill demo and succeeds."""
    result = _run_s1(seed=42)
    task = result["metrics"]["task"]
    assert task["skill_transfer_success"] is True
    assert task["vla_confidence"] > 0.5


def test_s1_ops_plan_complete() -> None:
    """OpsAgent completes all plan steps."""
    result = _run_s1(seed=42)
    task = result["metrics"]["task"]
    assert task["all_steps_complete"] is True
    assert task["ops_steps_completed"] == task["ops_steps_total"]


def test_s1_withholding_sop_makes_step_incomplete() -> None:
    """Falsifiability: if the SOP atom is withheld from memory, retrieval can't
    surface it, so the retrieve_sop step is incomplete and all_steps_complete
    is False. Completion is gated on the pipeline, not unconditional."""
    result = _run_s1(seed=42, config={"omit_evidence_tags": ["sop"]})
    task = result["metrics"]["task"]
    assert task["all_steps_complete"] is False
    assert task["ops_steps_completed"] < task["ops_steps_total"]


def test_s1_perception_tax_measured() -> None:
    """H3 (IMPROVEMENT M7): the oracle↔pipeline gap is computed per metric and
    is non-negative (the oracle is an upper bound by construction)."""
    result = _run_s1(seed=42)
    tax = result["metrics"]["perception_tax"]
    assert tax["oracle"]["recall_at_10"] >= result["metrics"]["retrieval"]["recall_at_10"]
    assert all(v >= 0.0 for k, v in tax["tax"].items() if "recall" in k)
    # The mock pipeline is not the oracle — some tax must exist somewhere.
    assert any(v > 0.0 for v in tax["tax"].values())


def test_s1_latency_decomposition_buckets() -> None:
    """M4/M5 (CLAUDE.md §10): per-call latency buckets exist with REAL multi-
    sample percentiles — local ANN and local embed in mock mode."""
    result = _run_s1(seed=42)
    sys_metrics = result["metrics"]["system"]
    assert sys_metrics["local_ann_count"] > 1, "ANN latency must be per-call sampled"
    assert sys_metrics["local_embed_count"] > 1, "embed latency must be per-call sampled"
    assert "local_ann_p95_ms" in sys_metrics and "local_embed_p95_ms" in sys_metrics
    # Mock mode performs no cloud inference — no gemini buckets may appear.
    assert "gemini_embed_count" not in sys_metrics
    assert "gemini_infer_count" not in sys_metrics


def test_s1_audit_completeness() -> None:
    """audit.jsonl records all phases of the handoff."""
    result = _run_s1(seed=42)
    assert result["metrics"]["audit"]["total_entries"] >= 10


def test_s1_deterministic() -> None:
    """Same seed produces identical retrieval metrics."""
    r1 = _run_s1(seed=99)
    r2 = _run_s1(seed=99)
    assert r1["metrics"]["retrieval"] == r2["metrics"]["retrieval"]


def test_s1_system_instrumentation() -> None:
    """Cost, bandwidth, and cache tracking are wired."""
    result = _run_s1(seed=42)
    sys = result["metrics"]["system"]
    assert sys["embedding_calls"] > 0
    assert sys["total_bandwidth_bytes"] > 0
