# Musubi — canonical task interface (CLAUDE.md §3).
# `make` targets are the source of truth; the real commands live here.

# uv runs everything inside the project venv. Offline-by-default.
PY  := uv run python
PYTEST := uv run pytest
RUFF := uv run ruff
MYPY := uv run mypy
MJPY := mjpython           # macOS interactive viewer / demo recording only

# Default VCR mode for tests: replay (no network, CI-equivalent).
export MUSUBI_VCR_MODE ?= replay

MODE ?= replay
ARM  ?= A4
S    ?=
E    ?=

.DEFAULT_GOAL := help

.PHONY: help setup gen fmt lint types test test-live check \
        scenario experiment dashboard demo docs-check report clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup:  ## Create venv, install deps + pre-commit (offline core only)
	uv venv --python 3.11
	uv pip install -e ".[dev]"
	-uv run pre-commit install
	@echo "setup complete. (live extra: 'uv pip install -e .[dev,live]')"

gen:  ## Regenerate ontology artifacts from LinkML sources (commit outputs)
	$(PY) -m ontology.generate

fmt:  ## Format with ruff
	$(RUFF) format .

lint:  ## Lint (ruff check + format diff check)
	$(RUFF) check .
	$(RUFF) format --check .

types:  ## Type check with mypy
	$(MYPY) ontology core sim perception agents external clients bench scoreboard config

test:  ## Run tests (VCR replay, offline, CI-equivalent)
	MUSUBI_VCR_MODE=replay $(PYTEST)

test-live:  ## Run live-API tests (record mode; needs key + budget — user only)
	MUSUBI_VCR_MODE=record $(PYTEST) -m live

check: lint types test  ## PR gate / definition of done (offline)

scenario:  ## Run one scenario: make scenario S=<name> [MODE=replay|record] [ARM=A4]
	MUSUBI_VCR_MODE=$(MODE) $(PY) -m bench.runner scenario --name "$(S)" --arm "$(ARM)"

experiment:  ## Run one experiment program: make experiment E=<E0..E7>
	MUSUBI_VCR_MODE=$(MODE) $(PY) -m bench.runner experiment --name "$(E)"

dashboard:  ## Generate & open the semantic-observability dashboard
	$(PY) -m scoreboard.dashboard build

report:  ## Generate an experiment report: make report E=<E>
	$(PY) -m scoreboard.reports build --experiment "$(E)"

demo:  ## Interactive viewer + GIF (macOS: mjpython): make demo S=<name>
	$(MJPY) -m sim.demo --scenario "$(S)"

docs-check:  ## Validate Mermaid blocks in Markdown
	$(PY) scripts/check_mermaid.py .

clean:  ## Remove caches and run artifacts (keeps committed cassettes/generated)
	rm -rf .pytest_cache .mypy_cache .ruff_cache runs data/*.duckdb data/*.sqlite
