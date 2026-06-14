.PHONY: help install dev setup format lint typecheck test test-live test-scenarios \
       check scenario-list scenario-run scenario-all eval clean es-up es-down test-es

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ──────────────────────────────────────────────────────────

install: ## Install runtime dependencies
	uv sync

dev: ## Install runtime + dev dependencies
	uv sync --extra dev

setup: ## Sync the full env (runtime + dev + live) for tests and live runs
	uv sync --extra dev --extra live

# ── Quality ────────────────────────────────────────────────────────

format: ## Auto-format code with ruff
	uv run ruff format mws/ tests/

lint: ## Lint code with ruff
	uv run ruff check mws/ tests/

lint-fix: ## Lint and auto-fix
	uv run ruff check --fix mws/ tests/

typecheck: ## Run pyright type checker
	uv run pyright mws/

test: ## Run all tests (mock, deterministic)
	uv run pytest

test-v: ## Run all tests with verbose output
	uv run pytest -v

test-live: ## Run live cloud tests (requires GOOGLE_API_KEY)
	uv run pytest -m live

test-scenarios: ## Run only scenario acceptance tests
	uv run pytest tests/scenarios/ -v

# ── Elasticsearch (optional vector backend) ────────────────────────

es-up: ## Start local Elasticsearch (docker-compose) and wait for health
	docker compose up -d --wait elasticsearch

es-down: ## Stop and remove local Elasticsearch
	docker compose down

test-es: ## Run Elasticsearch integration tests (needs `make es-up` + --extra es)
	uv run pytest -m elasticsearch -v

check: format lint typecheck test ## Run all quality checks (format + lint + typecheck + test)

# ── Scenarios ──────────────────────────────────────────────────────

scenario-list: ## List all available scenarios
	uv run python -m mws.cli scenario list

scenario-run: ## Run a scenario (usage: make scenario-run NAME=maintenance_handoff SEED=0)
	uv run python -m mws.cli scenario run --name $(NAME) --seed $(or $(SEED),0)

scenario-all: setup ## Build env, run scenario acceptance tests, then run all 7 scenarios (seed 0)
	@echo "\n=== [1/2] scenario acceptance tests (mock, deterministic) ==="
	uv run --no-sync pytest tests/scenarios/ -q
	@echo "\n=== [2/2] end-to-end scenario runs ==="
	@for s in $$(uv run --no-sync python -m mws.cli scenario list); do \
		echo "\n--- $$s ---" ; \
		uv run --no-sync python -m mws.cli scenario run --name "$$s" --seed 0 ; \
	done

# ── Sim / Index / Eval ─────────────────────────────────────────────

sim: ## Run MuJoCo simulation (usage: make sim STEPS=100 SEED=0)
	uv run python -m mws.cli sim run --seed $(or $(SEED),0) --steps $(or $(STEPS),100)

eval: ## View a run report (usage: make eval RUN_ID=maintenance_handoff-0-abc123)
	uv run python -m mws.cli eval run --run-id $(RUN_ID)

scenario-multi-seed: ## Run S1 with 5 seeds (mean +/- CI for recall/mrr)
	uv run python -c "from mws.eval.multi_seed import run_multi_seed; import json; \
		r = run_multi_seed('maintenance_handoff', seeds=[0,1,2,3,4]); \
		print(json.dumps({k:v for k,v in r['summary'].items() if 'recall' in k or 'mrr' in k}, indent=2))"

scenario-multi-seed-all: setup ## Build env, run scenario tests, then ALL scenarios across seeds 0-3 with 95% CIs (mock)
	@echo "\n=== [1/2] scenario acceptance tests (mock, deterministic) ==="
	uv run --no-sync pytest tests/scenarios/ -q
	@echo "\n=== [2/2] multi-seed run (seeds 0-3, 95% CIs) ==="
	uv run --no-sync python -m mws.cli eval multi-seed --seeds 0,1,2,3

scenario-multi-seed-live: ## LIVE multi-seed (REAL cloud spend; needs MWS_CONFIRM_LIVE_SPEND=1)
	@echo "Estimated cost: ~\$$0.008/seed x 7 scenarios x 3 seeds = ~\$$0.025 (real Gemini calls)"
	@if [ "$$MWS_CONFIRM_LIVE_SPEND" != "1" ]; then \
		echo "Refusing to spend. Re-run with MWS_CONFIRM_LIVE_SPEND=1 to proceed."; exit 1; \
	fi
	MWS_CLOUD_MODE=live uv run python -m mws.cli eval multi-seed --seeds 0,1,2

# ── Demo ───────────────────────────────────────────────────────────

demo: ## Run the flagship Scenario 1 demo
	uv run python -m mws.cli scenario run --name maintenance_handoff --seed 0

# ── Cleanup ────────────────────────────────────────────────────────

clean: ## Remove runs/, data/, caches, and __pycache__
	rm -rf runs/ data/ .ruff_cache/ .pytest_cache/ .mypy_cache/ .pyright/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

clean-runs: ## Remove only runs/ (experiment artifacts)
	rm -rf runs/
