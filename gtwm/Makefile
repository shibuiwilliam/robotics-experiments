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
	docker compose --profile core up -d --wait

down:
	docker compose --profile core down

sim-smoke:
	@echo "未実装: sim-smoke は着手順2で実装する"; exit 1

train-smoke:
	@echo "未実装: train-smoke は着手順4で実装する"; exit 1

exp:
	@echo "未実装: exp は着手順6で実装する"; exit 1

dashboard:
	@echo "未実装: dashboard は着手順7で実装する"; exit 1
