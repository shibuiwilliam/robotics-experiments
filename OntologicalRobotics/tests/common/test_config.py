from pathlib import Path

import pytest

from orx.common.config import ConfigError, WorldConfig, config_hash, load_config

MINIMAL_WORLD = """
name: tiny
zones:
  - {name: shelf_a, center: [0.0, 0.0, 0.1], size: [1.0, 0.5, 0.4]}
boxes:
  - {name: b1, zone: shelf_a, barcode: BC-001}
robots:
  - name: arm_a
    vendor_schema: vendor_arm_a
    camera: {pos: [1.5, 0.0, 1.0], lookat: [0.0, 0.0, 0.1]}
"""


def test_load_world_config(tmp_path: Path) -> None:
    p = tmp_path / "w.yaml"
    p.write_text(MINIMAL_WORLD, encoding="utf-8")
    cfg = load_config(p, WorldConfig)
    assert cfg.name == "tiny"
    assert cfg.boxes[0].barcode == "BC-001"
    assert cfg.degradation.id_read_failure_rate == 0.0


def test_missing_file_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="見つかりません"):
        load_config(tmp_path / "nope.yaml", WorldConfig)


def test_invalid_config_actionable_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("name: x\nzones: []\nboxes: []\nrobots: []\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="検証エラー"):
        load_config(p, WorldConfig)


def test_config_hash_stable_and_sensitive(tmp_path: Path) -> None:
    p = tmp_path / "w.yaml"
    p.write_text(MINIMAL_WORLD, encoding="utf-8")
    a = load_config(p, WorldConfig)
    b = load_config(p, WorldConfig)
    assert config_hash(a) == config_hash(b)
    b2 = b.model_copy(update={"name": "other"})
    assert config_hash(a) != config_hash(b2)
