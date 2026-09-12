"""デバイス選択：mps -> cpu の優先順位のみを許可する。"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_device() -> str:
    """利用可能な最適デバイス名を返す（"mps" または "cpu"）。"""
    try:
        import torch
    except ImportError:
        return "cpu"

    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
