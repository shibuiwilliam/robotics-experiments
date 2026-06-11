"""Scenario 7: Order to Fulfillment End-to-End.

Order accuracy, eliminate ghost inventory failures, automation.

Hypotheses: H4 (freshness), H8 (consumer-aware projection).
Mechanisms: physical observation overrides WMS ghost records, procurement
re-order triggered, customer notification, ERP close.

World: Shelved SKUs (sku_A, sku_B, sku_C on shelves), a target order,
1 damaged sku_A item. sku_C is ghost inventory (WMS=5, physical=0).

Actors:
  - OrderAgent (ADK mock): Orchestrates fulfillment.
  - PickingVLA: Picks items from shelves.
  - External stubs: order, WMS, procurement, ERP.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.logging import get_logger
from mws.core.types import ConsumerType, Modality
from mws.retrieval.query import RetrievalQuery
from mws.scenarios.base import BaseScenario
from mws.scenarios.freshness import reconcile_inventory
from mws.scenarios.ground_truth import derive_relevance
from mws.scenarios.registry import register_scenario
from mws.scenarios.s7_order_to_fulfillment.data import (
    generate_s7_order_record,
    generate_s7_product_master,
    generate_s7_wms_atoms,
)
from mws.scenarios.s7_order_to_fulfillment.world import SCENARIO7_XML
from mws.sim.world import MuJoCoWorld

logger = get_logger(__name__)


@register_scenario
class OrderToFulfillmentScenario(BaseScenario):
    name = "order_to_fulfillment"
    description = "Order to fulfillment end-to-end"

    def __init__(self) -> None:
        super().__init__()
        self._world: MuJoCoWorld | None = None
        self._order_atom: ExperienceAtom | None = None
        self._damaged_atom: ExperienceAtom | None = None
        self._ghost_observation_atom: ExperienceAtom | None = None
        self._fulfillment_log: list[dict[str, Any]] = []
        # External stub states
        self._wms_state: dict[str, int] = {}
        self._procurement_orders: list[dict[str, Any]] = []
        self._customer_notifications: list[dict[str, Any]] = []
        self._erp_status: dict[str, Any] = {}
        # Reconciliation outcomes (computed in act(), not hard-coded)
        self._sku_a_available: int = 0
        self._sku_c_reconcile: dict[str, Any] = {}
        # Tunables (populated in setup)
        self._sku_c_physical_qty: int = 0
        self._physical_obs_fresh: bool = True

    # Timestamps: WMS records are stamped older than physical observations so a
    # fresh observation can override them (H4). Overridable for falsifiability.
    _WMS_TS = 1700000000.0
    _PHYSICAL_FRESH_TS = 1700001200.0
    _PHYSICAL_STALE_TS = 1699990000.0  # older than WMS → must NOT override

    def setup(self, seed: int, config: dict[str, Any]) -> None:
        self._sku_c_physical_qty = int(config.get("physical_quantity_sku_c", 0))
        self._physical_obs_fresh = bool(config.get("physical_obs_fresh", True))

        # Build MuJoCo world first, then pass to _init_run for scene graph
        self._world = MuJoCoWorld(xml_path="__inline__", seed=seed)
        import mujoco

        self._world._model = mujoco.MjModel.from_xml_string(SCENARIO7_XML)
        self._world._data = mujoco.MjData(self._world._model)
        self._init_run(seed, config, world=self._world, world_id="fulfillment_warehouse")

        # Initialize WMS state (what WMS believes)
        self._wms_state = {"sku_A": 3, "sku_B": 2, "sku_C": 5}
        self._procurement_orders = []
        self._customer_notifications = []
        self._erp_status = {}
        self._fulfillment_log = []

        assert self.audit is not None
        self.audit.log(
            "setup",
            "system",
            "world_created",
            details={"world": "order_fulfillment_warehouse"},
        )

    def seed_memory(self) -> None:
        """Ingest WMS records, product master, and order record."""
        assert self.audit is not None

        # WMS inventory records
        wms_atoms = generate_s7_wms_atoms(seed=self.seed)
        self._ingest_atoms(wms_atoms)
        self.audit.log(
            "seed_memory",
            "system",
            "wms_data_ingested",
            details={"n_atoms": len(wms_atoms), "skus": ["sku_A", "sku_B", "sku_C"]},
        )

        # Product master
        product_atoms = generate_s7_product_master(seed=self.seed)
        self._ingest_atoms(product_atoms)
        self.audit.log(
            "seed_memory",
            "system",
            "product_master_ingested",
            details={"n_atoms": len(product_atoms)},
        )

        # Order record
        order_atom = generate_s7_order_record(seed=self.seed)
        self._ingest_atom(order_atom)
        self._order_atom = order_atom
        self.audit.log(
            "seed_memory",
            "system",
            "order_ingested",
            entity_id="ORD-501",
            details={"order_id": "ORD-501", "items": {"sku_A": 2, "sku_B": 1}},
        )

    def inject(self) -> None:
        """Inject damaged sku_A observation and ghost inventory condition."""
        assert self.audit is not None

        # Damaged sku_A: robot observes 1 of 3 is damaged
        self._damaged_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=1.2,
                y=0.0,
                z=1.8,
                timestamp=1700001100.0,
                world_id="order_fulfillment_warehouse",
            ),
            text_summary="OBSERVATION: sku_A unit 3 at shelf_A is damaged. "
            "Visual inspection shows packaging torn, product dented. "
            "Marking as non-pickable. Available undamaged: 2 of 3.",
            entity_id="sku_A",
            tags=["observation", "damage", "sku_A", "shelf_A", "quality"],
            structured_fields={
                "sku": "sku_A",
                "total_physical": 3,
                "damaged": 1,
                "location": "shelf_A",
                "source": "physical",
                "observed_at": 1700001100.0,
            },
        )
        self._damaged_atom.provenance.add("picking_robot:camera", "sensor", "created")
        self._ingest_atom(self._damaged_atom)
        self.audit.log(
            "inject",
            "picking_robot",
            "damage_detected",
            entity_id="sku_A",
            details={"damaged_units": 1, "available": 2},
        )

        # Ghost inventory: shelf_C is physically empty but WMS says 5
        # The physical observation will be generated in perceive()
        self.audit.log(
            "inject",
            "system",
            "ghost_inventory_condition",
            entity_id="sku_C",
            details={"wms_qty": 5, "physical_qty": 0},
        )

    def perceive(self) -> None:
        """Generate body position atoms. Robot observes shelf_C is empty."""
        assert self._world is not None
        assert self.audit is not None

        self._world.step(100)

        # Generate position atoms for all bodies
        pose_atoms: list[ExperienceAtom] = []
        for name, pos in self._world.get_body_positions().items():
            if not name:
                continue
            coord = SpatiotemporalCoord(
                x=float(pos[0]),
                y=float(pos[1]),
                z=float(pos[2]),
                timestamp=self._world.time,
                world_id="order_fulfillment_warehouse",
            )
            atom = ExperienceAtom(
                modality=Modality.POSE,
                coord=coord,
                text_summary=f"Body '{name}' observed at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})",
                entity_id=name,
                tags=["sim", "pose", name],
                structured_fields={"body_name": name},
            )
            atom.provenance.add(f"mujoco:order_fulfillment_warehouse:{name}", "sensor", "created")
            pose_atoms.append(atom)
        self._ingest_atoms(pose_atoms)  # batched embedding requests (M17)

        # Robot observes shelf_C physical count. Whether this contradicts WMS
        # (qty=5) and whether it is fresh enough to OVERRIDE the record is
        # decided later in act() by reconcile_inventory — NOT asserted here.
        phys_qty = self._sku_c_physical_qty
        obs_ts = self._PHYSICAL_FRESH_TS if self._physical_obs_fresh else self._PHYSICAL_STALE_TS
        self._ghost_observation_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=5.0,
                y=0.0,
                z=1.0,
                timestamp=obs_ts,
                world_id="order_fulfillment_warehouse",
            ),
            text_summary=(
                f"OBSERVATION: shelf_C physical count = {phys_qty}. "
                "Robot visual inspection of shelf_C."
            ),
            entity_id="sku_C",
            tags=["observation", "inventory", "sku_C", "shelf_C"],
            structured_fields={
                "sku": "sku_C",
                "quantity": phys_qty,
                "location": "shelf_C",
                "source": "physical",
                "observed_at": obs_ts,
            },
            freshness=1.0,
        )
        self._ghost_observation_atom.provenance.add("picking_robot:camera", "sensor", "created")
        self._ingest_atom(self._ghost_observation_atom)

        self.audit.log(
            "perceive",
            "picking_robot",
            "shelf_C_count_observed",
            entity_id="sku_C",
            details={
                "physical_qty": phys_qty,
                "observed_at": obs_ts,
                "fresh": self._physical_obs_fresh,
            },
        )
        self.audit.log(
            "perceive",
            "sim",
            "observations_generated",
            details={"n_bodies": len(self._world.get_body_positions())},
        )

    def act(self) -> None:
        """OrderAgent orchestrates fulfillment, PickingVLA picks items."""
        assert self.engine is not None
        assert self.audit is not None

        rng = np.random.default_rng(self.seed)

        # --- Step 1: OrderAgent receives order ---
        order_items = {"sku_A": 2, "sku_B": 1}
        self.audit.log(
            "act",
            "order_agent",
            "order_received",
            entity_id="ORD-501",
            details={"items": order_items},
        )

        # --- Step 2: Check sku_A availability ---
        sku_a_query = RetrievalQuery(
            text="sku_A inventory availability shelf_A damage observation",
            tags=["sku_A"],
            structured_filters={"sku": "sku_A"},
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        sku_a_results = self.engine.search(sku_a_query)
        sku_a_projected = self.engine.project(sku_a_results, sku_a_query)
        self.audit.log(
            "act",
            "order_agent",
            "query_sku_A",
            entity_id="sku_A",
            indices=["structured", "spatial", "semantic", "temporal"],
            projection="text+provenance/numeric",
            details={"n_results": len(sku_a_results)},
        )
        self._track_llm_cost(sku_a_projected)

        # Availability is COMPUTED from the retrieved atoms: WMS quantity minus
        # the damaged count the robot observed. Not a constant.
        needed_a = 2
        sku_a_wms_qty: float | None = None
        sku_a_damaged: float = 0.0
        for r in sku_a_results:
            atom = self.engine.get_atom(r.atom_id)
            if atom is None or atom.structured_fields.get("sku") != "sku_A":
                continue
            sf = atom.structured_fields
            if sf.get("source") == "wms" and "quantity" in sf:
                sku_a_wms_qty = float(sf["quantity"])
            if "damaged" in sf:
                sku_a_damaged = float(sf["damaged"])
        sku_a_available = int((sku_a_wms_qty or 0) - sku_a_damaged)
        self._sku_a_available = sku_a_available
        can_fulfill_a = sku_a_available >= needed_a
        self._fulfillment_log.append(
            {
                "step": "check_sku_A",
                "wms_qty": sku_a_wms_qty,
                "damaged": sku_a_damaged,
                "available": sku_a_available,
                "needed": needed_a,
                "can_fulfill": can_fulfill_a,
            }
        )

        # --- Step 3: Pick sku_A x2 (PickingVLA) — succeeds only if available ---
        pick_a_action = {
            "type": "pick",
            "sku": "sku_A",
            "quantity": needed_a,
            "source": "shelf_A",
            "action_vector": rng.standard_normal(6).tolist(),
            "success": can_fulfill_a,
        }
        self._fulfillment_log.append({"step": "pick_sku_A", **pick_a_action})
        self.audit.log(
            "act",
            "picking_vla",
            "pick_sku_A",
            entity_id="sku_A",
            details=pick_a_action,
        )
        if can_fulfill_a:
            self._wms_state["sku_A"] -= needed_a  # Update WMS after successful pick

        # --- Step 4: Check and pick sku_B x1 ---
        sku_b_query = RetrievalQuery(
            text="sku_B inventory availability shelf_B",
            tags=["sku_B"],
            structured_filters={"sku": "sku_B"},
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        sku_b_results = self.engine.search(sku_b_query)
        sku_b_projected = self.engine.project(sku_b_results, sku_b_query)
        self.audit.log(
            "act",
            "order_agent",
            "query_sku_B",
            entity_id="sku_B",
            indices=["structured", "spatial", "semantic", "temporal"],
            projection="text+provenance/numeric",
            details={"n_results": len(sku_b_results)},
        )
        self._track_llm_cost(sku_b_projected)

        # sku_B availability from retrieved WMS record (no damage on sku_B).
        needed_b = 1
        sku_b_wms_qty: float | None = None
        for r in sku_b_results:
            atom = self.engine.get_atom(r.atom_id)
            if atom is None or atom.structured_fields.get("sku") != "sku_B":
                continue
            sf = atom.structured_fields
            if sf.get("source") == "wms" and "quantity" in sf:
                sku_b_wms_qty = float(sf["quantity"])
        can_fulfill_b = (sku_b_wms_qty or 0) >= needed_b

        pick_b_action = {
            "type": "pick",
            "sku": "sku_B",
            "quantity": needed_b,
            "source": "shelf_B",
            "action_vector": rng.standard_normal(6).tolist(),
            "success": can_fulfill_b,
        }
        self._fulfillment_log.append({"step": "pick_sku_B", **pick_b_action})
        self.audit.log(
            "act",
            "picking_vla",
            "pick_sku_B",
            entity_id="sku_B",
            details=pick_b_action,
        )
        if can_fulfill_b:
            self._wms_state["sku_B"] -= needed_b

        # --- Step 5: Check sku_C (ghost inventory detection) ---
        sku_c_query = RetrievalQuery(
            text="sku_C inventory availability shelf_C observation ghost",
            tags=["sku_C"],
            structured_filters={"sku": "sku_C"},
            consumer=ConsumerType.AUDIT,  # audit projection for write-back trail
            top_k=10,
        )
        sku_c_results = self.engine.search(sku_c_query)
        sku_c_projected = self.engine.project(sku_c_results, sku_c_query)
        self.audit.log(
            "act",
            "order_agent",
            "query_sku_C",
            entity_id="sku_C",
            indices=["structured", "spatial", "semantic", "temporal"],
            projection="text+provenance/numeric",
            details={
                "n_results": len(sku_c_results),
                "qor": {"freshness": "strict"},
            },
        )
        self._track_llm_cost(sku_c_projected)

        # Ghost detection is COMPUTED by reconciling the retrieved WMS record
        # against the retrieved physical observation via freshness (H4). It
        # fires only if the fresher physical count is below the WMS quantity.
        sku_c_atoms = [
            atom
            for r in sku_c_results
            if (atom := self.engine.get_atom(r.atom_id)) is not None
            and atom.structured_fields.get("sku") == "sku_C"
        ]
        reconcile = reconcile_inventory(sku_c_atoms)
        ghost_detected = reconcile.ghost_detected
        self._sku_c_reconcile = {
            "wms_qty": reconcile.wms_quantity,
            "physical_qty": reconcile.physical_quantity,
            "physical_is_fresher": reconcile.physical_is_fresher,
            "quantities_disagree": reconcile.quantities_disagree,
            "ghost_detected": ghost_detected,
        }
        self._fulfillment_log.append({"step": "ghost_detection_sku_C", **self._sku_c_reconcile})
        self.audit.log(
            "act",
            "order_agent",
            "ghost_inventory_reconciled",
            entity_id="sku_C",
            details=self._sku_c_reconcile,
        )

        # --- Step 6: Ghost inventory write-back (only when detected) ---
        if ghost_detected:
            old_qty = self._wms_state.get("sku_C", 0)
            new_qty = int(reconcile.override_quantity or 0)
            self._wms_state["sku_C"] = new_qty  # Correct WMS to physical truth
            self._fulfillment_log.append(
                {
                    "step": "wms_write_back",
                    "sku": "sku_C",
                    "old_qty": old_qty,
                    "new_qty": new_qty,
                }
            )
            self.audit.log(
                "act",
                "order_agent",
                "wms_write_back",
                entity_id="sku_C",
                details={"sku": "sku_C", "old_qty": old_qty, "new_qty": new_qty},
            )

            # --- Step 7: Procurement re-order (only when ghost corrected) ---
            shortfall = int((reconcile.wms_quantity or 0) - (reconcile.physical_quantity or 0))
            procurement_order = {
                "sku": "sku_C",
                "quantity": shortfall,
                "reason": "ghost_inventory_correction",
                "order_id": "PO-701",
            }
            self._procurement_orders.append(procurement_order)
            self._fulfillment_log.append(
                {
                    "step": "procurement_reorder",
                    **procurement_order,
                }
            )
            self.audit.log(
                "act",
                "order_agent",
                "procurement_triggered",
                entity_id="sku_C",
                details=procurement_order,
            )

        # Compute what was actually shipped from pick outcomes.
        items_shipped: dict[str, int] = {}
        if can_fulfill_a:
            items_shipped["sku_A"] = needed_a
        if can_fulfill_b:
            items_shipped["sku_B"] = needed_b
        order_fully_fulfilled = can_fulfill_a and can_fulfill_b

        # --- Step 8: Customer notification (composed by the OrderAgent, M9) ---
        # The FACTS (fulfilled?, ghost?, shipped items) are pipeline-gated above;
        # the agent only turns them into prose. Live mode uses a real ADK call.
        from contextlib import nullcontext

        from mws.agents.scenario_agent import create_step_agent
        from mws.core.types import CloudMode

        is_live = self.settings.cloud_mode == CloudMode.LIVE
        self.agent_mode = "live" if is_live else "mock"
        facts = {
            "order_fully_fulfilled": order_fully_fulfilled,
            "ghost_detected": ghost_detected,
            "items_shipped": items_shipped,
        }
        order_agent = create_step_agent(
            self.settings,
            name="order_agent",
            instruction=(
                "You are an order-fulfillment agent. Compose a one-or-two-sentence "
                "customer notification strictly from the provided facts. Do not "
                "invent quantities or statuses."
            ),
            mock_step_fn=_mock_order_note,
            call_recorder=self.llm_recorder,
        )
        with self.latency.track("gemini_infer") if is_live else nullcontext():
            note_result = order_agent.execute_step(
                "compose_customer_notification", {"retrieval_results": [{"facts": facts}]}
            )
        note_usage = getattr(order_agent, "last_usage", None) if is_live else None
        self.cost.record_llm_call(
            input_tokens=(
                note_usage["input_tokens"] if note_usage else max(1, len(str(facts)) // 4)
            ),
            output_tokens=(
                note_usage["output_tokens"] if note_usage else max(1, len(str(note_result)) // 4)
            ),
            real=is_live,
            measured=note_usage is not None,
        )
        notification = {
            "order_id": "ORD-501",
            "status": "fulfilled" if order_fully_fulfilled else "partial",
            "items_shipped": items_shipped,
            "note": note_result["result"],
        }
        self._customer_notifications.append(notification)
        self._fulfillment_log.append(
            {
                "step": "customer_notification",
                **notification,
            }
        )
        self.audit.log(
            "act",
            "order_agent",
            "customer_notified",
            entity_id="ORD-501",
            details=notification,
        )

        # --- Step 9: ERP close ---
        self._erp_status = {
            "order_id": "ORD-501",
            "status": "closed",
            "fulfillment_status": "complete" if order_fully_fulfilled else "partial",
            "items_fulfilled": items_shipped,
            "ghost_inventory_corrected": ["sku_C"] if ghost_detected else [],
            "procurement_orders": [o["order_id"] for o in self._procurement_orders],
        }
        self._fulfillment_log.append(
            {
                "step": "erp_close",
                **self._erp_status,
            }
        )
        self.audit.log(
            "act",
            "order_agent",
            "erp_closed",
            entity_id="ORD-501",
            details=self._erp_status,
        )

    def evaluate(self) -> dict[str, Any]:
        """Compute metrics and save report."""
        assert self.engine is not None
        assert self.audit is not None

        # Ground-truth relevance
        relevant = derive_relevance(
            query_entity_ids=["sku_A", "sku_B", "sku_C", "ORD-501"],
            query_tags=["wms", "inventory", "order", "observation", "ghost_inventory"],
            atoms=self._atoms,
        )

        # Re-run a combined query for retrieval metrics
        combined_query = RetrievalQuery(
            text="order fulfillment sku_A sku_B sku_C inventory ghost",
            tags=["order", "inventory", "wms"],
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        combined_results = self.engine.search(combined_query)
        retrieved_ids = [r.atom_id for r in combined_results]
        retrieval_metrics = self._retrieval_metrics(retrieved_ids, relevant)

        # Task-specific metrics
        ghost_inventory_detected = any(
            entry.get("ghost_detected") is True for entry in self._fulfillment_log
        )
        wms_write_back = self._wms_state.get("sku_C") == 0
        procurement_triggered = len(self._procurement_orders) > 0
        customer_notified = len(self._customer_notifications) > 0
        erp_closed = self._erp_status.get("status") == "closed"

        # Order success: sku_A x2 and sku_B x1 fulfilled despite damage
        pick_a_ok = any(
            entry.get("step") == "pick_sku_A" and entry.get("success") is True
            for entry in self._fulfillment_log
        )
        pick_b_ok = any(
            entry.get("step") == "pick_sku_B" and entry.get("success") is True
            for entry in self._fulfillment_log
        )
        order_success = pick_a_ok and pick_b_ok

        e2e_checks = [
            ghost_inventory_detected,
            wms_write_back,
            procurement_triggered,
            customer_notified,
            erp_closed,
            order_success,
        ]
        e2e_success_rate = sum(1 for c in e2e_checks if c) / len(e2e_checks)

        metrics = {
            "retrieval": retrieval_metrics,
            "task": {
                "ghost_inventory_detected": ghost_inventory_detected,
                "wms_write_back": wms_write_back,
                "procurement_triggered": procurement_triggered,
                "customer_notified": customer_notified,
                "erp_closed": erp_closed,
                "order_success": order_success,
                "e2e_success_rate": e2e_success_rate,
            },
            "system": {
                **self.latency.summary(),
                **self.cost.summary(),
                **self.bandwidth.summary(),
                **self.engine.cache_stats,
                "total_atoms": self.engine.atom_count,
                "cloud_mode": str(self.settings.cloud_mode),
                "agent_mode": self.agent_mode,
            },
            "audit": {
                "total_entries": self.audit.entry_count,
            },
        }

        # Flush audit before saving
        self.audit.flush()
        self._save_results(metrics)

        # Print summary
        logger.info(f"Run ID: {self.run_id}")
        logger.info(f"Atoms ingested: {self.engine.atom_count}")
        logger.info(f"Ghost inventory detected: {ghost_inventory_detected}")
        logger.info(f"WMS write-back: {wms_write_back}")
        logger.info(f"Procurement triggered: {procurement_triggered}")
        logger.info(f"Customer notified: {customer_notified}")
        logger.info(f"ERP closed: {erp_closed}")
        logger.info(f"Order success: {order_success}")
        logger.info(f"E2E success rate: {e2e_success_rate:.2f}")

        return {"run_id": self.run_id, "metrics": metrics}

    def _track_llm_cost(self, projected: list[dict]) -> None:
        """Track mock LLM cost for a projected result set."""
        self.cost.record_llm_call(
            input_tokens=max(1, len(str(projected)) // 4),
            output_tokens=max(1, len(str(projected)) // 8),
        )
        self.bandwidth.record_llm_request(
            prompt_chars=len(str(projected)),
            response_chars=len(str(projected)) // 2,
        )


def _mock_order_note(step: str, retrieval_results: list[dict]) -> dict:
    """Deterministic customer-notification composer (mock agent path).

    Reconstructs the exact pre-M9 notification text from the gated facts, so
    mock-mode behavior and tests are unchanged.
    """
    facts = retrieval_results[0]["facts"] if retrieval_results else {}
    note = (
        "Order fulfilled."
        if facts.get("order_fully_fulfilled")
        else "Order partially fulfilled: sku_A short on undamaged stock."
    )
    if facts.get("ghost_detected"):
        note += " sku_C inventory corrected (ghost); re-order placed."
    return {"action": step, "result": note, "status": "complete"}
