"""真理グラフ生成器のテスト。"""

from orx.common import iri
from orx.common.schemas import TruthObject, TruthState
from orx.oracle.truth import FIDELITY_PREDICATES, truth_entity, truth_snapshot


def test_truth_snapshot_triples() -> None:
    state = TruthState(
        sim_time=3.0,
        objects=[
            TruthObject(object_id="b1", position=(0.1, 0.2, 0.3), barcode="BC-001", zone="shelf_a"),
            TruthObject(object_id="b8", position=(0.4, -0.8, 0.2), barcode=None, zone=None),
        ],
    )
    snap = truth_snapshot(state)
    triples = {t.as_tuple() for t in snap.triples}
    b1 = truth_entity("b1")
    assert (b1, iri.RDF_TYPE, f"<{iri.upper('Box')}>") in triples
    assert (b1, iri.st("inZone"), f"<{iri.entity('zone', 'shelf_a')}>") in triples
    assert (b1, iri.upper("hasIdentifier"), '"BC-001"') in triples
    # ゾーン無し・バーコード無しは type のみ
    b8_triples = [t for t in snap.triples if t.subject == truth_entity("b8")]
    assert len(b8_triples) == 1
    assert snap.positions[b1] == (0.1, 0.2, 0.3)


def test_fidelity_predicates_frozen_v0() -> None:
    # D2 v0 集合の意図しない変更を検出する（変更時はCQとoracleを同時更新すること）
    assert FIDELITY_PREDICATES == (
        iri.RDF_TYPE,
        iri.st("inZone"),
        iri.upper("hasIdentifier"),
    )
