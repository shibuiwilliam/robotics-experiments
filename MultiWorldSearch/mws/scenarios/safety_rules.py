"""Deterministic safety rule engine for counterfactual risk assessment (H6).

Risk scores here are RULE-BASED (computed from how far an observed value
exceeds an SOP limit), not hard-coded constants and not Monte-Carlo
frequencies. The decision (AVOID vs PROCEED) follows from the computed risk
crossing a threshold. Loosen the SOP limit (or reduce the observed value) and
the risk falls and the decision flips — i.e. the safety verdict is falsifiable.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default risk sensitivity: how sharply risk rises as the observed value
# exceeds the limit. risk = 1 - 1/(1 + k * margin), margin = (obs-limit)/limit.
DEFAULT_RISK_SENSITIVITY = 8.0
DEFAULT_RISK_THRESHOLD = 0.5


@dataclass
class RiskAssessment:
    """Outcome of assessing one hazard against its SOP limit."""

    observed: float
    limit: float
    exceedance_ratio: float  # (observed - limit) / limit, clamped at 0 below limit
    risk_score: float  # in [0, 1)
    exceeds_limit: bool
    decision: str  # "AVOID" or "PROCEED"


def exceedance_risk(observed: float, limit: float, sensitivity: float) -> float:
    """Monotonic, saturating risk as a function of limit exceedance.

    Returns 0.0 when at or below the limit, rising toward 1.0 as the observed
    value exceeds the limit. Deterministic — same inputs, same output.
    """
    if limit <= 0:
        return 0.0
    margin = (observed - limit) / limit
    if margin <= 0:
        return 0.0
    return 1.0 - 1.0 / (1.0 + sensitivity * margin)


def assess_risk(
    observed: float,
    limit: float,
    *,
    sensitivity: float = DEFAULT_RISK_SENSITIVITY,
    threshold: float = DEFAULT_RISK_THRESHOLD,
) -> RiskAssessment:
    """Assess a 'higher is more dangerous' hazard (stack height, pressure)."""
    risk = exceedance_risk(observed, limit, sensitivity)
    exceeds = observed > limit
    decision = "AVOID" if risk >= threshold else "PROCEED"
    ratio = max(0.0, (observed - limit) / limit) if limit > 0 else 0.0
    return RiskAssessment(
        observed=observed,
        limit=limit,
        exceedance_ratio=ratio,
        risk_score=risk,
        exceeds_limit=exceeds,
        decision=decision,
    )
