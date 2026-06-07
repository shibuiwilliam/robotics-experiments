"""Shared scenario evaluation infrastructure.

Every scenario produces a ScenarioResult with two-layer success:
  1. Business success — the scenario-specific task goal was met
  2. PSL success — the breakpoint-specific PSL metric met its threshold

A scenario that achieves business success without PSL success is INVALID
because a naive translator can succeed by luck.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class BreakpointResult:
    """Result of a breakpoint assertion.

    Fields:
        name: Breakpoint identifier (e.g. 'calibration_nll', 'commutativity').
        triggered: Whether the breakpoint was actually caused during the scenario.
        metric_value: The measured metric value.
        threshold: The threshold for PSL success.
        passed: Whether metric_value meets the threshold.
    """

    name: str
    triggered: bool
    metric_value: float
    threshold: float
    passed: bool


@dataclass
class ScenarioResult:
    """Two-layer result for a scenario evaluation.

    Fields:
        scenario_id: e.g. 's1_mixed_fleet_pick'.
        seed: Random seed used.
        business_success: Whether the business task goal was met.
        psl_success: Whether all PSL breakpoint metrics met thresholds.
        breakpoints: Per-breakpoint results.
        metrics: All collected metrics (for manifests/analysis).
        rq_contributions: Which RQs/Hs this run provides evidence for.
        wall_time_s: Wall clock time for this run.
        baseline_name: None for PSL, 'B0'/'B1'/'B2' for baselines.
    """

    scenario_id: str
    seed: int
    business_success: bool
    psl_success: bool
    breakpoints: list[BreakpointResult] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)
    rq_contributions: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0
    baseline_name: str | None = None

    @property
    def overall_pass(self) -> bool:
        """Both layers must pass for overall success."""
        return self.business_success and self.psl_success


@dataclass
class DoseResponsePoint:
    """One point on a dose-response curve."""

    dose_label: str
    dose_value: float
    method: str  # 'PSL', 'B0', 'B1', 'B2'
    business_success_rate: float
    psl_metric: float
    n_seeds: int


def load_scenario_config(config_path: str | Path) -> dict[str, object]:
    """Load a scenario YAML config.

    Args:
        config_path: Path to the scenario YAML file.

    Returns:
        Parsed config dict.
    """
    with open(config_path) as f:
        return yaml.safe_load(f)  # type: ignore[no-any-return]


def save_scenario_result(result: ScenarioResult, output_dir: str | Path) -> Path:
    """Save a scenario result to a run directory.

    Args:
        result: The scenario evaluation result.
        output_dir: Directory to save to.

    Returns:
        Path to the saved JSON file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.scenario_id}_result.json"
    path.write_text(json.dumps(asdict(result), indent=2, default=str))
    return path
