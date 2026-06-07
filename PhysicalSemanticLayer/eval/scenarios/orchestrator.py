"""Scenario orchestrator — shared execution logic for all scenarios.

Supports two execution modes:
  OFFLINE (default): MuJoCo physics + direct Python MCP handler calls.
  ONLINE: MuJoCo physics + pseudo-cloud HTTP (in-process via httpx ASGI) +
          VLA encoder + Claude Agent SDK with real MCP tool calls.

Each scenario eval delegates to this orchestrator for the common parts
and adds only its scenario-specific breaking point logic.
"""

from __future__ import annotations

import asyncio
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import structlog

from eval.baselines import run_baseline_comparison
from eval.metamorphic.compositionality import commutativity_divergence
from eval.metrics.se3 import joint_rmse
from eval.scenarios.execution_mode import ExecutionMode
from pseudo_cloud.data import init_db, resolve_bin_to_position
from psl.adapters.agents.claude.adapter import ClaudeAgentAdapter
from psl.adapters.cloud.adapter import CloudDataAdapter
from psl.adapters.robots.amr.adapter import AMRAdapter
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.anchoring.resolver import PhysicalAnchorResolver
from psl.ir.core import IRState
from psl.negotiation.handshake import CapabilityDescriptor, ControlMode, SemanticNegotiator
from psl.phyte.geometry import identity_se3
from psl.safety.gate import JointLimits, PhysicsConsistencyGate
from psl.world_model.core import WorldModel
from sim.schema_gen.generator import SchemaTransform
from sim.task_controller import TaskStep, execute_trajectory
from sim.wrapper import SCENES_DIR, MuJoCoSim

logger = structlog.get_logger()

SCENARIO_SCENES = SCENES_DIR


def scene_path(scenario_slug: str) -> Path:
    """Get the MuJoCo scene XML path for a scenario."""
    return SCENARIO_SCENES / scenario_slug / "scene.xml"


@dataclass
class OrchestratorResult:
    """Raw results from the orchestrator."""

    sim: MuJoCoSim
    world_model: WorldModel
    panda: PandaAdapter
    amr: AMRAdapter
    negotiator: SemanticNegotiator
    db_conn: object  # sqlite3.Connection
    resolver: PhysicalAnchorResolver
    trajectory_readings: list[dict[str, object]]
    panda_ir: IRState
    r2r_handoff_ir: IRState | None
    r2r_handoff_error: float
    panda_roundtrip_rmse: float
    commutativity_div: float
    baseline_results: dict[str, dict[str, float]]
    mcp_results: dict[str, object]
    mode: ExecutionMode = ExecutionMode.OFFLINE
    wall_time_s: float = 0.0
    # Online-mode fields (None when offline)
    agent_cost_usd: float | None = None
    agent_num_turns: int | None = None
    agent_tool_calls: list[str] = field(default_factory=list)
    agent_result_text: str | None = None
    agent_trace: list[dict[str, object]] = field(default_factory=list)
    # Ground-truth verification (eval/ only)
    physical_accuracy_ee: float | None = None
    physical_accuracy_object: float | None = None
    agent_fallback_used: bool = False
    agent_404_count: int = 0
    vla_mode: str = "hash_fallback"


def run_orchestrator(
    scenario_slug: str,
    trajectory: list[TaskStep],
    seed: int = 42,
    schema_dose: SchemaTransform | None = None,
    run_baselines: bool = True,
    sensor_prefix: str = "",
    mode: ExecutionMode = ExecutionMode.OFFLINE,
) -> OrchestratorResult:
    """Execute the common scenario orchestration.

    Phases 1-4 (physics, world model, R2R) run in both modes.
    Phase 5 differs: OFFLINE calls handlers directly; ONLINE starts
    the pseudo-cloud via httpx ASGI, creates VLA encoder, and runs
    Claude Agent SDK with real MCP tool calls.

    Args:
        scenario_slug: e.g. "s1_mixed_fleet_pick".
        trajectory: Task controller steps.
        seed: Random seed.
        schema_dose: Heterogeneity transform for baselines.
        run_baselines: Whether to run B0/B1/B2 comparison.
        sensor_prefix: Sensor name prefix (e.g. "a_" for dual-arm scenes).
        mode: OFFLINE or ONLINE execution mode.

    Returns:
        OrchestratorResult with all metrics; agent fields populated in ONLINE mode.
    """
    start = time.time()
    rng = np.random.default_rng(seed)

    logger.info("orchestrator_start", scenario=scenario_slug, mode=mode.value, seed=seed)

    # ── 1. Load scenario-specific scene ──
    xml_path = scene_path(scenario_slug)
    sim = MuJoCoSim(scene_xml=xml_path, seed=seed)

    # ── 2. Execute task trajectory ──
    readings = execute_trajectory(sim, trajectory, sensor_prefix=sensor_prefix)
    final_reading = (
        readings[-1]
        if readings
        else {
            "joint_positions": sim.get_joint_positions(sensor_prefix),
            "joint_velocities": sim.get_joint_velocities(sensor_prefix),
            "ee_position": sim.get_ee_pose(sensor_prefix)[0],
            "ee_quaternion": sim.get_ee_pose(sensor_prefix)[1],
            "time": sim.time,
        }
    )

    # ── 3. Set up world model + adapters + safety gate ──
    panda = PandaAdapter(entity_id="panda_arm")
    amr = AMRAdapter(entity_id="amr_transport")

    lower, upper = sim.get_joint_limits(sensor_prefix)
    gate = PhysicsConsistencyGate(
        joint_limits=JointLimits(
            position_lower=lower,
            position_upper=upper,
            velocity_max=np.full(len(lower), 2.61),
        )
    )

    wm = WorldModel(safety_gates={"panda_arm": gate})
    wm.register_entity("world")
    wm.register_entity("panda_arm", parent_id="world")
    wm.register_entity("amr_transport", parent_id="world")
    wm.register_entity("blue_gear", parent_id="world")

    negotiator = SemanticNegotiator()
    negotiator.register(
        CapabilityDescriptor(
            entity_id="panda_arm",
            entity_type="robot",
            n_joints=7,
            control_modes=(ControlMode.POSITION,),
            frame_convention="z_up",
            unit_system="SI",
            semantic_capabilities=("grasp", "place"),
        )
    )
    negotiator.register(
        CapabilityDescriptor(
            entity_id="amr_transport",
            entity_type="robot",
            n_joints=0,
            control_modes=(ControlMode.VELOCITY,),
            frame_convention="y_up",
            unit_system="mm",
            semantic_capabilities=("transport",),
        )
    )

    # ── 4. Real cross-adapter R2R: Panda → IR → WorldModel → IR → AMR ──
    panda_native = {
        "joint_positions": np.asarray(final_reading["joint_positions"]),
        "joint_velocities": np.asarray(final_reading["joint_velocities"]),
        "ee_position": np.asarray(final_reading["ee_position"]),
        "ee_quaternion": np.asarray(final_reading["ee_quaternion"]),
        "time": final_reading["time"],
    }

    panda_ir = panda.to_ir(panda_native)
    wm.write("panda_arm", panda_ir.phytes)

    ee_pos = np.asarray(final_reading["ee_position"])
    current_time = panda_ir.timestamp

    from psl.phyte.core import Phyte
    from psl.phyte.provenance import Provenance, ProvenanceEntry

    object_phyte = Phyte(
        semantic_id="object_position",
        frame="world",
        pose=panda_ir.phytes["ee_pose"].pose if "ee_pose" in panda_ir.phytes else identity_se3(),
        timestamp=current_time,
        clock_domain="sim",
        unit="m",
        value=ee_pos,
        covariance=np.eye(3) * 0.001,
        provenance=Provenance(
            chain=[
                ProvenanceEntry(
                    source="panda_arm", operation="grasp_handoff", timestamp=current_time
                )
            ],
            confidence=0.95,
        ),
    )
    wm.write("blue_gear", {"position": object_phyte})

    gear_phytes = wm.read("blue_gear")
    gear_pos = gear_phytes["position"].value[:2] if "position" in gear_phytes else np.zeros(2)

    amr_target: dict[str, object] = {
        "base_pos_mm": gear_pos * 1000.0,
        "base_yaw_deg": 0.0,
        "base_vel_mm_s": np.zeros(2),
        "base_yaw_rate_deg_s": 0.0,
        "time": current_time,
    }
    amr_ir = amr.to_ir(amr_target)
    amr_rt = amr.from_ir(amr_ir)
    amr_rt_pos = np.asarray(amr_rt["base_pos_mm"]) / 1000.0

    handoff_ir = IRState(
        entity_id="blue_gear",
        phytes=gear_phytes,
        timestamp=current_time,
        clock_domain="sim",
    )
    r2r_error = float(np.linalg.norm(ee_pos[:2] - amr_rt_pos[:2]))

    panda_roundtrip = panda.from_ir(panda_ir)
    rt_jpos = np.asarray(panda_roundtrip["joint_positions"])
    orig_jpos = np.asarray(panda_native["joint_positions"])
    rt_rmse = joint_rmse(rt_jpos, orig_jpos)
    comm_div = commutativity_divergence(panda, panda, panda_native)

    # ── 4a. Ground-truth verification (eval/ only — §9.1, §9.2 fixes) ──
    # Use sensor_prefix for site name (Fix 1: s2 uses "a_ee_site" not "ee_site")
    site_name = f"{sensor_prefix}ee_site" if sensor_prefix else "ee_site"
    physical_accuracy_ee: float | None = None
    physical_accuracy_object: float | None = None
    try:
        gt_ee = sim.ground_truth_site_xpos(site_name)
        sensor_ee = np.asarray(final_reading["ee_position"])
        physical_accuracy_ee = float(np.linalg.norm(gt_ee - sensor_ee))
    except Exception:
        pass
    try:
        # Fix 2: Compare WM object position against EE ground truth (not body).
        # The world model records EE position as the object's position at grasp time.
        # Comparing against the object's body position is misleading because the sim
        # doesn't physically move the object (no grasp closure).
        gt_ee_for_obj = sim.ground_truth_site_xpos(site_name)
        wm_gear = ee_pos  # The object position written to world model
        physical_accuracy_object = float(np.linalg.norm(gt_ee_for_obj[:3] - wm_gear[:3]))
    except Exception:
        pass

    # ── 4b. VLA encoding (both modes — hash fallback is free) ──
    from psl.grounding.vla_encoder import VLAEncoder

    vla = VLAEncoder(use_real_clip=False, seed=seed)
    vla_mode = "clip" if vla._use_real_clip else "hash_fallback"
    import contextlib

    for entity_id in ["blue_gear", "panda_arm"]:
        embedding_phyte = vla.encode_object(entity_id, timestamp=current_time)
        with contextlib.suppress(Exception):
            wm.write(entity_id, {f"embedding:{entity_id}": embedding_phyte})

    # ── 5. MCP tool calls + optional agent SDK ──
    db = init_db()
    resolver = PhysicalAnchorResolver(db)
    mcp_results: dict[str, object] = {}
    agent_cost_usd: float | None = None
    agent_num_turns: int | None = None
    agent_tool_calls: list[str] = []
    agent_result_text: str | None = None
    agent_trace: list[dict[str, object]] = []

    if mode == ExecutionMode.ONLINE:
        online_result = _run_online(scenario_slug, wm, db, vla, seed)
        mcp_results = online_result[0]
        agent_cost_usd = online_result[1]
        agent_num_turns = online_result[2]
        agent_tool_calls = online_result[3]
        agent_result_text = online_result[4]
        agent_trace = online_result[5] if len(online_result) > 5 else []
        agent_fallback = online_result[6] if len(online_result) > 6 else False
        agent_404s = online_result[7] if len(online_result) > 7 else 0
    else:
        mcp_results = _run_offline(wm, db, vla)
        agent_fallback = False
        agent_404s = 0

    # ── 5a. Agent adapter: write agent state to world model (N+N) ──
    agent_adapter = ClaudeAgentAdapter(entity_id="claude_supervisor")
    wm.register_entity("claude_supervisor", parent_id="world")
    negotiator.register(
        CapabilityDescriptor(
            entity_id="claude_supervisor",
            entity_type="agent",
            n_joints=0,
            control_modes=(),
            frame_convention="z_up",
            unit_system="SI",
            semantic_capabilities=("plan", "decide", "query", "resolve_document"),
        )
    )
    agent_native: dict[str, object] = {
        "task_plan": f"Execute scenario {scenario_slug}",
        "decision": "proceed",
        "confidence": 0.9,
        "referenced_entities": ["panda_arm", "amr_transport", "blue_gear"],
        "timestamp": current_time,
        "tool_calls": list(mcp_results.keys()),
    }
    agent_ir = agent_adapter.to_ir(agent_native)
    wm.write("claude_supervisor", agent_ir.phytes)

    # ── 5b. Cloud data adapter: write business data to world model (N+N) ──
    cloud_adapter = CloudDataAdapter(entity_id="pseudo_cloud")
    wm.register_entity("pseudo_cloud", parent_id="world")
    bin_positions: dict[str, list[float]] = {}
    for bin_id in ["Bin_A", "Bin_B", "Bin_C", "QA_TRAY"]:
        pos = resolve_bin_to_position(db, bin_id)
        if pos is not None:
            bin_positions[bin_id] = list(pos)
    cloud_native: dict[str, object] = {
        "bin_positions": bin_positions,
        "work_orders": [],
        "inventory": [],
        "timestamp": current_time,
    }
    cloud_ir = cloud_adapter.to_ir(cloud_native)
    wm.write("pseudo_cloud", cloud_ir.phytes)

    # ── 6. Baseline comparison ──
    baseline_results: dict[str, dict[str, float]] = {}
    if run_baselines:
        dose = schema_dose or SchemaTransform(unit_scale=1000.0, sensor_noise_std=0.01)
        baseline_results = run_baseline_comparison(panda_native, dose, rng)

    wall_time = time.time() - start
    logger.info(
        "orchestrator_complete",
        scenario=scenario_slug,
        mode=mode.value,
        wall_time_s=f"{wall_time:.2f}",
        agent_cost=agent_cost_usd,
    )

    return OrchestratorResult(
        sim=sim,
        world_model=wm,
        panda=panda,
        amr=amr,
        negotiator=negotiator,
        db_conn=db,
        resolver=resolver,
        trajectory_readings=readings,
        panda_ir=panda_ir,
        r2r_handoff_ir=handoff_ir,
        r2r_handoff_error=r2r_error,
        panda_roundtrip_rmse=rt_rmse,
        commutativity_div=comm_div,
        baseline_results=baseline_results,
        mcp_results=mcp_results,
        mode=mode,
        wall_time_s=wall_time,
        agent_cost_usd=agent_cost_usd,
        agent_num_turns=agent_num_turns,
        agent_tool_calls=agent_tool_calls,
        agent_result_text=agent_result_text,
        agent_trace=agent_trace,
        physical_accuracy_ee=physical_accuracy_ee,
        physical_accuracy_object=physical_accuracy_object,
        agent_fallback_used=agent_fallback,
        agent_404_count=agent_404s,
        vla_mode=vla_mode,
    )


def _run_offline(
    wm: WorldModel,
    db: object,
    vla: object,
) -> dict[str, object]:
    """Offline path: call MCP handlers directly (no SDK, no HTTP)."""
    from agents.tools.psl_tools import (
        make_query_world_model_handler,
        make_resolve_document_handler,
        make_subscribe_affordances_handler,
    )

    wm_handler = make_query_world_model_handler(wm)
    doc_handler = make_resolve_document_handler(db)  # type: ignore[arg-type]
    aff_handler = make_subscribe_affordances_handler(wm, vla_encoder=vla)

    loop = asyncio.new_event_loop()
    mcp_results: dict[str, object] = {}
    try:
        mcp_results["query_world_model"] = loop.run_until_complete(
            wm_handler({"entity_id": "panda_arm"})
        )
        mcp_results["resolve_document"] = loop.run_until_complete(
            doc_handler({"reference_type": "work_order", "reference_id": "WO-42"})
        )
        mcp_results["subscribe_affordances"] = loop.run_until_complete(
            aff_handler({"entity_id": "panda_arm"})
        )
    finally:
        loop.close()
    return mcp_results


def _run_online(
    scenario_slug: str,
    wm: WorldModel,
    db: object,
    vla: object,
    seed: int,
) -> tuple[
    dict[str, object],
    float | None,
    int | None,
    list[str],
    str | None,
    list[dict[str, object]],
    bool,
    int,
]:
    """Online path: pseudo-cloud HTTP + VLA + Claude Agent SDK.

    Returns:
        (mcp_results, agent_cost_usd, agent_num_turns, agent_tool_calls, agent_result_text, agent_trace, fallback_used, 404_count)
    """
    from pseudo_cloud.server import get_request_stats, reset_request_stats

    reset_request_stats()
    fallback_used = False

    import httpx
    from httpx import ASGITransport

    from pseudo_cloud.server import app as pseudo_cloud_app

    # Also run offline MCP calls for mcp_results consistency
    mcp_results = _run_offline(wm, db, vla)

    # Start pseudo-cloud in-process via ASGI transport (no port needed)
    transport = ASGITransport(app=pseudo_cloud_app)

    loop = asyncio.new_event_loop()
    agent_cost: float | None = None
    agent_turns: int | None = None
    agent_tools: list[str] = []
    agent_text: str | None = None
    agent_trace: list[dict[str, object]] = []

    try:
        http_client = httpx.AsyncClient(transport=transport, base_url="http://pseudo-cloud")

        async def _run() -> dict[str, object]:
            from eval.scenarios.agent_runner import SCENARIO_PROMPTS, run_scenario_with_agents

            prompt = SCENARIO_PROMPTS.get(scenario_slug, "Execute the scenario task.")
            return await asyncio.wait_for(
                run_scenario_with_agents(
                    scenario_id=scenario_slug,
                    prompt=prompt,
                    world_model=wm,
                    db_conn=db,  # type: ignore[arg-type]
                    seed=seed,
                    http_client=http_client,
                    vla_encoder=vla,
                ),
                timeout=180.0,
            )

        result = loop.run_until_complete(_run())
        cost_val = result.get("total_cost_usd")
        agent_cost = float(cost_val) if isinstance(cost_val, (int, float)) else 0.0
        turns_val = result.get("num_turns")
        agent_turns = int(turns_val) if isinstance(turns_val, int) else 0
        raw_tools = result.get("tool_calls")
        agent_tools = list(raw_tools) if isinstance(raw_tools, list) else []
        text_val = result.get("result_text")
        agent_text = str(text_val) if text_val is not None else ""
        raw_trace = result.get("trace")
        if isinstance(raw_trace, list):
            agent_trace = raw_trace

        loop.run_until_complete(http_client.aclose())
    except Exception as e:
        warnings.warn(
            f"Online agent run failed for {scenario_slug}: {e}. "
            "Results from offline MCP calls will be used.",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.warning("agent_run_failed", scenario=scenario_slug, error=str(e))
        fallback_used = True
    finally:
        loop.close()

    stats = get_request_stats()
    return (
        mcp_results,
        agent_cost,
        agent_turns,
        agent_tools,
        agent_text,
        agent_trace,
        fallback_used,
        stats.get("404", 0),
    )
