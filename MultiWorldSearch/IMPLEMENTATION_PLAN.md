# IMPLEMENTATION_PLAN.md — Multi-World Search (MWS)

Living checklist. Follows PROJECT.md phase order and CLAUDE.md dependency direction.

> **歴史的文書（2026-06-11 注記）**: 本書のフェーズ進捗は初期計画時点のスナップショットであり、
> 現状とは乖離している。実装は全7シナリオ・consolidation（dedup/TTL/圧縮↔recall計測）・
> federation（FederatedStore + QoRRouter）・reactive（standing query / curiosity）・
> 検索拡張VLA（RetrievalAugmentedPolicy を S1/S4 が使用）・実 LanceDB/DuckDB バックエンド・
> H3 知覚税オラクル・H7 教師/生徒 A/B（実ローカルモデル）まで完了済み。
> **現在の正は IMPROVEMENT.md（レビュー/修正状況）と REPORT.md（検証結果）**。
> 未実装のまま残っているもの: federation/prefetch（予測プリフェッチ）、A2A エージェント間通信。

**Current status (original)**: Phase 0 + Phase 1 complete. 90 tests, all green.

---

## Phase 0: Foundations (core / worldmodel / storage / embedding)

### 0.1 Environment Bootstrap
- [x] pyproject.toml with pinned deps, uv sync works
- [x] mws/ package layout with __init__.py stubs
- [x] mws.cli entrypoint (trivial smoke command)
- [x] ruff + pyright configured, passing on empty package
- [x] pytest configured, trivial test passes
- [x] .gitignore (runs/, data/, .env, __pycache__, etc.)
- [x] configs/ skeleton with default.yaml

### 0.2 Core — Experience Atom & Envelope
- [x] `core/atom.py` — ExperienceAtom pydantic model (envelope + payload ref)
- [x] `core/types.py` — shared enums (Modality, CloudMode, EmbeddingSpace, ConsumerType)
- [x] `core/config.py` — typed settings (MWS_CLOUD_MODE, seeds, paths)
- [x] `core/clock.py` — deterministic clock with seed support
- [x] `core/logging.py` — structured JSON logger (no print)
- [x] `core/provenance.py` — provenance chain model
- [x] Tests: `tests/core/test_atom.py`, `test_config.py`, `test_clock.py`

### 0.3 World Model — 4D Scene Graph
- [x] `worldmodel/scene_graph.py`, `entity.py`, `relation.py`
- [x] Tests: `tests/worldmodel/test_scene_graph.py`

### 0.4 Storage — Polyglot Persistence
- [x] `storage/base.py` — AtomStore protocol
- [x] `storage/vector.py` — in-memory cosine similarity (LanceDB-swappable)
- [x] `storage/graph.py` — NetworkX-backed graph store
- [x] `storage/timeseries.py` — in-memory sorted timeseries
- [x] `storage/spatial.py` — scipy KD-tree spatial index
- [x] `storage/blob.py` — local filesystem blob store
- [x] `storage/registry.py` — polyglot store registry
- [x] Tests: `tests/storage/test_{vector,graph,timeseries,spatial,blob}.py`

### 0.5 Embedding — Two-Tier with Mock Default
- [x] `embedding/base.py` — Embedder protocol, EmbeddingResult with space tag
- [x] `embedding/mock.py` — deterministic mock embedder (seed + content-hash)
- [x] `embedding/cache.py` — content-hash embedding cache
- [x] `embedding/teacher.py` — Gemini Embedding 2 adapter (TODO live wiring)
- [x] `embedding/student.py` — local embedder stub (EmbeddingGemma placeholder)
- [x] `embedding/factory.py` — embedder factory with error handling (missing API key)
- [x] Tests: `tests/embedding/test_{mock,cache,factory}.py`

---

## Phase 1: Scenario 1 Vertical Slice

### 1.1 Retrieval — Multi-Index Fused Search
- [x] `retrieval/query.py` — RetrievalQuery + RetrievalResult models
- [x] `retrieval/fusion.py` — weighted Reciprocal Rank Fusion
- [x] `retrieval/projection.py` — consumer-aware projection (VLA/LLM/control/dashboard/audit)
- [x] `retrieval/engine.py` — unified engine with embedding cache, cost tracking, bandwidth metering, empty-result warning
- [x] Tests: `tests/retrieval/test_{engine,fusion,projection}.py`
- [x] **Golden retrieval tests**: `tests/retrieval/test_golden.py` — auto-derived labels, Recall@k/MRR/nDCG baselines

### 1.2 Sim — MuJoCo World & Sensors
- [x] `sim/world.py` — MuJoCo world wrapper with built-in minimal warehouse XML
- [x] `sim/sensors.py` — sensor data → atom extraction
- [x] `sim/factory.py` — world factory from YAML config
- [x] Tests: `tests/sim/test_{world,sensors}.py`

### 1.3 Business — Synthetic Data Generation
- [x] `business/schemas.py` — CMMS/WMS/SOP/MaintenanceLog pydantic models
- [x] `business/generator.py` — deterministic synthetic data + atom conversion
- [x] `business/stubs.py` — API-like stub connectors
- [x] Tests: `tests/business/test_generator.py`

### 1.4 Agents — ADK Agent with MWS Search Tool
- [x] `agents/base.py` — agent protocol, search tool interface
- [x] `agents/mock.py` — deterministic mock ops agent (7-step plan)
- [x] `agents/ops_agent.py` — factory (mock/live dispatch)
- [x] `agents/serve.py` — agent serve command (mock mode)
- [x] Tests: `tests/agents/test_mock_agent.py`

### 1.5 VLA — Retrieval-Augmented Policy
- [x] `vla/policy.py` — retrieval-augmented policy
- [x] `vla/skill_atoms.py` — skill demonstration atom creation
- [x] `vla/mock.py` — deterministic mock VLA
- [x] Tests: `tests/vla/test_policy.py`

### 1.6 Eval — Metrics, Reporting & Instrumentation
- [x] `eval/metrics.py` — Recall@k, MRR, nDCG, task success rate
- [x] `eval/labels.py` — ground-truth relevance auto-derivation (sim privilege)
- [x] `eval/latency.py` — latency decomposition (p50/p95 per component)
- [x] `eval/cost.py` — cloud-call/cost counter (wired into engine)
- [x] `eval/bandwidth.py` — virtual edge↔cloud bandwidth meter
- [x] `eval/report.py` — manifest + metrics JSON reporting
- [x] Tests: `tests/eval/test_{metrics,labels,bandwidth}.py`

### 1.7 Scenarios — Scenario 1 End-to-End
- [x] `scenarios/maintenance_handoff.py` — full orchestration with config loading
- [x] `scenarios/runner.py` — scenario dispatcher (loads YAML config)
- [x] `scenarios/sim_runner.py`, `index_builder.py` — standalone CLI commands
- [x] Tests: `tests/scenarios/test_maintenance_handoff.py` (smoke + config + determinism)

### 1.8 CLI Completion
- [x] All CLI subcommands: `version`, `sim run`, `index build`, `scenario run`, `eval run`, `agent serve`
- [x] One-command demo: `uv run python -m mws.cli scenario run --name maintenance_handoff --seed 0`

---

## Phase 2: Consolidation / Federation / Reactive (future)

### 2.1 Consolidation
- [ ] `consolidation/engine.py` — episodic→semantic distillation
- [ ] `consolidation/dedup.py` — deduplication
- [ ] `consolidation/ttl.py` — TTL pruning

### 2.2 Federation
- [ ] `federation/router.py` — QoR routing
- [ ] `federation/prefetch.py` — predictive prefetch

### 2.3 Reactive
- [ ] `reactive/standing_query.py` — pub-sub standing queries
- [ ] `reactive/curiosity.py` — curiosity loop (gap → explore)

---

## Phase 3+: Remaining Scenarios, Stress Tests, Ablations
- [ ] Scenarios 2–7 implementation
- [ ] Ablation experiments
- [ ] Paper-ready figures and analysis

---

## Quality Checklist
- [x] ruff format + ruff check: all clean
- [x] pyright: 0 errors (7 warnings from MuJoCo stubs)
- [x] pytest: 90 tests, all pass, <0.5s
- [x] No print() — structured logging only
- [x] No magic numbers — thresholds/dims in configs or settings
- [x] Cloud boundary respected — only embedding/ and agents/ touch Gemini
- [x] Dependency direction intact — no upward/circular imports
- [x] Seeds throughout — all randomness seeded and manifested
- [x] Content-hash embedding cache wired
- [x] Cost tracking wired into embedding and agent calls
- [x] Bandwidth metering for hypothetical cloud traffic
- [x] Golden retrieval tests with auto-derived labels and baselines
- [x] Error handling: factory validates API keys, engine warns on empty results
- [x] Config loading: scenario runner reads YAML configs
