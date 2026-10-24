## SBP — Semantic Bridge Platform
## 全ターゲットは uv 経由で Python を実行する（CLAUDE.md コーディング規約）。

SHELL := /bin/bash
# compose は既定で compose ファイルのディレクトリ（infra/）の .env しか読まないので、直下の .env を
# 明示して渡す。渡さないと .env に書いたポート退避（SBP_PORT_*）やプラットフォームが黙って無視される
# （所見 114）。環境変数は --env-file より優先されるので、CI の SBP_PLATFORM はそのまま効く。
# .env が無いときに --env-file を付けると compose が起動前に落ちるので、あるときだけ付ける
COMPOSE := docker compose $(if $(wildcard .env),--env-file .env )-f infra/compose.yaml
UV := uv run
# $(subst) の区切りにカンマを渡すための変数（関数の引数ではカンマを直接書けない）。
# 未定義だと PROFILES=ui,observability が --profile ui,observability の 1 引数になり、
# compose は未知の profile として基本 4 サービスだけを黙って起こす（所見 111）
comma := ,

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
gen: ## ontology → vocab.py、EventType → schemas/*.json、METRICS → Grafana ダッシュボードを再生成
	$(UV) python -m scripts.gen_vocab
	$(UV) python -m scripts.gen_schemas
	$(UV) python -m scripts.gen_dashboard

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

.PHONY: ui-install
ui-install: ## apps/ui の依存を入れる（Node 20+）
	cd apps/ui && npm ci

.PHONY: ui-check
ui-check: ## apps/ui の型検査とテスト（ADR-0008。依存が少ないので数秒で終わる）
	cd apps/ui && npm run typecheck && npm test

.PHONY: ui-build
ui-build: ## apps/ui を dist/ にビルド（compose の ui profile が配る）
	cd apps/ui && npm run build

# `audit` と同じ理由で `check` には入れない（ネットワークが必要）。
# **moderate 以上で落とす。** 開発サーバの脆弱性は本番バンドルには入らないが、
# 開発者が信頼できない網で `npm run dev` を動かす経路は実在する。
.PHONY: ui-audit
ui-audit: ## apps/ui の npm 依存の脆弱性照会（X-05。ネットワークが必要）
	cd apps/ui && npm audit --audit-level=moderate

.PHONY: ui-dev
ui-dev: ## apps/ui の開発サーバ（:5173、ホットリロード）
	cd apps/ui && npm run dev

.PHONY: scan-secrets
scan-secrets: ## 追跡ファイルのシークレット走査（不変条件 12）
	$(UV) python -m scripts.scan_secrets

# `make check` に入れていない: 脆弱性 DB の照会にネットワークが要るため
# （`make check` は Compose もネットワークも不要、という CLAUDE.md の方針）。
# `--strict` は使えない（sbp 自身が editable で PyPI に無いため必ず skip される）。
# 代わりに **skip されたのが sbp だけであること**を検査する——skip を数えずに
# 「脆弱性なし」と読むと、照会できなかったパッケージが緑に見える。
.PHONY: audit
audit: ## 依存の脆弱性照会（X-05。ネットワークが必要）
	@$(UV) pip-audit --skip-editable --progress-spinner off > /tmp/sbp-audit.txt 2>&1; \
	  status=$$?; \
	  cat /tmp/sbp-audit.txt; \
	  skipped=$$(awk '/^Name +Skip Reason/{found=1; next} found && NF && $$0 !~ /^-+/ {print $$1}' \
	    /tmp/sbp-audit.txt | grep -v '^sbp$$' || true); \
	  if [ -n "$$skipped" ]; then \
	    echo "❌ audit: sbp 以外が skip された（照会できていない）: $$skipped"; exit 1; \
	  fi; \
	  if [ $$status -ne 0 ]; then echo "❌ audit: 脆弱性あり"; exit $$status; fi; \
	  echo "✅ audit: 既知の脆弱性なし（skip は sbp 自身のみ）"

.PHONY: check
check: lint typecheck deps-check ontology-check scan-secrets test ## コミット前に必ず緑にする
	@echo "✅ make check: all green"

# UI を触ったときはこちら。`check` に ui-check を入れていないのは、`make check` を
# 「追加のインストール無しで必ず走る」ものに保つため（node_modules が無い環境でも緑にする）。
.PHONY: check-all
check-all: check ui-check ## check + apps/ui（UI を触った PR で使う）
	@echo "✅ make check-all: all green（Python + UI）"
