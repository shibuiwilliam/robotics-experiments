.DEFAULT_GOAL := help
SHELL := /bin/bash
PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
SOURCES := src/ eval/ sim/ agents/ pseudo_cloud/ tests/

# ──────────────────────────────────────────────
# Setup & Environment
# ──────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Create venv and install all deps (dev + agent)
	uv venv --python 3.12
	uv sync --all-extras

.PHONY: doctor
doctor: ## Environment diagnostics
	@echo "=== PSL-Bench Environment Doctor ==="
	@echo ""
	@echo -n "Python:          "; $(PYTHON) --version 2>/dev/null || echo "MISSING — run 'make setup'"
	@echo -n "MuJoCo:          "; $(PYTHON) -c "import mujoco; print(mujoco.__version__)" 2>/dev/null || echo "MISSING"
	@echo -n "Pydantic:        "; $(PYTHON) -c "import pydantic; print(pydantic.__version__)" 2>/dev/null || echo "MISSING"
	@echo -n "Pint:            "; $(PYTHON) -c "import pint; print(pint.__version__)" 2>/dev/null || echo "MISSING"
	@echo -n "pytransform3d:   "; $(PYTHON) -c "import pytransform3d; print(pytransform3d.__version__)" 2>/dev/null || echo "MISSING"
	@echo -n "NumPy:           "; $(PYTHON) -c "import numpy; print(numpy.__version__)" 2>/dev/null || echo "MISSING"
	@echo -n "Hypothesis:      "; $(PYTHON) -c "import hypothesis; print(hypothesis.__version__)" 2>/dev/null || echo "MISSING"
	@echo -n "structlog:       "; $(PYTHON) -c "import structlog; print(structlog.__version__)" 2>/dev/null || echo "MISSING"
	@echo -n "Ruff:            "; .venv/bin/ruff --version 2>/dev/null || echo "MISSING"
	@echo -n "Mypy:            "; .venv/bin/mypy --version 2>/dev/null || echo "MISSING"
	@echo -n "Claude Agent SDK:"; $(PYTHON) -c "import claude_agent_sdk; print(' OK')" 2>/dev/null || echo " not installed (optional)"
	@echo -n "ANTHROPIC_API_KEY:"; if [ -f .env ] && grep -q 'ANTHROPIC_API_KEY=.' .env 2>/dev/null; then echo " set"; else echo " NOT SET (needed for agent tests)"; fi
	@echo -n "Pseudo-cloud:    "; curl -sf http://127.0.0.1:8042/health >/dev/null 2>&1 && echo "running on :8042" || echo "not running (start with 'make pseudo-cloud-start')"
	@echo -n "Scene XMLs:      "; echo "$$(ls sim/scenes/s*/scene.xml 2>/dev/null | wc -l | tr -d ' ')/7 found"
	@echo ""
	@echo "=== Done ==="

# ──────────────────────────────────────────────
# Code Quality
# ──────────────────────────────────────────────

.PHONY: fmt
fmt: ## Format code (ruff)
	.venv/bin/ruff format $(SOURCES)

.PHONY: lint
lint: ## Lint (ruff — includes architecture invariant rules)
	.venv/bin/ruff check --fix $(SOURCES)

.PHONY: type
type: ## Type check (mypy strict)
	$(PYTHON) -m mypy src/ eval/ sim/

.PHONY: check
check: fmt lint type test-fast ## Pre-commit gate: fmt + lint + type + unit tests

# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────

.PHONY: test
test: ## Run all tests excluding API (157 tests)
	$(PYTEST) -m "not api"

.PHONY: test-fast
test-fast: ## Run unit tests only (84 tests)
	$(PYTEST) -m "unit"

.PHONY: test-oracle
test-oracle: ## Run oracle (ground-truth) tests (50 tests)
	$(PYTEST) -m "oracle"

.PHONY: test-mr
test-mr: ## Run metamorphic / property tests (23 tests)
	$(PYTEST) -m "metamorphic"

.PHONY: test-scenarios
test-scenarios: ## Run all scenario tests (oracle + metamorphic)
	$(PYTEST) tests/scenarios/ -v

.PHONY: test-api
test-api: ## [$$] Run API tests (calls Anthropic — costs money)
	$(PYTEST) -m "api" -v

.PHONY: test-slow
test-slow: ## Run slow tests (CLIP model loading etc.)
	$(PYTEST) -m "slow" -v

# ──────────────────────────────────────────────
# Simulation
# ──────────────────────────────────────────────

.PHONY: vla-smoke
vla-smoke: ## Test SmolVLA action prediction (requires lerobot)
	$(PYTHON) -c "from psl.grounding.smolvla_encoder import SmolVLAEncoder; import numpy as np; e=SmolVLAEncoder(); a=e.predict_action(np.zeros((224,224,3),dtype=np.uint8),'pick up gear',{'joint_positions':np.zeros(7)}); print(f'model={e.model_name} action_dim={len(a.ee_delta)} confidence={a.confidence:.2f} gripper={a.gripper:.2f}')"

.PHONY: sim-smoke
sim-smoke: ## MuJoCo smoke test — load Panda, step 100, print qpos
	$(PYTHON) -m sim.smoke

.PHONY: sim-scenes
sim-scenes: ## Validate all 7 scenario MuJoCo scenes load correctly
	@for s in s1_mixed_fleet_pick s2_line_changeover s3_lab_custody s4_field_inspection s5_pharma_logistics s6_ewaste_disassembly s7_degraded_ops; do \
		$(PYTHON) -c "import mujoco; m=mujoco.MjModel.from_xml_path('sim/scenes/$$s/scene.xml'); print(f'  $$s: OK (nq={m.nq})')" 2>&1 || echo "  $$s: FAILED"; \
	done

# ──────────────────────────────────────────────
# Scenarios
# ──────────────────────────────────────────────

.PHONY: scenario-s1
scenario-s1: ## Run s1_mixed_fleet_pick (real physics + R2R + MCP offline)
	$(PYTHON) -c "from eval.scenarios.s1_mixed_fleet_pick.eval import evaluate_s1; r=evaluate_s1(); print(f's1: business={r.business_success} psl={r.psl_success} pass={r.overall_pass}')"

.PHONY: scenario-s2
scenario-s2: ## Run s2_line_changeover (commutativity + safety gate)
	$(PYTHON) -c "from eval.scenarios.s2_line_changeover.eval import evaluate_s2; r=evaluate_s2(); print(f's2: business={r.business_success} psl={r.psl_success} pass={r.overall_pass}')"

.PHONY: scenario-s3
scenario-s3: ## Run s3_lab_custody (provenance chain)
	$(PYTHON) -c "from eval.scenarios.s3_lab_custody.eval import evaluate_s3; r=evaluate_s3(); print(f's3: business={r.business_success} psl={r.psl_success} pass={r.overall_pass}')"

.PHONY: scenario-s4
scenario-s4: ## Run s4_field_inspection (LOD fusion + anchoring)
	$(PYTHON) -c "from eval.scenarios.s4_field_inspection.eval import evaluate_s4; r=evaluate_s4(); print(f's4: business={r.business_success} psl={r.psl_success} pass={r.overall_pass}')"

.PHONY: scenario-s5
scenario-s5: ## Run s5_pharma_logistics (neuro-symbolic + poisoning)
	$(PYTHON) -c "from eval.scenarios.s5_pharma_logistics.eval import evaluate_s5; r=evaluate_s5(); print(f's5: business={r.business_success} psl={r.psl_success} pass={r.overall_pass}')"

.PHONY: scenario-s6
scenario-s6: ## Run s6_ewaste_disassembly (open-world grounding)
	$(PYTHON) -c "from eval.scenarios.s6_ewaste_disassembly.eval import evaluate_s6; r=evaluate_s6(); print(f's6: business={r.business_success} psl={r.psl_success} pass={r.overall_pass}')"

.PHONY: scenario-s7
scenario-s7: ## Run s7_degraded_ops (clock skew + degradation)
	$(PYTHON) -c "from eval.scenarios.s7_degraded_ops.eval import evaluate_s7; r=evaluate_s7(); print(f's7: business={r.business_success} psl={r.psl_success} pass={r.overall_pass}')"

.PHONY: scenarios-all
scenarios-all: ## [$$] Full end-to-end: MuJoCo + Claude agents + HTTP + VLA (auto-detects API key)
	@PSL_MODE=online $(MAKE) --no-print-directory _run-scenarios

.PHONY: scenarios-offline
scenarios-offline: ## Fast offline: MuJoCo + direct MCP (no API calls, free)
	@PSL_MODE=offline $(MAKE) --no-print-directory _run-scenarios

.PHONY: _run-scenarios
_run-scenarios: scenario-s1 scenario-s2 scenario-s3 scenario-s4 scenario-s5 scenario-s6 scenario-s7

.PHONY: scenarios-multi-seed
scenarios-multi-seed: ## Run all scenarios with 5 seeds (mean ± std, offline)
	PSL_SEEDS=42,123,456,789,1024 PSL_MODE=offline $(PYTHON) -m eval.scenarios

.PHONY: multi-seed-report
multi-seed-report: ## Run 5-seed statistical evaluation (offline, free)
	$(PYTHON) eval/scenarios/run_multi_seed_report.py

.PHONY: sweep-scenario
sweep-scenario: ## Dose-response for one scenario (S=s1_mixed_fleet_pick)
	PSL_SCENARIO=$(S) $(PYTHON) -m eval.scenarios.dose_response

# ──────────────────────────────────────────────
# Phase 4 Stress Tests
# ──────────────────────────────────────────────

.PHONY: stress-fuzz
stress-fuzz: ## Run schema fuzzing stress test (50 random transforms)
	$(PYTHON) -c "from eval.stress.schema_fuzzing import fuzz_schema, find_breaking_point; from sim.wrapper import MuJoCoSim; from psl.adapters.robots.panda.adapter import PandaAdapter; sim=MuJoCoSim(); sim.step(100); r=fuzz_schema(50, 42, sim, PandaAdapter()); bp=find_breaking_point(r); print(f'{len(r)} fuzz runs, {sum(1 for x in r if x.gate_accepted)} accepted, max_rmse={bp[\"max_rmse\"]:.6f}')"

.PHONY: stress-skew
stress-skew: ## Run clock skew stress test
	$(PYTHON) -c "from eval.stress.clock_skew import sweep_clock_skew; r=sweep_clock_skew(clock_uncertainty=0.005); [print(f'skew={x.skew_seconds:.3f}s unc={x.clock_uncertainty:.3f}s rejections={x.gate_rejections}/{x.n_transitions} rate={x.rejection_rate:.0%}') for x in r]"

.PHONY: stress-grounding
stress-grounding: ## Run grounding generalization test (20 holdout objects)
	$(PYTHON) -c "from eval.stress.grounding_generalization import run_generalization, compute_generalization_metrics, HOLDOUT_OBJECTS, KNOWN_OBJECTS; r=run_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, 42); m=compute_generalization_metrics(r); [print(f'{k}: {v}') for k,v in m.items()]"

.PHONY: stress-all
stress-all: ## Run full Phase 4 stress test suite
	$(PYTHON) -c "from eval.stress.report import main; main()"

# ──────────────────────────────────────────────
# Claude Agent SDK (costs money — API calls)
# ──────────────────────────────────────────────

.PHONY: agent-smoke
agent-smoke: ## [$$] SDK 1-shot query (costs money!)
	$(PYTHON) -m agents.smoke

.PHONY: baseline-llm-smoke
baseline-llm-smoke: ## [$$] B2-Live single real translation call (~$$0.001)
	$(PYTHON) -m eval.b2_live smoke

.PHONY: baseline-llm
baseline-llm: ## [$$] B2-Live real Claude translation baseline (dose + non-det, ~$$0.01)
	$(PYTHON) -m eval.b2_live

# ──────────────────────────────────────────────
# Pseudo-Cloud HTTP Service
# ──────────────────────────────────────────────

.PHONY: pseudo-cloud-start
pseudo-cloud-start: ## Start pseudo-cloud HTTP server on port 8042
	$(PYTHON) -m pseudo_cloud &

.PHONY: pseudo-cloud-stop
pseudo-cloud-stop: ## Stop pseudo-cloud server
	@pkill -f "pseudo_cloud" 2>/dev/null || echo "Not running"

.PHONY: pseudo-cloud-health
pseudo-cloud-health: ## Check pseudo-cloud server health
	@curl -sf http://127.0.0.1:8042/health 2>/dev/null | $(PYTHON) -m json.tool || echo "Pseudo-cloud not running"

# ──────────────────────────────────────────────
# Evaluation & Sweeps
# ──────────────────────────────────────────────

.PHONY: eval
eval: ## Run evaluation (requires CFG=experiments/<name>.yaml)
	$(PYTHON) -m eval.runner --config $(CFG)

.PHONY: sweep
sweep: ## Run dose-response heterogeneity sweep
	$(PYTHON) -m eval.runner --sweep

.PHONY: sweep-cfg
sweep-cfg: ## Run sweep with config (requires CFG=experiments/<name>.yaml)
	$(PYTHON) -m eval.runner --sweep --config $(CFG)
