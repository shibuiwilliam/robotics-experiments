"""Mock agent — deterministic, no cloud calls."""

from __future__ import annotations

from typing import Any

from mws.core.logging import get_logger

logger = get_logger(__name__)


class MockOpsAgent:
    """Deterministic mock operations agent for Scenario 1.

    In mock mode, generates structured maintenance instructions based on
    retrieved context, without calling any LLM.
    """

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    def plan(self, context: dict[str, Any]) -> list[str]:
        """Generate a deterministic maintenance plan from retrieved context."""
        steps = [
            "assess_situation",
            "retrieve_sop",
            "check_inventory",
            "dispatch_robot",
            "execute_repair",
            "verify_fix",
            "close_work_order",
        ]
        logger.info("Mock agent generated plan", steps=len(steps))
        return steps

    def execute_step(self, step: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute a plan step deterministically."""
        retrieval_results = context.get("retrieval_results", [])
        n_results = len(retrieval_results)

        responses = {
            "assess_situation": {
                "action": "assess",
                "result": f"Anomaly confirmed. {n_results} relevant records found.",
                "status": "complete",
            },
            "retrieve_sop": {
                "action": "retrieve_sop",
                "result": "SOP-CONV-MOTOR-01 retrieved: Conveyor Motor Overheating procedure.",
                "status": "complete",
            },
            "check_inventory": {
                "action": "check_inventory",
                "result": "Parts available: bearing (PART-001), thermal paste (PART-002).",
                "status": "complete",
            },
            "dispatch_robot": {
                "action": "dispatch",
                "result": "maintenance_robot dispatched to CONV-01 location.",
                "status": "complete",
            },
            "execute_repair": {
                "action": "repair",
                "result": "VLA executing bearing replacement using retrieved skill demonstration.",
                "status": "complete",
            },
            "verify_fix": {
                "action": "verify",
                "result": "Motor temperature dropped to 45C. Vibration within normal range.",
                "status": "complete",
            },
            "close_work_order": {
                "action": "close",
                "result": "WO-001 closed. Maintenance log updated. Audit trail recorded.",
                "status": "complete",
            },
        }

        result = responses.get(step, {"action": step, "result": "unknown step", "status": "error"})
        logger.info("Mock agent executed step", step=step, status=result["status"])
        return result
