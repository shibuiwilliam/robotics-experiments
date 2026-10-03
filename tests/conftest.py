"""テスト共通の fixture。

``make test`` はネットワークも Compose も不要で通ること（CLAUDE.md テスト方針）。
ネットワークは ``pytest-socket`` の ``--disable-socket``（``pyproject.toml`` の addopts）で遮断し、
統合テストのみ ``@pytest.mark.enable_socket`` で解禁する。
"""

from __future__ import annotations

import pytest
from tests.constants import ONTOLOGY_VERSION, PROV_BUNDLE, PUMP, RUNNING, T0

from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.modality import Modality


@pytest.fixture
def sample_assertion() -> Assertion:
    """``Running(P-101)`` の observed アサーション。"""
    return Assertion(
        subject=PUMP,
        predicate=RUNNING,
        object=True,
        valid_from=T0,
        tx_from=T0,
        modality=Modality.OBSERVED,
        belief=BooleanBelief(p=0.95),
        provenance=PROV_BUNDLE,
        ontology_version=ONTOLOGY_VERSION,
    )
