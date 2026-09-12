"""乱数シード制御。乱数はこのモジュール経由でのみ設定する。"""

from __future__ import annotations

import os
import random


def seed_everything(seed: int) -> None:
    """random / numpy / torch のシードを揃える。"""
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)

    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass

    try:
        import torch

        torch.manual_seed(seed)
        if torch.backends.mps.is_available():
            torch.mps.manual_seed(seed)
    except ImportError:
        pass
