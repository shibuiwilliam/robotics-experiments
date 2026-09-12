"""`sim/assets/registry.yaml`（個体名 → オントロジー個体 ID）の読み込み。"""

from __future__ import annotations

from functools import lru_cache

import yaml

from gtwm.utils.paths import registry_yaml_path


@lru_cache(maxsize=1)
def load_registry() -> dict[str, dict[str, str]]:
    """カテゴリ別の {sim名: gt:個体ID} マッピングを返す。"""
    with registry_yaml_path().open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise TypeError("registry.yaml はマッピング型である必要があります")
    return data


def ontology_id(sim_name: str) -> str | None:
    """個体名（例: "pallet:1"）からオントロジー個体 ID（例: "gt:Pallet_0001"）を引く。"""
    registry = load_registry()
    for category in registry.values():
        if sim_name in category:
            return category[sim_name]
    return None
