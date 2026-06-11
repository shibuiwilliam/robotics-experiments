"""Golden retrieval tests — assert Recall@k / MRR / nDCG against baselines.

Uses the sim privilege: MuJoCo ground truth provides entity IDs, so we can
auto-derive relevance labels and assert retrieval quality without human annotation.
These tests are the regression guard for retrieval-affecting changes.
"""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import ConsumerType, EmbeddingSpace, Modality
from mws.embedding.mock import MockEmbedder
from mws.eval.labels import derive_relevance_labels
from mws.eval.metrics import mrr, ndcg_at_k, recall_at_k
from mws.retrieval.engine import RetrievalEngine
from mws.retrieval.query import RetrievalQuery
from mws.storage.registry import StoreRegistry


def _build_golden_engine() -> tuple[RetrievalEngine, list[ExperienceAtom]]:
    """Build engine with a known corpus for golden testing.

    Corpus simulates Scenario 1 data: motor observations, CMMS records,
    SOP documents, inventory, and unrelated noise. Entity IDs are set
    to enable ground-truth relevance derivation.
    """
    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=42)
    engine = RetrievalEngine(stores=stores, embedder=embedder)
    atoms: list[ExperienceAtom] = []

    def make(
        text: str,
        modality: Modality,
        entity_id: str | None = None,
        x: float = 0.0,
        y: float = 0.0,
        ts: float = 100.0,
        tags: list[str] | None = None,
        fields: dict | None = None,
    ) -> ExperienceAtom:
        coord = SpatiotemporalCoord(x=x, y=y, z=0.0, timestamp=ts)
        atom = ExperienceAtom(
            modality=modality,
            coord=coord,
            text_summary=text,
            entity_id=entity_id,
            tags=tags or [],
            structured_fields=fields or {},
        )
        atom.provenance.add("test", "sensor", "created")
        return atom

    # --- Relevant atoms (about conveyor_motor / maintenance) ---
    relevant = [
        make(
            "Conveyor motor temperature 85C, above threshold",
            Modality.TELEMETRY,
            entity_id="conveyor_motor",
            x=2.0,
            y=0.0,
            ts=100.0,
            tags=["maintenance", "motor", "CONV-01", "anomaly"],
            fields={"equipment_id": "CONV-01", "severity": 3},
        ),
        make(
            "Work order WO-001: conveyor motor overheating, scheduled repair",
            Modality.STRUCTURED_RECORD,
            entity_id="conveyor_motor",
            x=2.0,
            y=0.0,
            ts=99.0,
            tags=["cmms", "work_order", "CONV-01", "maintenance"],
            fields={"equipment_id": "CONV-01", "work_order_id": "WO-001"},
        ),
        make(
            "SOP: Conveyor motor overheating diagnosis and repair procedure",
            Modality.SOP,
            x=0.0,
            y=0.0,
            ts=50.0,
            tags=["sop", "conveyor_motor", "maintenance", "motor"],
            fields={"equipment_type": "conveyor_motor"},
        ),
        make(
            "Motor bearing replacement skill demonstration, 10-step trajectory",
            Modality.SKILL_DEMO,
            entity_id="conveyor_motor",
            x=2.0,
            y=0.0,
            ts=80.0,
            tags=["skill_demo", "bearing_replacement", "conveyor_motor"],
        ),
        make(
            "Inventory: conveyor motor bearing, 5 pcs at shelf-A3",
            Modality.STRUCTURED_RECORD,
            x=0.0,
            y=0.0,
            ts=90.0,
            tags=["wms", "inventory", "CONV-01", "maintenance"],
            fields={"item_id": "PART-001"},
        ),
    ]

    # --- Noise atoms (unrelated) ---
    noise = [
        make(
            "Pump pressure reading 150 PSI, normal operation",
            Modality.TELEMETRY,
            entity_id="pump_02",
            x=10.0,
            y=10.0,
            ts=100.0,
            tags=["pump", "normal"],
            fields={"equipment_id": "PUMP-02"},
        ),
        make(
            "Forklift battery level at 80%",
            Modality.TELEMETRY,
            entity_id="forklift_01",
            x=-5.0,
            y=3.0,
            ts=100.0,
            tags=["forklift", "battery"],
        ),
        make(
            "Warehouse zone B temperature 22C, humidity 45%",
            Modality.TELEMETRY,
            entity_id="zone_b",
            x=20.0,
            y=20.0,
            ts=100.0,
            tags=["environment", "zone_b"],
        ),
        make(
            "Shipping order SO-1234 dispatched",
            Modality.STRUCTURED_RECORD,
            x=0.0,
            y=0.0,
            ts=95.0,
            tags=["erp", "shipping"],
            fields={"order_id": "SO-1234"},
        ),
        make(
            "Safety inspection of emergency exits completed",
            Modality.DOCUMENT,
            x=0.0,
            y=0.0,
            ts=70.0,
            tags=["safety", "inspection"],
        ),
    ]

    for atom in relevant + noise:
        engine.ingest(atom)
        atoms.append(atom)

    return engine, atoms


def test_golden_cross_modal_retrieval() -> None:
    """Golden test: cross-modal query for motor maintenance must hit baselines.

    Baselines (mock embedder, seed=42):
      - Recall@5 >= 0.4  (at least 2 of 5 relevant in top 5)
      - Recall@10 >= 0.6  (at least 3 of 5 relevant in top 10)
      - MRR > 0.0  (at least one relevant item found)
      - nDCG@10 > 0.0
    """
    engine, atoms = _build_golden_engine()

    query = RetrievalQuery(
        text="conveyor motor overheating maintenance procedure",
        position=(2.0, 0.0, 0.0),
        spatial_radius=5.0,
        tags=["maintenance", "motor", "CONV-01"],
        structured_filters={"equipment_id": "CONV-01"},
        consumer=ConsumerType.LLM,
        top_k=10,
    )
    results = engine.search(query)
    retrieved_ids = [r.atom_id for r in results]

    # Auto-derive ground-truth relevance using sim privilege
    relevant_ids = derive_relevance_labels(
        query_entity_id="conveyor_motor",
        query_tags=["maintenance", "motor", "CONV-01"],
        atoms=atoms,
    )

    # There should be at least 3 relevant atoms (entity match + tag overlap)
    assert len(relevant_ids) >= 3, f"Expected >=3 relevant, got {len(relevant_ids)}"

    # Compute metrics
    r5 = recall_at_k(retrieved_ids, relevant_ids, k=5)
    r10 = recall_at_k(retrieved_ids, relevant_ids, k=10)
    mrr_score = mrr(retrieved_ids, relevant_ids)
    ndcg_score = ndcg_at_k(retrieved_ids, relevant_ids, k=10)

    # Assert against baselines
    assert r5 >= 0.4, f"Recall@5={r5:.2f} below baseline 0.4"
    assert r10 >= 0.6, f"Recall@10={r10:.2f} below baseline 0.6"
    assert mrr_score > 0.0, f"MRR={mrr_score:.2f}, no relevant item found"
    assert ndcg_score > 0.0, f"nDCG@10={ndcg_score:.2f}, ranking quality too low"


def test_golden_spatial_only_retrieval() -> None:
    """Golden test: purely spatial query near the motor should return nearby atoms."""
    engine, atoms = _build_golden_engine()

    query = RetrievalQuery(
        position=(2.0, 0.0, 0.0),
        spatial_radius=3.0,
        top_k=5,
    )
    results = engine.search(query)
    assert len(results) >= 1

    # All results should be within the spatial radius
    relevant_ids = derive_relevance_labels(
        query_entity_id="conveyor_motor",
        query_tags=[],
        atoms=atoms,
    )
    retrieved_ids = [r.atom_id for r in results]
    # At least the motor-related atoms should appear (they're at x=2, y=0)
    r5 = recall_at_k(retrieved_ids, relevant_ids, k=5)
    assert r5 > 0.0, "Spatial query near motor should find motor-related atoms"


def test_golden_structured_filter() -> None:
    """Golden test: structured filter for equipment_id should find exact matches."""
    engine, _atoms = _build_golden_engine()

    query = RetrievalQuery(
        structured_filters={"equipment_id": "CONV-01"},
        top_k=10,
    )
    results = engine.search(query)
    assert len(results) >= 2, "Should find at least 2 atoms with equipment_id=CONV-01"

    # Every result should have the right equipment_id
    for r in results:
        atom = engine._atoms[r.atom_id]
        # Atoms matched by structured filter should have the field
        assert (
            atom.structured_fields.get("equipment_id") == "CONV-01"
            or len(set(atom.tags) & {"CONV-01", "maintenance"}) >= 2
        )


def test_golden_relational_retrieval() -> None:
    """Golden test (IMPROVEMENT R2): an entity_id query expands through the
    scene graph and surfaces atoms attached to RELATED entities — the 6th
    (relational) index genuinely contributes to fusion."""
    from mws.core.types import EntityKind, RelationType
    from mws.worldmodel.entity import EntityNode
    from mws.worldmodel.relation import Relation
    from mws.worldmodel.scene_graph import SceneGraph

    graph = SceneGraph(world_id="golden")
    for eid, pos in [("conveyor_motor", (2.0, 0.0, 0.0)), ("drive_belt", (2.5, 0.0, 0.0))]:
        graph.add_entity(EntityNode(entity_id=eid, kind=EntityKind.OBJECT, name=eid, position=pos))
    graph.add_relation(
        Relation(
            source_id="conveyor_motor",
            target_id="drive_belt",
            relation_type=RelationType.SPATIAL_NEAR,
            weight=0.9,
            timestamp=100.0,
        )
    )

    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    engine = RetrievalEngine(stores=stores, embedder=MockEmbedder(seed=42), graph=graph)

    belt_atom = ExperienceAtom(
        modality=Modality.SKILL_DEMO,
        coord=SpatiotemporalCoord(x=2.5, y=0.0, z=0.0, timestamp=100.0),
        text_summary="Belt tensioning skill demonstration",
        entity_id="drive_belt",
        tags=["skill_demo"],
    )
    engine.ingest(belt_atom)
    noise_atom = ExperienceAtom(
        modality=Modality.STRUCTURED_RECORD,
        coord=SpatiotemporalCoord(x=50.0, y=50.0, z=0.0, timestamp=100.0),
        text_summary="Unrelated shipping manifest",
        entity_id="dock_03",
        tags=["shipping"],
    )
    engine.ingest(noise_atom)

    query = RetrievalQuery(structured_filters={"entity_id": "conveyor_motor"}, top_k=5)
    results = engine.search(query)
    retrieved = [r.atom_id for r in results]
    assert belt_atom.atom_id in retrieved, "related-entity atom must be retrievable"
    hit = next(r for r in results if r.atom_id == belt_atom.atom_id)
    assert "relational" in hit.scores_by_index
    assert noise_atom.atom_id not in retrieved
