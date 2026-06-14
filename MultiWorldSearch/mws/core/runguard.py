"""Run-time guards and the pre-run mode banner (IMPROVEMENT G1/G2/G5).

Two safety/observability concerns live here so every entrypoint shares one
implementation:

- **Mode visibility (G2/G5)**: a one-line banner stating the resolved cloud
  mode, embedding model, vector backend, and seed — printed *before* a run so
  the operator knows whether real spend is about to happen and that it is a
  single-seed point estimate.
- **Live-spend gate (G1)**: a live run (real Gemini) requires an explicit
  ``MWS_CONFIRM_LIVE_SPEND=1`` opt-in, mirroring ``scenario-multi-seed-live``.
  Mock and replay (zero-cost) runs pass through untouched.
"""

from __future__ import annotations

import os

from mws.core.config import MWSSettings
from mws.core.types import CloudMode

LIVE_SPEND_ENV = "MWS_CONFIRM_LIVE_SPEND"

#: Rough per-scenario live cost (real embedding + ADK), aligned with the
#: estimate the ``scenario-multi-seed-live`` Make target advertises.
EST_COST_PER_SCENARIO_USD = 0.008


def is_live_spend(settings: MWSSettings) -> bool:
    """True only when a run will make real, billable cloud calls.

    Replay (``llm_replay`` set) deterministically reuses recorded responses, so
    it is live-mode but **zero cloud spend** — not gated.
    """
    return settings.cloud_mode == CloudMode.LIVE and not settings.llm_replay


def embedding_model_name(settings: MWSSettings) -> str:
    return "gemini-embedding-2" if settings.cloud_mode == CloudMode.LIVE else "mock"


def format_mode_banner(settings: MWSSettings, seed: int) -> str:
    """One-line, stdout-friendly run banner (G2/G5)."""
    mode = str(getattr(settings.cloud_mode, "value", settings.cloud_mode))
    suffix = " (replay: zero spend)" if settings.llm_replay else ""
    spend = " ⚠ REAL SPEND" if is_live_spend(settings) else ""
    return (
        f"[MWS] cloud_mode={mode}{suffix}{spend} | embedding={embedding_model_name(settings)} "
        f"| vector_backend={settings.vector_backend} | seed={seed} (single-seed point estimate; "
        f"CIs via scenario-multi-seed-all)"
    )


def live_spend_refusal(settings: MWSSettings, n_scenarios: int = 1) -> str | None:
    """Return a refusal message if a live run lacks confirmation, else None (G1)."""
    if not is_live_spend(settings):
        return None
    if os.environ.get(LIVE_SPEND_ENV) == "1":
        return None
    est = EST_COST_PER_SCENARIO_USD * max(1, n_scenarios)
    return (
        f"Refusing to run LIVE (real Gemini spend). cloud_mode=live and "
        f"{LIVE_SPEND_ENV}!=1. Estimated cost: ~${est:.3f} "
        f"({n_scenarios}x ~${EST_COST_PER_SCENARIO_USD:.3f}/scenario, real embedding + ADK). "
        f"Re-run with {LIVE_SPEND_ENV}=1 to proceed, or MWS_CLOUD_MODE=mock for offline."
    )
