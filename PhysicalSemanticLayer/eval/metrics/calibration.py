"""Calibration metrics — does declared uncertainty match actual errors?

Measures whether the covariance declared by Phytes is well-calibrated
against the actual error distribution (ground truth − predicted).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def scalar_calibration_nll(
    predicted: NDArray[np.float64],
    ground_truth: NDArray[np.float64],
    declared_variance: NDArray[np.float64],
) -> float:
    """Negative log-likelihood under Gaussian assumption.

    For each dimension, assumes the prediction is N(predicted, declared_variance).
    Computes average NLL of ground_truth under that distribution.

    Args:
        predicted: (N,) predicted values.
        ground_truth: (N,) ground truth values.
        declared_variance: (N,) declared variance for each dimension.

    Returns:
        Average NLL (lower = better calibrated).
    """
    # Clamp variance to avoid log(0)
    var = np.maximum(declared_variance, 1e-12)
    residuals = ground_truth - predicted
    nll = 0.5 * np.log(2 * np.pi * var) + 0.5 * residuals**2 / var
    return float(np.mean(nll))


def regression_ece(
    predicted: NDArray[np.float64],
    ground_truth: NDArray[np.float64],
    declared_std: NDArray[np.float64],
    n_bins: int = 10,
) -> float:
    """Regression Expected Calibration Error (ECE).

    For well-calibrated predictions, the fraction of residuals within
    k standard deviations should match the Gaussian CDF.

    Args:
        predicted: (N,) predicted values.
        ground_truth: (N,) ground truth values.
        declared_std: (N,) declared standard deviations.
        n_bins: Number of confidence level bins.

    Returns:
        ECE in [0, 1] (lower = better calibrated).
    """
    from scipy.stats import norm

    std = np.maximum(declared_std, 1e-12)
    z_scores = np.abs((ground_truth - predicted) / std)

    # Check at n_bins confidence levels
    confidence_levels = np.linspace(0.1, 0.99, n_bins)
    ece = 0.0
    for p in confidence_levels:
        # For a Gaussian, the expected fraction within z_p std devs:
        z_p = norm.ppf((1 + p) / 2)
        expected_frac = p
        actual_frac = float(np.mean(z_scores <= z_p))
        ece += abs(actual_frac - expected_frac)

    return ece / n_bins
