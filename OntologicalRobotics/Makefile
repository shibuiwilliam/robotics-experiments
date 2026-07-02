# ORX (Ontological Robotics eXperiments) — 開発・実験コマンド
# `make help` で一覧。すべての実験はオフライン（stub）で再現可能。
# live 計測（OpenAI）はコンフィグの provider.mode 変更＋コスト承認が必要。

.PHONY: help install format lint lint-fix test test-v test-cq test-arch check \
        demo cq exp-t1 exp-t2 exp-t3 exp-t4 exp-t5 exp-t7 exp-all \
        exp-estimate-live exp-live-t2 exp-live-t5 exp-live-t7 exp-live-all \
        exp-estimate-m2 exp-live-m2-all scenario-live-m2-all live-m2-all \
        onboard-demo profile clean \
        scenario-list scenario-demo scenario-milestone-a scenario-milestone-all \
        scenario-s1 scenario-s2 scenario-s3 scenario-s4 scenario-s5 scenario-s6 scenario-s7 scenario-s8 \
        scenario-run-all scenario-run-live-all scenario-verify-live-cache scenario-demo-all scenario-test \
        scenario-validate scenario-all scenario-report-all scenario-report-all-timestamped report \
        git-provenance-check

# .env があれば読み込んで子プロセスへ渡す（OPENAI_API_KEY 等。.env は gitignore 済み）
-include .env
export

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | \
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

# --- live 計測（課金・要コスト承認）: docs/LIVE_MEASUREMENT.md を参照 ---
exp-estimate-live: ## 全live実験の概算トークン・費用を表示（実APIを叩かない・承認判断用）
	@for c in t2_business_live t5_onboarding_live t7_sop_live; do \
		uv run orx exp estimate configs/experiments/$$c.yaml; echo; \
	done

exp-live-t2: ## T2 live 本計測（H6,H7 agent検証）— OPENAI_API_KEY＋コスト承認が前提
	uv run orx exp run configs/experiments/t2_business_live.yaml

exp-live-t5: ## T5 live 本計測（H1 agent検証）— OPENAI_API_KEY＋コスト承認が前提
	uv run orx exp run configs/experiments/t5_onboarding_live.yaml

exp-live-t7: ## T7 live 本計測（H4 agent検証）— OPENAI_API_KEY＋コスト承認が前提
	uv run orx exp run configs/experiments/t7_sop_live.yaml

exp-live-all: exp-live-t2 exp-live-t5 exp-live-t7 ## 全live実験（H1/H4/H6/H7 agent検証）を本計測

# --- 第2モデル再現（R-A, PROJECT.md §8.3）: モデル依存性の切り分け。第1モデルはキャッシュ済み0円 ---
exp-estimate-m2: ## 第2モデル再現の概算（t2/t5/t7 m2）を表示（実APIを叩かない・承認判断用）
	@for c in t2_business_live_m2 t5_onboarding_live_m2 t7_sop_live_m2; do \
		uv run orx exp estimate configs/experiments/$$c.yaml; echo; \
	done

exp-live-m2-all: ## 抽象タスクの第2モデル再現（t2/t5/t7 m2）を本計測 — 要 KEY＋コスト承認
	@for c in t2_business_live_m2 t5_onboarding_live_m2 t7_sop_live_m2; do \
		echo "=== m2 $$c ==="; uv run orx exp run configs/experiments/$$c.yaml || exit 1; \
	done

scenario-live-m2-all: ## 業務シナリオの第2モデル再現（s1/s2/s6 m2）を本計測 — 要 KEY＋コスト承認
	@for c in s1_lot_recall_live_m2 s2_allergen_live_m2 s6_recycling_live_m2; do \
		echo "=== m2 $$c ==="; uv run orx scenario run configs/experiments/$$c.yaml || exit 1; \
	done

live-m2-all: exp-live-m2-all scenario-live-m2-all ## 主要 live 結果の第2モデル再現（T2/T5/T7・S1/S2/S6）

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

scenario-s2: ## S2 allergen cross-contamination (T9): demo + experiment + tests
	uv run orx scenario demo s2
	uv run orx scenario run configs/experiments/s2_allergen.yaml
	uv run pytest tests/scenarios/s2 -q

scenario-s3: ## S3 multi-vendor line (T10): demo + experiment + tests
	uv run orx scenario demo s3
	uv run orx scenario run configs/experiments/s3_multi_vendor.yaml
	uv run pytest tests/scenarios/s3 -q

scenario-s4: ## S4 plant inspection (T11): demo + experiment + tests
	uv run orx scenario demo s4
	uv run orx scenario run configs/experiments/s4_inspection.yaml
	uv run pytest tests/scenarios/s4 -q

scenario-s5: ## S5 hospital transport (T12): demo + experiment + tests
	uv run orx scenario demo s5
	uv run orx scenario run configs/experiments/s5_hospital.yaml
	uv run pytest tests/scenarios/s5 -q

scenario-s6: ## S6 recycling sortation (T13): demo + experiment + tests
	uv run orx scenario demo s6
	uv run orx scenario run configs/experiments/s6_recycling.yaml
	uv run pytest tests/scenarios/s6 -q

scenario-s7: ## S7 eldercare ownership (T14): demo + experiment + tests
	uv run orx scenario demo s7
	uv run orx scenario run configs/experiments/s7_ownership.yaml
	uv run pytest tests/scenarios/s7 -q

scenario-s8: ## S8 fulfillment / kinetic action layer (T15): demo + experiment + tests
	uv run orx scenario demo s8
	uv run orx scenario run configs/experiments/s8_fulfillment.yaml
	uv run pytest tests/scenarios/s8 -q

scenario-estimate-s8: ## S8 agent(live) のコスト概算（実APIなし・K2 承認判断用）
	uv run orx scenario estimate configs/experiments/s8_fulfillment_live.yaml

git-provenance-check: ## Warn if ORX tree is dirty (results.json は -dirty で焼かれる)。STRICT_PROVENANCE=1 で失敗 (D-1)
	@if [ -n "$$(git status --porcelain -- . 2>/dev/null)" ]; then \
		echo "⚠ WARNING: ORX ツリーに未コミット変更があります — results.json の git_commit は '-dirty' になります (IMPROVEMENT.md D-1)。"; \
		echo "  公式（受入）実行はコミット直後のクリーンツリーで行ってください。"; \
		if [ "$$STRICT_PROVENANCE" = "1" ]; then echo "STRICT_PROVENANCE=1: 中断します。"; exit 1; fi; \
	fi

scenario-run-all: git-provenance-check ## Run every DETERMINISTIC scenario experiment (除外: *_live.yaml=agent/課金) + report
	@found=0; \
	for f in configs/experiments/s*_*.yaml; do \
		[ -e "$$f" ] || continue; \
		case "$$f" in *_live.yaml|*_live_m*.yaml) continue;; esac; \
		found=1; \
		echo "=== run $$f ==="; uv run orx scenario run "$$f" || exit 1; \
	done; \
	[ "$$found" = 1 ] || echo "（シナリオ実験コンフィグが未だありません）"

scenario-run-live-all: git-provenance-check ## Run agent(live)シナリオ実験（*_live.yaml）— 要 OPENAI_API_KEY＋コスト承認
	@for f in configs/experiments/s*_live.yaml; do \
		[ -e "$$f" ] || continue; \
		echo "=== live $$f ==="; uv run orx scenario run "$$f" || exit 1; \
	done

scenario-verify-live-cache: ## 記録済み live(S1/S2/S6) を mode=cache で 0 円再現し回帰検証（実APIなし, R-1B）
	uv run pytest tests/scenarios/test_live_cache_reproduction.py -q -rs

scenario-demo-all: ## Demo+validate every registered scenario (反証 green を全件アサート)
	@for s in $$(uv run orx scenario list | awk 'NR>1 {print $$1}'); do \
		echo "=== demo $$s ==="; uv run orx scenario demo "$$s" || exit 1; \
	done

scenario-test: ## Run all scenario test suites (oracle/falsification/replay/metamorphic/SHACL)
	uv run pytest tests/scenarios -q

scenario-milestone-a: ## M-Scenario-A: Tier A (S1,S2,S6) comparative report
	uv run orx scenario milestone M-Scenario-A

scenario-milestone-all: ## All-7 scenarios comparative report (C3 complete)
	uv run orx scenario milestone M-All-Scenarios --tier all

scenario-validate: scenario-demo-all scenario-test ## Validate ALL scenarios (demos + test suites)

scenario-report-all: ## 全シナリオの最新結果を恒久サマリ reports/scenario-all-<date>.md に集約（R-3e）
	uv run orx scenario report-all

scenario-report-all-timestamped: ## 恒久サマリを時刻付きで保存し履歴を残す（同日再実行で上書きしない, R-C）
	uv run orx scenario report-all --timestamped

report: ## Regenerate REPORT.md deterministically from results.json (R-INFRA; git 非追跡の消失対策)
	uv run orx report-main

scenario-all: scenario-run-all scenario-report-all report scenario-test ## Run + validate ALL scenarios end-to-end（恒久サマリ＋REPORT.md 再生成）

# ── Housekeeping ───────────────────────────────────────────────────

clean: ## Remove tool caches (data/ と reports/ は絶対に消さない)
	rm -rf .pytest_cache .ruff_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
