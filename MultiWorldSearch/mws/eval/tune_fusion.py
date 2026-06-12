"""Pre-registered RRF fusion-weight tuning (IMPROVEMENT backlog: Recall@5).

PRE-REGISTERED before any sweep was run (PROJECT.md §10.2 — declared here,
in code, ahead of execution; do not tune after seeing results):

- Evaluation set: the golden (query, relevance) pairs OWNED by the scenarios
  themselves (`golden_eval()` on S1/S3/S5/S7 — the same definitions their
  regression metrics use), each run in mock mode across seeds (0, 1).
- Candidate grid: the named weight configurations in ``CANDIDATE_WEIGHTS``
  (current default first). The grid is fixed; no post-hoc additions.
- Decision rule: adopt a candidate iff
    (a) its MEAN Recall@5 across all (scenario, seed) pairs strictly exceeds
        the current default's mean, AND
    (b) it does NOT regress Recall@10 or nDCG@10 on ANY (scenario, seed) pair.
  Ties or violations → keep current weights. "Current weights confirmed" is a
  valid, reportable outcome — not a failure.
- Any adopted change must additionally pass the full golden/ablation test
  suites and a live S1 confirmation before becoming the default.

Mock embeddings make the sweep deterministic; the harness mutates only the
engine's ``fusion_weights`` between searches (the same mechanism the ablation
study uses), so corpora are built once per (scenario, seed).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.logging import get_logger
from mws.eval.metrics import ndcg_at_k, recall_at_k

logger = get_logger(__name__)

#: Current production default (mws/retrieval/engine.py) — always swept first.
CURRENT_WEIGHTS: dict[str, float] = {
    "semantic": 0.4,
    "spatial": 0.2,
    "temporal": 0.2,
    "symbolic": 0.1,
    "structured": 0.1,
    "relational": 0.1,
}

#: Fixed candidate grid (pre-registered; current default first).
CANDIDATE_WEIGHTS: list[tuple[str, dict[str, float]]] = [
    ("current", CURRENT_WEIGHTS),
    ("semantic_heavy", {**CURRENT_WEIGHTS, "semantic": 0.6}),
    ("semantic_light", {**CURRENT_WEIGHTS, "semantic": 0.3}),
    ("symbolic_boost", {**CURRENT_WEIGHTS, "symbolic": 0.25}),
    ("symbolic_heavy", {**CURRENT_WEIGHTS, "symbolic": 0.4}),
    ("structured_light", {**CURRENT_WEIGHTS, "structured": 0.05}),
    ("structured_off", {**CURRENT_WEIGHTS, "structured": 0.0}),
    ("temporal_light", {**CURRENT_WEIGHTS, "temporal": 0.1}),
    ("spatial_light", {**CURRENT_WEIGHTS, "spatial": 0.1}),
    ("relational_boost", {**CURRENT_WEIGHTS, "relational": 0.2}),
    ("sym+struct_light", {**CURRENT_WEIGHTS, "symbolic": 0.25, "structured": 0.05}),
    ("sem_heavy+sym_boost", {**CURRENT_WEIGHTS, "semantic": 0.6, "symbolic": 0.25}),
]

#: Scenario classes providing golden_eval() (imported lazily to avoid cycles).
_GOLDEN_SCENARIOS = (
    (
        "maintenance_handoff",
        "mws.scenarios.s1_maintenance_handoff.scenario",
        "MaintenanceHandoffScenario",
    ),
    (
        "collective_weak_signal",
        "mws.scenarios.s3_collective_weak_signal.scenario",
        "CollectiveWeakSignalScenario",
    ),
    (
        "incident_response",
        "mws.scenarios.s5_incident_response.scenario",
        "IncidentResponseScenario",
    ),
    (
        "order_to_fulfillment",
        "mws.scenarios.s7_order_to_fulfillment.scenario",
        "OrderToFulfillmentScenario",
    ),
)

SEEDS: tuple[int, ...] = (0, 1)


def _build_golden_engines(seeds: tuple[int, ...]) -> list[dict[str, Any]]:
    """Run each golden scenario per seed (mock) and capture engine+gold."""
    import importlib

    triples: list[dict[str, Any]] = []
    for name, module_path, class_name in _GOLDEN_SCENARIOS:
        cls = getattr(importlib.import_module(module_path), class_name)
        for seed in seeds:
            scenario = cls()
            scenario.run(seed=seed)
            query, relevant = scenario.golden_eval()
            triples.append(
                {
                    "scenario": name,
                    "seed": seed,
                    "engine": scenario.engine,
                    "query": query,
                    "relevant": relevant,
                }
            )
    return triples


def _measure(triple: dict[str, Any], weights: dict[str, float]) -> dict[str, float]:
    engine = triple["engine"]
    original = engine.fusion_weights
    try:
        engine.fusion_weights = weights
        hits = [r.atom_id for r in engine.search(triple["query"])]
    finally:
        engine.fusion_weights = original
    relevant = triple["relevant"]
    return {
        "recall_at_5": recall_at_k(hits, relevant, k=5),
        "recall_at_10": recall_at_k(hits, relevant, k=10),
        "ndcg_at_10": ndcg_at_k(hits, relevant, k=10),
    }


def run_fusion_tuning(seeds: tuple[int, ...] = SEEDS) -> dict[str, Any]:
    """Sweep the pre-registered grid and render the pre-registered verdict."""
    triples = _build_golden_engines(seeds)
    baseline = [_measure(t, CURRENT_WEIGHTS) for t in triples]
    baseline_mean_r5 = float(np.mean([m["recall_at_5"] for m in baseline]))

    sweep: list[dict[str, Any]] = []
    for label, weights in CANDIDATE_WEIGHTS:
        per_triple = [_measure(t, weights) for t in triples]
        mean_r5 = float(np.mean([m["recall_at_5"] for m in per_triple]))
        regressions = [
            f"{t['scenario']}/seed{t['seed']}:{metric}"
            for t, m, b in zip(triples, per_triple, baseline, strict=True)
            for metric in ("recall_at_10", "ndcg_at_10")
            if m[metric] < b[metric] - 1e-9
        ]
        sweep.append(
            {
                "label": label,
                "weights": weights,
                "mean_recall_at_5": round(mean_r5, 4),
                "improves_r5": mean_r5 > baseline_mean_r5 + 1e-9,
                "regressions": regressions,
            }
        )
        logger.info(
            "fusion sweep", label=label, mean_r5=round(mean_r5, 4), n_regressions=len(regressions)
        )

    admissible = [c for c in sweep if c["improves_r5"] and not c["regressions"]]
    winner = max(admissible, key=lambda c: c["mean_recall_at_5"]) if admissible else None
    return {
        "pre_registered": {
            "metric": "mean recall_at_5 over scenario-owned golden pairs",
            "scenarios": [name for name, _, _ in _GOLDEN_SCENARIOS],
            "seeds": list(seeds),
            "rule": "adopt iff mean R@5 strictly improves AND no per-pair R@10/nDCG@10 regression",
        },
        "n_eval_pairs": len(triples),
        "baseline_mean_recall_at_5": round(baseline_mean_r5, 4),
        "sweep": sweep,
        "verdict": {
            "adopt": winner is not None,
            "winner": winner["label"] if winner else None,
            "winner_weights": winner["weights"] if winner else None,
            "reason": (
                f"{winner['label']} improves mean R@5 "
                f"{baseline_mean_r5:.4f}→{winner['mean_recall_at_5']:.4f} with no regressions"
                if winner
                else "no candidate beat current weights without a regression — current weights confirmed"
            ),
        },
    }
