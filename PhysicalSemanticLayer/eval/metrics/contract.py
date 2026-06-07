"""Contract accuracy metrics — declared info loss vs measured.

Compares the information loss declared by FidelityContracts against
the actual measured loss (from ground truth).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def contract_accuracy(
    declared_loss: float,
    actual_loss: float,
) -> float:
    """Absolute error between declared and actual information loss.

    Args:
        declared_loss: Loss claimed by the FidelityContract [0, 1].
        actual_loss: Measured loss from ground truth comparison [0, 1].

    Returns:
        |declared - actual| — 0 means perfect contract.
    """
    return abs(declared_loss - actual_loss)


def round_trip_information_loss(
    original: NDArray[np.float64],
    reconstructed: NDArray[np.float64],
) -> float:
    """Estimate information loss from a round-trip translation.

    Uses normalized reconstruction error as a proxy for mutual info loss.

    Args:
        original: Original values.
        reconstructed: Values after native → IR → native round trip.

    Returns:
        Normalized loss in [0, 1].
    """
    err = float(np.linalg.norm(original - reconstructed))
    ref = float(np.linalg.norm(original))
    if ref < 1e-12:
        return 0.0 if err < 1e-12 else 1.0
    return float(np.clip(err / ref, 0.0, 1.0))
