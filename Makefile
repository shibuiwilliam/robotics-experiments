## SBP — Semantic Bridge Platform
## 全ターゲットは uv 経由で Python を実行する（CLAUDE.md コーディング規約）。

SHELL := /bin/bash
COMPOSE := docker compose -f infra/compose.yaml
UV := uv run

.DEFAULT_GOAL := help

.PHONY: help
help: ## このヘルプを表示
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---- セットアップ -----------------------------------------------------------
.PHONY: bootstrap
bootstrap: ## 初回セットアップ（uv sync → .env 生成 → イメージ取得）
	uv sync --all-extras
	@[ -f .env ] || (cp .env.example .env && echo "created .env from .env.example")
	-$(COMPOSE) pull

.PHONY: sync
sync: ## 依存を lock どおりに同期
	uv sync --all-extras

# ---- Compose ----------------------------------------------------------------
.PHONY: up
up: ## Compose 起動（profile は PROFILES=ui,observability で指定）
	$(COMPOSE) $(if $(PROFILES),$(foreach p,$(subst $(comma), ,$(PROFILES)),--profile $(p)),) up -d

.PHONY: down
down: ## Compose 停止（ボリュームは残す）
	$(COMPOSE) down

.PHONY: ps
ps: ## Compose の状態
	$(COMPOSE) ps

.PHONY: logs
logs: ## Compose のログ追尾
	$(COMPOSE) logs -f --tail=100

.PHONY: nuke
nuke: ## Compose 停止 + ボリューム削除（データが消える。唯一の削除経路）
	$(COMPOSE) down -v

.PHONY: seed
seed: ## オントロジー投入・トピック作成・DB マイグレーション・バケット作成
	$(UV) python -m scripts.seed

# ---- 開発 -------------------------------------------------------------------
.PHONY: dev
dev: ## API（:8000）とワーカーをホットリロードで起動
	$(UV) python -m scripts.dev

# ---- 生成物 -----------------------------------------------------------------
.PHONY: gen
gen: ## ontology → src/sbp/core/vocab.py、EventType → schemas/*.json を再生成
	$(UV) python -m scripts.gen_vocab
	$(UV) python -m scripts.gen_schemas

.PHONY: ontology-check
ontology-check: ## TTL 構文・SHACL 自己検証・事象計算対応・semver 互換・生成物差分
	$(UV) python -m scripts.ontology_check

# ---- 品質 -------------------------------------------------------------------
.PHONY: lint
lint: ## ruff（format + lint）と Turtle 正規化検査
	$(UV) ruff format --check src tests services scripts
	$(UV) ruff check src tests services scripts

.PHONY: fmt
fmt: ## ruff format を適用
	$(UV) ruff format src tests services scripts
	$(UV) ruff check --fix src tests services scripts

.PHONY: typecheck
typecheck: ## pyright strict
	$(UV) pyright

.PHONY: deps-check
deps-check: ## 層依存の向きとオラクル境界を検査（import-linter）
	$(UV) lint-imports --config pyproject.toml

.PHONY: test
test: ## 単体テスト（ネットワーク・Compose 不要、5 分以内）
	$(UV) pytest -m "not integration and not llm_live"

.PHONY: test-int
test-int: ## 統合テスト（Compose 必須）
	$(UV) pytest -m integration

.PHONY: scan-secrets
scan-secrets: ## 追跡ファイルのシークレット走査（不変条件 12）
	$(UV) python -m scripts.scan_secrets

.PHONY: check
check: lint typecheck deps-check ontology-check scan-secrets test ## コミット前に必ず緑にする
	@echo "✅ make check: all green"
