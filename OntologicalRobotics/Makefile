# ORX (Ontological Robotics eXperiments) — 開発・実験コマンド
# `make help` で一覧。すべての実験はオフライン（stub）で再現可能。
# live 計測（OpenAI）はコンフィグの provider.mode 変更＋コスト承認が必要。

.PHONY: help install format lint lint-fix test test-v test-cq test-arch check \
        demo cq exp-t1 exp-t2 exp-t3 exp-t4 exp-t5 exp-t7 exp-all \
        onboard-demo profile clean \
        scenario-list scenario-demo scenario-s1 \
        scenario-run-all scenario-demo-all scenario-test scenario-validate scenario-all

# .env があれば読み込んで子プロセスへ渡す（OPENAI_API_KEY 等。.env は gitignore 済み）
-include .env
export

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Setup ──────────────────────────────────────────────────────────

install: ## Install dependencies (uv sync)
	uv sync

# ── Quality ────────────────────────────────────────────────────────

format: ## Auto-format code with ruff
	uv run ruff format src/ tests/

lint: ## Lint code with ruff
	uv run ruff check src/ tests/

lint-fix: ## Lint and auto-fix
	uv run ruff check --fix src/ tests/

test: ## Run all tests (offline, deterministic)
	uv run pytest -q

test-v: ## Run all tests with verbose output
	uv run pytest -v

test-cq: ## Run competency-question regression only (CLAUDE.md §5)
	uv run pytest tests/cq -q

test-arch: ## Run architecture invariant tests (import contracts etc.)
	uv run pytest tests/test_architecture.py -q

check: lint test ## Lint + full test suite (pre-commit gate)

# ── Run ────────────────────────────────────────────────────────────

demo: ## End-to-end offline smoke run (P0 pipeline, ~1min, no API key)
	uv run orx demo

cq: ## Competency-question pass/fail table via CLI
	uv run orx cq

profile: ## P0 perception-path performance numbers (docs/design/perf_p0.md)
	@cat docs/design/perf_p0.md

# ── Experiments (T1–T7, offline reproducible) ──────────────────────

exp-t1: ## T1 cross-robot identity (H2): OR-full vs OR-no-identity
	uv run orx exp run configs/experiments/t1_identity.yaml

exp-t2: ## T2 business-physical queries (H6,H7); LLM条件はliveで本計測
	uv run orx exp run configs/experiments/t2_business.yaml

exp-t3: ## T3/T6 capability planning + calibration (H3)
	uv run orx exp run configs/experiments/t3_capability.yaml

exp-t4: ## T4 degradation robustness sweep (H5)
	uv run orx exp run configs/experiments/t4_degradation_sweep.yaml

exp-t5: ## T5 onboarding integration cost (H1)
	uv run orx exp run configs/experiments/t5_onboarding.yaml

exp-t7: ## T7 SOP retrieval: onto-guided vs vector RAG (H4)
	uv run orx exp run configs/experiments/t7_sop.yaml

exp-all: exp-t1 exp-t2 exp-t3 exp-t4 exp-t5 exp-t7 ## Run all offline experiments

onboard-demo: ## New-robot onboarding workflow demo (T5)
	uv run orx onboard configs/robots/fuzzed_vendor_x.yaml

# ── Scenarios (T8–T14, business use-cases, offline) ────────────────
# 「実行」= experiment を回しレポート生成 / 「検証」= demo(反証アサート, 失敗時非零) ＋ pytest。

scenario-list: ## List registered business scenarios (T8–T14)
	uv run orx scenario list

scenario-demo: ## Demo+validate one scenario offline: make scenario-demo S=s1
	@test -n "$(S)" || { echo "usage: make scenario-demo S=s1"; exit 2; }
	uv run orx scenario demo $(S)

scenario-s1: ## S1 lot-recall (T8): demo(validate) + experiment + report + tests
	uv run orx scenario demo s1
	uv run orx scenario run configs/experiments/s1_lot_recall.yaml
	uv run pytest tests/scenarios/s1 -q

scenario-run-all: ## Run every scenario experiment (configs/experiments/s*_*.yaml) + report
	@found=0; \
	for f in configs/experiments/s*_*.yaml; do \
		[ -e "$$f" ] || continue; found=1; \
		echo "=== run $$f ==="; uv run orx scenario run "$$f" || exit 1; \
	done; \
	[ "$$found" = 1 ] || echo "（シナリオ実験コンフィグが未だありません）"

scenario-demo-all: ## Demo+validate every registered scenario (反証 green を全件アサート)
	@for s in $$(uv run orx scenario list | awk 'NR>1 {print $$1}'); do \
		echo "=== demo $$s ==="; uv run orx scenario demo "$$s" || exit 1; \
	done

scenario-test: ## Run all scenario test suites (oracle/falsification/replay/metamorphic/SHACL)
	uv run pytest tests/scenarios -q

scenario-validate: scenario-demo-all scenario-test ## Validate ALL scenarios (demos + test suites)

scenario-all: scenario-run-all scenario-test ## Run + validate ALL scenarios end-to-end

# ── Housekeeping ───────────────────────────────────────────────────

clean: ## Remove tool caches (data/ と reports/ は絶対に消さない)
	rm -rf .pytest_cache .ruff_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
