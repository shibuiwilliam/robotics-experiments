"""ベンダースキーマ→共通検出形式の宣言的リフティング。

マッピング定義は `ontology/mappings/{schema}.yaml`。新ロボットの統合は
コード変更ではなくマッピング追加で行う（H1 ハブ&スポーク）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field

from orx.common.config import load_config
from orx.common.paths import mappings_dir
from orx.common.schemas import Detection, RawObservation, StrictModel

_UNIT_SCALE = {"m": 1.0, "cm": 0.01, "mm": 0.001}


class PositionMapping(StrictModel):
    x: str
    y: str
    z: str
    units: str = "m"


class VendorMapping(StrictModel):
    """1ベンダースキーマのリフティング定義。"""

    schema_name: str
    detections_path: str
    sensor_id: str
    position: PositionMapping
    symbol_id: str | None = None
    confidence: str | None = None

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> VendorMapping:
        fields = data.get("fields", {})
        return cls(
            schema_name=data["schema"],
            detections_path=data["detections_path"],
            sensor_id=data.get("sensor_id", "sensor"),
            position=PositionMapping.model_validate(fields["position"]),
            symbol_id=fields.get("symbol_id"),
            confidence=fields.get("confidence"),
        )


class _RawMappingFile(StrictModel):
    """YAMLファイルの素の形（fields はベンダー毎に異質なので緩く受ける）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str | None = Field(default=None, alias="schema")
    detections_path: str
    sensor_id: str = "sensor"
    fields: dict[str, Any]


def load_mapping(schema: str, directory: Path | None = None) -> VendorMapping:
    path = (directory or mappings_dir()) / f"{schema}.yaml"
    raw = load_config(path, _RawMappingFile)
    return VendorMapping.from_yaml_dict(
        {
            "schema": raw.schema_name or schema,
            "detections_path": raw.detections_path,
            "sensor_id": raw.sensor_id,
            "fields": raw.fields,
        }
    )


def _resolve(payload: dict[str, Any], dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(f"リフティング失敗: パス {dotted!r} が payload にありません")
        cur = cur[part]
    return cur


def lift(obs: RawObservation, mapping: VendorMapping) -> list[Detection]:
    """ベンダー観測を共通 Detection 列へ正規化する（埋め込みは未付与）。"""
    if obs.vendor_schema != mapping.schema_name:
        raise ValueError(
            f"スキーマ不一致: 観測 {obs.vendor_schema!r} に対しマッピング "
            f"{mapping.schema_name!r}"
        )
    dets_raw = _resolve(obs.payload, mapping.detections_path)
    if not isinstance(dets_raw, list):
        raise ValueError(f"検出列 {mapping.detections_path!r} がリストではありません")
    scale = _UNIT_SCALE.get(mapping.position.units)
    if scale is None:
        raise ValueError(f"未知の単位 {mapping.position.units!r}")
    detections: list[Detection] = []
    for index, det in enumerate(dets_raw):
        x = float(_resolve(det, mapping.position.x)) * scale
        y = float(_resolve(det, mapping.position.y)) * scale
        z = float(_resolve(det, mapping.position.z)) * scale
        symbol = _resolve(det, mapping.symbol_id) if mapping.symbol_id else None
        confidence = float(_resolve(det, mapping.confidence)) if mapping.confidence else 1.0
        detections.append(
            Detection(
                sensor_id=mapping.sensor_id,
                index=index,
                position=(x, y, z),
                symbol_id=symbol if symbol is None or isinstance(symbol, str) else str(symbol),
                confidence=confidence,
            )
        )
    return detections
