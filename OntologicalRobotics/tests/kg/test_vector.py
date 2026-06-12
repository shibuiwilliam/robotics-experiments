"""ベクトル索引（LanceDB, IRIキー）のテスト。"""

from pathlib import Path

import pytest

from orx.common import iri
from orx.kg.vector import VectorIndex


def test_upsert_and_search(tmp_path: Path) -> None:
    index = VectorIndex(tmp_path, dim=4)
    e1, e2 = iri.entity("object", "e1"), iri.entity("object", "e2")
    index.upsert(e1, [1.0, 0.0, 0.0, 0.0])
    index.upsert(e2, [0.0, 1.0, 0.0, 0.0])
    results = index.search([0.9, 0.1, 0.0, 0.0], k=2)
    assert results[0][0] == e1
    assert index.count() == 2


def test_upsert_replaces(tmp_path: Path) -> None:
    index = VectorIndex(tmp_path, dim=2)
    e1 = iri.entity("object", "e1")
    index.upsert(e1, [1.0, 0.0])
    index.upsert(e1, [0.0, 1.0])
    assert index.count() == 1
    assert index.search([0.0, 1.0], k=1)[0][0] == e1


def test_dimension_mismatch_rejected(tmp_path: Path) -> None:
    index = VectorIndex(tmp_path, dim=2)
    with pytest.raises(ValueError, match="次元不一致"):
        index.upsert(iri.entity("object", "e1"), [1.0, 0.0, 0.0])
