"""リポジトリルートなど共通パスの解決。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """`pyproject.toml` を目印に、このファイルから上方向へリポジトリルートを探す。"""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("リポジトリルート（pyproject.toml）が見つかりません")


def warehouse_xml_path() -> Path:
    return repo_root() / "sim" / "assets" / "warehouse.xml"


def registry_yaml_path() -> Path:
    return repo_root() / "sim" / "assets" / "registry.yaml"


def data_dir() -> Path:
    return repo_root() / "data"
