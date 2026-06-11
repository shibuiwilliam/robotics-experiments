"""Tests for score fusion."""

from mws.retrieval.fusion import reciprocal_rank_fusion


def test_rrf_single_list() -> None:
    ranked = {"semantic": [("a", 0.9), ("b", 0.7), ("c", 0.5)]}
    results = reciprocal_rank_fusion(ranked, top_n=2)
    assert len(results) == 2
    assert results[0].atom_id == "a"


def test_rrf_multi_list_fusion() -> None:
    ranked = {
        "semantic": [("a", 0.9), ("b", 0.7)],
        "spatial": [("b", 0.8), ("c", 0.6)],
    }
    results = reciprocal_rank_fusion(ranked, top_n=3)
    # "b" appears in both lists, should rank high
    ids = [r.atom_id for r in results]
    assert "b" in ids
    assert len(results) == 3


def test_rrf_weighted() -> None:
    ranked = {
        "semantic": [("a", 0.9)],
        "spatial": [("b", 0.9)],
    }
    # Heavily weight semantic
    results = reciprocal_rank_fusion(ranked, weights={"semantic": 10.0, "spatial": 1.0}, top_n=2)
    assert results[0].atom_id == "a"


def test_rrf_empty() -> None:
    results = reciprocal_rank_fusion({}, top_n=5)
    assert results == []
