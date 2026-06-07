"""Evaluation metrics — single source of truth for all metric computations."""

from eval.metrics.calibration import regression_ece, scalar_calibration_nll
from eval.metrics.contract import contract_accuracy, round_trip_information_loss
from eval.metrics.se3 import joint_max_error, joint_rmse, se3_geodesic_distance

__all__ = [
    "contract_accuracy",
    "joint_max_error",
    "joint_rmse",
    "regression_ece",
    "round_trip_information_loss",
    "scalar_calibration_nll",
    "se3_geodesic_distance",
]
