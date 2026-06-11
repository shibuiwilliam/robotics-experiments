"""Tests for retrieval metrics."""

from mws.eval.metrics import mrr, ndcg_at_k, recall_at_k, task_success_rate


def test_recall_at_k() -> None:
    retrieved = ["a", "b", "c", "d", "e"]
    relevant = {"a", "c", "f"}
    assert recall_at_k(retrieved, relevant, k=3) == 2 / 3
    assert recall_at_k(retrieved, relevant, k=1) == 1 / 3


def test_recall_empty() -> None:
    assert recall_at_k([], set(), k=5) == 0.0
    assert recall_at_k(["a"], set(), k=5) == 0.0


def test_mrr() -> None:
    assert mrr(["a", "b", "c"], {"b"}) == 0.5
    assert mrr(["a", "b", "c"], {"a"}) == 1.0
    assert mrr(["a", "b", "c"], {"x"}) == 0.0


def test_ndcg() -> None:
    retrieved = ["a", "b", "c"]
    relevant = {"a", "c"}
    score = ndcg_at_k(retrieved, relevant, k=3)
    assert 0.0 < score <= 1.0


def test_task_success_rate() -> None:
    assert task_success_rate(8, 10) == 0.8
    assert task_success_rate(0, 0) == 0.0


def test_cost_tracker_splits_real_and_modeled_llm_calls() -> None:
    """IMPROVEMENT M8: metrics must never conflate real cloud LLM calls with
    cost-model entries. `llm_calls` stays the sum for compatibility."""
    from mws.eval.cost import CloudCostTracker

    cost = CloudCostTracker()
    cost.record_llm_call(input_tokens=10, output_tokens=5)  # modeled (default)
    cost.record_llm_call(input_tokens=10, output_tokens=5, real=True)
    cost.record_llm_call(input_tokens=10, output_tokens=5)  # modeled

    s = cost.summary()
    assert s["llm_calls_real"] == 1
    assert s["llm_calls_modeled"] == 2
    assert s["llm_calls"] == 3


def test_cost_tracker_marks_measured_tokens() -> None:
    """IMPROVEMENT M13: real calls whose tokens came from usage_metadata are
    counted separately from estimated ones."""
    from mws.eval.cost import CloudCostTracker

    cost = CloudCostTracker()
    cost.record_llm_call(input_tokens=100, output_tokens=20, real=True, measured=True)
    cost.record_llm_call(input_tokens=50, output_tokens=10, real=True)  # estimated
    cost.record_llm_call(input_tokens=10, output_tokens=5)  # modeled
    s = cost.summary()
    assert s["llm_calls_tokens_measured"] == 1
    assert s["llm_calls_real"] == 2
    assert s["llm_input_tokens"] == 160
