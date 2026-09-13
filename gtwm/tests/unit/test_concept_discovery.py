"""概念発見（LLM不使用部分）のユニットテスト。LLM呼出を伴う部分は mock provider のみ使う
（llm.md「テストは常に mock」）。"""

from __future__ import annotations

import numpy as np
import pytest

from gtwm.grounding.concept_discovery import (
    ResidualSample,
    cluster_residuals,
    filter_unexplained_clusters,
    name_candidates,
    save_candidates_to_kg,
)
from gtwm.kg.schema import GT
from gtwm.kg.store import RdflibKGStore

pytestmark = pytest.mark.unit


def _sample(vec: list[float], type_confidence: float, episode_id: str = "ep0") -> ResidualSample:
    return ResidualSample(
        episode_id=episode_id,
        frame_idx=0,
        slot_idx=0,
        residual_vec=np.array(vec),
        type_confidence=type_confidence,
        type_idx=0,
    )


def test_cluster_residuals_groups_similar_vectors_and_drops_noise() -> None:
    # HDBSCAN は極端に点数が少ない/低次元だと密度クラスタを見出せない（実データでの
    # 疎な残差分布に近づけるため、十分な点数・次元数・分離度を使う）。
    rng = np.random.default_rng(1)
    tight_cluster = rng.normal(0.0, 0.01, size=(20, 4))
    outliers = rng.uniform(-30, 30, size=(10, 4))
    samples = [_sample(list(v), 0.9) for v in tight_cluster] + [
        _sample(list(v), 0.9) for v in outliers
    ]
    clusters = cluster_residuals(samples, min_cluster_size=5)
    assert len(clusters) >= 1
    sizes = [len(members) for members in clusters.values()]
    assert max(sizes) >= 5


def test_cluster_residuals_returns_empty_below_min_size() -> None:
    samples = [_sample([0.0, 0.0], 0.9), _sample([0.01, 0.0], 0.9)]
    assert cluster_residuals(samples, min_cluster_size=3) == {}


def test_filter_unexplained_clusters_drops_high_confidence_clusters() -> None:
    clusters = {
        0: [_sample([0.0], 0.95), _sample([0.0], 0.9)],  # 既存記号で説明できる（低残差・高確信度）
        1: [_sample([1.0], 0.2), _sample([1.0], 0.1)],  # 説明できない（高残差・低確信度）
    }
    unexplained = filter_unexplained_clusters(clusters, type_confidence_threshold=0.6)
    assert list(unexplained.keys()) == [1]


def test_filter_unexplained_clusters_keeps_high_residual_even_with_high_type_confidence() -> None:
    """型ヘッドが高確信度でも、予測残差が大きいクラスタは「概念的に未知」として残す
    （積み重ねケースの上段が依然 "case" に高確信度分類される、という実際に踏んだ罠の回帰）。"""
    clusters = {
        0: [_sample([0.0, 0.0], 0.95)] * 3,  # 低残差・高確信度 → 除外
        1: [_sample([10.0, 10.0], 0.95)] * 3,  # 高残差・高確信度 → 残す
    }
    unexplained = filter_unexplained_clusters(clusters, type_confidence_threshold=0.6)
    assert list(unexplained.keys()) == [1]


def test_name_candidates_uses_mock_and_ranks_by_size() -> None:
    clusters = {
        0: [_sample([0.0], 0.1)] * 2,
        1: [_sample([1.0], 0.1)] * 5,
    }
    records = name_candidates(clusters, llm_config_path="configs/llm_mock.yaml", top_n=5)
    assert len(records) == 2
    # メンバー数の多い順（cluster 1 が先）。
    assert records[0].cluster_id == 1
    assert records[0].n_members == 5
    assert records[0].naming.proposed_label_ja


def test_save_candidates_to_kg_writes_pending_review_status() -> None:
    clusters = {0: [_sample([1.0], 0.1)] * 3}
    records = name_candidates(clusters, llm_config_path="configs/llm_mock.yaml", top_n=1)
    store = RdflibKGStore()
    save_candidates_to_kg(store, records)
    subj = GT[records[0].candidate_id]
    assert (subj, GT.reviewStatus, None) in store.graph
    statuses = list(store.graph.objects(subj, GT.reviewStatus))
    assert str(statuses[0]) == "pending"
