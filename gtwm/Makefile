SHELL := /bin/bash
-include .env
export

PYTORCH_ENABLE_MPS_FALLBACK ?= 1
TOKENIZERS_PARALLELISM ?= false
MUJOCO_GL ?= glfw
export PYTORCH_ENABLE_MPS_FALLBACK
export TOKENIZERS_PARALLELISM
export MUJOCO_GL

.PHONY: setup doctor lint test test-sim test-int up down sim-smoke train-smoke exp dashboard

setup:
	@command -v uv >/dev/null 2>&1 || { echo "uv が見つかりません: brew install uv"; exit 1; }
	@command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg が見つかりません: brew install ffmpeg"; exit 1; }
	uv sync --all-extras
	uv run pre-commit install

doctor:
	uv run gtwm doctor

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

test:
	uv run pytest -m unit -q

test-sim:
	uv run pytest -m sim -q -p no:xdist

test-int:
	uv run pytest -m integration -q

up:
	docker compose --profile core up -d
	@echo "Oxigraph の起動待ち（HTTP ポーリング。イメージに healthcheck 用の shell/wget/curl が無いため）..."
	uv run python scripts/wait_for_oxigraph.py

down:
	docker compose --profile core down

up-p2:
	docker compose --profile p2 up -d --build
	@echo "拠点コネクタの healthcheck 待ち..."
	@timeout 60 sh -c 'until [ "$$(docker inspect -f "{{.State.Health.Status}}" gtwm-site-a-connector-1 2>/dev/null)" = "healthy" ] && [ "$$(docker inspect -f "{{.State.Health.Status}}" gtwm-site-b-connector-1 2>/dev/null)" = "healthy" ]; do sleep 1; done'
	@echo "site_a (:9101) / site_b (:9102) ともに healthy"

down-p2:
	docker compose --profile p2 down

sim-smoke:
	uv run gtwm sim gen --set smoke --episodes 1 --duration 30 --seed 0

train-smoke:
	@test -d data/sim/wm_smoke/ep_0001_seed1 || uv run gtwm sim gen --set wm_smoke --episodes 2 --duration 30 --seed 0
	uv run gtwm wm train --config configs/wm/smoke.yaml

exp:
	@test -n "$(EXP)" || { echo "使い方: make exp EXP=EXP-01 [SMOKE=1]"; exit 1; }
	@test -f experiments/$(EXP)/run.py || { echo "未実装: experiments/$(EXP)/run.py が無い"; exit 1; }
	SMOKE="$(SMOKE)" uv run python experiments/$(EXP)/run.py

dashboard:
	uv run streamlit run src/gtwm/ui/app.py
