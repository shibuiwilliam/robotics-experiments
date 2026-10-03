"""接地層（セマンティック・ブリッジ）。プローブ、較正、事象昇格、同一性・空間束縛、検証。

責務: 記号化、事象昇格、同一性・空間束縛、較正、制約検証。
非責務: **状態の永続化**（イベントを出すだけ）、信念統合。

この層が中核テーゼ 3「接地は分類ではなく写像の対である」を実装する。抽象化写像 A（潜在 →
記号）と記号遷移 T（事象計算）の可換性を整合残差として測り、プローブの昇格条件に入れる。
"""

from __future__ import annotations

from sbp.bridge.alignment import (
    AlignmentReport,
    SymbolicTransition,
    alignment_residual,
    equipment_maintenance_transition,
    symbolize,
)
from sbp.bridge.bocpd import BocpdDetector, ChangePoint, apply_hysteresis, detect_change_points
from sbp.bridge.calibration import (
    Calibrator,
    ConformalRegressor,
    TemperatureScaler,
    reliability_diagram,
)
from sbp.bridge.lifting import (
    EventCalculusRules,
    FluentChange,
    LiftedEvent,
    lift_events,
    load_event_calculus,
)
from sbp.bridge.mil import TimeBag, fit_with_bags, mil_loss
from sbp.bridge.probes import (
    LinearProbe,
    ProbeKind,
    ProbeMetrics,
    ProbeOutput,
    ProbeSpec,
    ProbeStatus,
    auroc,
    expected_calibration_error,
    promote,
)

__all__ = [
    "AlignmentReport",
    "BocpdDetector",
    "Calibrator",
    "ChangePoint",
    "ConformalRegressor",
    "EventCalculusRules",
    "FluentChange",
    "LiftedEvent",
    "LinearProbe",
    "ProbeKind",
    "ProbeMetrics",
    "ProbeOutput",
    "ProbeSpec",
    "ProbeStatus",
    "SymbolicTransition",
    "TemperatureScaler",
    "TimeBag",
    "alignment_residual",
    "apply_hysteresis",
    "auroc",
    "detect_change_points",
    "equipment_maintenance_transition",
    "expected_calibration_error",
    "fit_with_bags",
    "lift_events",
    "load_event_calculus",
    "mil_loss",
    "promote",
    "reliability_diagram",
    "symbolize",
]
