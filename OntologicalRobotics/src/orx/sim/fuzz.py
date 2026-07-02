"""スキーマ・ファジング（T5, H1）— 合成ベンダースキーマのプロシージャル量産。

フィールド改名・構造変更・単位変更を確率的に施した観測スキーマを生成する。
各ファズスキーマには「真のマッピング」（採点用）とペイロード生成器が付く。
異質性を所与の障害ではなく操作可能な実験変数に変える（PROJECT.md §4.1）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from orx.common.schemas import StrictModel
from orx.sim.sensors import SensedObject

_CONTAINER_NAMES = ["detections", "objects", "dets", "items", "returns"]
_NEST_KEYS = [None, "p", "pos", "coord"]
_X_NAMES = ["x", "px", "pos_x", "loc_x"]
_Y_NAMES = ["y", "py", "pos_y", "loc_y"]
_Z_NAMES = ["z", "pz", "pos_z", "loc_z"]
_SYMBOL_NAMES = ["bc", "barcode", "tag_id", "code"]
_CONF_NAMES = ["cf", "conf", "quality", "q"]
_UNITS = ["m", "cm", "mm"]
_UNIT_SCALE = {"m": 1.0, "cm": 100.0, "mm": 1000.0}


class FuzzSpec(StrictModel):
    """1合成スキーマの仕様（これ自体が真のマッピングを定義する）。"""

    schema_name: str
    container: str
    nest_key: str | None
    x_name: str
    y_name: str
    z_name: str
    units: str
    symbol_name: str | None  # None = 識別子フィールド無し
    conf_name: str


def generate_fuzz_spec(rng: np.random.Generator, index: int) -> FuzzSpec:
    def pick(pool: list) -> Any:
        return pool[int(rng.integers(0, len(pool)))]

    return FuzzSpec(
        schema_name=f"vendor_fuzz_{index:03d}",
        container=pick(_CONTAINER_NAMES),
        nest_key=pick(_NEST_KEYS),
        x_name=pick(_X_NAMES),
        y_name=pick(_Y_NAMES),
        z_name=pick(_Z_NAMES),
        units=pick(_UNITS),
        symbol_name=pick(_SYMBOL_NAMES) if rng.random() < 0.8 else None,
        conf_name=pick(_CONF_NAMES),
    )


def _position_paths(spec: FuzzSpec) -> tuple[str, str, str]:
    if spec.nest_key is None:
        return spec.x_name, spec.y_name, spec.z_name
    return (
        f"{spec.nest_key}.{spec.x_name}",
        f"{spec.nest_key}.{spec.y_name}",
        f"{spec.nest_key}.{spec.z_name}",
    )


def make_payload(spec: FuzzSpec, sensed: list[SensedObject]) -> dict[str, Any]:
    """ファズ仕様に従ってベンダーペイロードを構築する。"""
    scale = _UNIT_SCALE[spec.units]
    dets: list[dict[str, Any]] = []
    for s in sensed:
        coords = {
            spec.x_name: round(s.position[0] * scale, 4),
            spec.y_name: round(s.position[1] * scale, 4),
            spec.z_name: round(s.position[2] * scale, 4),
        }
        det: dict[str, Any] = {spec.nest_key: coords} if spec.nest_key else dict(coords)
        if spec.symbol_name is not None:
            det[spec.symbol_name] = s.barcode
        det[spec.conf_name] = s.confidence
        dets.append(det)
    return {"meta": {"schema": spec.schema_name}, spec.container: dets}


def truth_mapping_yaml(spec: FuzzSpec) -> str:
    """このスキーマの正解リフティング定義（採点・手書きベースラインに使う）。"""
    px, py, pz = _position_paths(spec)
    lines = [
        f"schema: {spec.schema_name}",
        f"detections_path: {spec.container}",
        "sensor_id: fuzzed",
        "fields:",
        "  position:",
        f"    x: {px}",
        f"    y: {py}",
        f"    z: {pz}",
        f"    units: {spec.units}",
    ]
    if spec.symbol_name is not None:
        lines.append(f"  symbol_id: {spec.symbol_name}")
    lines.append(f"  confidence: {spec.conf_name}")
    return "\n".join(lines) + "\n"


def make_samples(
    spec: FuzzSpec, rng: np.random.Generator, n_samples: int = 3
) -> list[dict[str, Any]]:
    """オンボーディング入力となるサンプルペイロード（ベンダー提供物の模擬）。"""
    samples: list[dict[str, Any]] = []
    for _ in range(n_samples):
        sensed = []
        for j in range(int(rng.integers(2, 5))):
            sensed.append(
                SensedObject(
                    true_object_id=f"obj{j}",
                    position=(
                        float(rng.uniform(-1.5, 1.5)),
                        float(rng.uniform(-1.5, 1.5)),
                        float(rng.uniform(0.0, 0.8)),
                    ),
                    barcode=f"BC-{int(rng.integers(100, 999))}" if rng.random() < 0.7 else None,
                    confidence=round(float(rng.uniform(0.6, 0.99)), 4),
                )
            )
        samples.append(make_payload(spec, sensed))
    return samples
