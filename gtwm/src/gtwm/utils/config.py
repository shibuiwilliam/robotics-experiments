"""Hydra 設定のロード補助。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omegaconf import DictConfig, OmegaConf


def load_config(path: str | Path) -> DictConfig:
    """単一の YAML 設定ファイルを読み込む（Hydra compose を使わない単純ロード）。"""
    cfg = OmegaConf.load(Path(path))
    if not isinstance(cfg, DictConfig):
        raise TypeError(f"{path} はマッピング型の設定ではありません")
    return cfg


def to_container(cfg: DictConfig) -> dict[str, Any]:
    """DictConfig をプレーンな dict に変換する。"""
    result = OmegaConf.to_container(cfg, resolve=True)
    if not isinstance(result, dict):
        raise TypeError("設定はマッピング型である必要があります")
    return {str(k): v for k, v in result.items()}
