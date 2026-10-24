"""統合テスト: 実体の型を実物の Oxigraph から引く（M8-30）。

照合ワーカーは深刻度の業務価値を「実体 → ObjectType」で引く。運用では ``make seed`` が投入した
オントロジーのグラフ（型の階層）と、インスタンスのグラフ（``rdf:type``）が別の名前付きグラフに
ある。組み込み pyoxigraph の単体テストと同じ問い合わせが、サーバ上の配置でも通ることを確かめる
（所見 57: パスの途中にグラフ境界があると 1 件も返らないことがあった）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from services.reconcile_worker import load_registries

from sbp.core import vocab
from sbp.knowledge import most_specific_types

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sbp.core.config import Settings
    from sbp.knowledge import HttpGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ROOT = Path(__file__).resolve().parents[2]
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


@pytest.fixture
def instances(graph_store: HttpGraphStore, test_graph_suffix: str) -> Iterator[dict[str, str]]:
    """テスト拠点にポンプ 1 台とライン 1 本を置く。テスト後にそのグラフだけを消す。"""
    pump = f"https://sbp.example/site/{test_graph_suffix}/asset/P-1"
    line = f"https://sbp.example/site/{test_graph_suffix}/line/L-1"
    graph = f"urn:sbp:g:inferred:{test_graph_suffix}:entity-types"
    graph_store.load_turtle(
        f"""
        <{pump}> <{RDF_TYPE}> <{vocab.ent.Pump}> .
        <{line}> <{RDF_TYPE}> <{vocab.ent.ProductionLine}> .
        """,
        graph=graph,
    )
    yield {"pump": pump, "line": line}
    graph_store.update(f"DROP SILENT GRAPH <{graph}>")


def test_registered_types_resolve_on_the_server(
    graph_store: HttpGraphStore,
    settings: Settings,
    test_graph_suffix: str,
    instances: dict[str, str],
) -> None:
    """``make seed`` が入れたオントロジーと ``value/*.yaml`` の組で、最も具体的な型に解決される。

    ``value/`` は ``Pump`` と上位の ``RotatingEquipment`` の両方を登録している。ポンプが
    回転機械一般の値で評価されないこと。
    """
    values, _reliability = load_registries(settings, root=ROOT)

    found = most_specific_types(graph_store, types=values.object_types, site=test_graph_suffix)

    assert found == {
        instances["pump"]: str(vocab.ent.Pump),
        instances["line"]: str(vocab.ent.ProductionLine),
    }
