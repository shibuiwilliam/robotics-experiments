"""T5 オンボーディングスイート（H1）— 支援マッピング生成 vs 手書き。

ワークフロー（`orx onboard`）:
  1. ベンダー提供のサンプルペイロードを読む
  2. マッピング案を生成（heuristic: 規則ベース / llm: LLM支援・live）
  3. 検証: 案をサンプルに適用してリフティングできるか＋値の妥当性
  4. 真のマッピングとの差分（自動生成精度・人手修正行数）で採点

統合コスト = 人手で書く/直すYAML行数。手書きベースラインは全行。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from orx.common.providers import LLMClient, LLMRequest
from orx.common.schemas import RawObservation, StrictModel
from orx.perception.lifting import VendorMapping, lift
from orx.sim.fuzz import FuzzSpec, truth_mapping_yaml

_XYZ_PATTERNS = {
    "x": re.compile(r"(^|_)(x|px|pos_x|loc_x|east)$"),
    "y": re.compile(r"(^|_)(y|py|pos_y|loc_y|north)$"),
    "z": re.compile(r"(^|_)(z|pz|pos_z|loc_z|up)$"),
}
_SYMBOL_PATTERN = re.compile(r"(bc|barcode|tag|code|id)", re.IGNORECASE)
_CONF_PATTERN = re.compile(r"(cf|conf|quality|score|^q$)", re.IGNORECASE)


class OnboardResult(StrictModel):
    schema_name: str
    mode: str  # heuristic | llm
    proposed_yaml: str
    valid: bool
    validation_errors: list[str]
    field_accuracy: float  # 真マッピングと一致したスロット率
    hand_fix_lines: int  # 真マッピングとの差分行数（人手修正コスト）
    handwritten_lines: int  # 手書きベースラインの総行数
    elapsed_s: float


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, path))
        else:
            out[path] = v
    return out


def _find_detections_path(payload: dict[str, Any], prefix: str = "") -> str | None:
    for k, v in payload.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, list) and v and all(isinstance(e, dict) for e in v):
            return path
        if isinstance(v, dict):
            found = _find_detections_path(v, path)
            if found:
                return found
    return None


def heuristic_infer(schema_name: str, samples: list[dict[str, Any]]) -> VendorMapping:
    """規則ベースのマッピング推定（名前パターン＋値レンジで単位推定）。"""
    dets_path = _find_detections_path(samples[0])
    if dets_path is None:
        raise ValueError("検出リストが見つかりません")
    all_dets: list[dict[str, Any]] = []
    for sample in samples:
        cur: Any = sample
        for part in dets_path.split("."):
            cur = cur[part]
        all_dets.extend(cur)
    flat_keys: dict[str, list[Any]] = {}
    for det in all_dets:
        for path, value in _flatten(det).items():
            flat_keys.setdefault(path, []).append(value)

    axes: dict[str, str] = {}
    for path, values in flat_keys.items():
        leaf = path.split(".")[-1].lower()
        if not all(isinstance(v, int | float) for v in values):
            continue
        for axis, pattern in _XYZ_PATTERNS.items():
            if axis not in axes and pattern.search(leaf):
                axes[axis] = path
    if set(axes) != {"x", "y", "z"}:
        raise ValueError(f"座標フィールドを特定できません: {axes}")

    # 単位推定: 倉庫世界の座標は ±数m。ピーク値が5以下ならm、500以下ならcm、
    # それ以上はmm（1500 は「15m(cm)」より「1.5m(mm)」が妥当）
    magnitudes = [
        abs(float(v)) for p in axes.values() for v in flat_keys[p]
    ]
    peak = max(magnitudes) if magnitudes else 0.0
    units = "m" if peak <= 5 else ("cm" if peak <= 500 else "mm")

    symbol_path: str | None = None
    conf_path: str | None = None
    for path, values in flat_keys.items():
        if path in axes.values():
            continue
        leaf = path.split(".")[-1]
        if symbol_path is None and _SYMBOL_PATTERN.search(leaf) and any(
            isinstance(v, str) for v in values
        ):
            symbol_path = path
        if conf_path is None and _CONF_PATTERN.search(leaf) and all(
            isinstance(v, int | float) and 0 <= float(v) <= 1 for v in values
        ):
            conf_path = path

    return VendorMapping.from_yaml_dict(
        {
            "schema": schema_name,
            "detections_path": dets_path,
            "sensor_id": "fuzzed",
            "fields": {
                "position": {
                    "x": axes["x"], "y": axes["y"], "z": axes["z"], "units": units,
                },
                **({"symbol_id": symbol_path} if symbol_path else {}),
                **({"confidence": conf_path} if conf_path else {}),
            },
        }
    )


_LLM_PROMPT = (
    "あなたはロボットベンダーの観測スキーマを共通形式へ写像するエンジニアです。\n"
    "サンプルペイロードから、次のJSONだけを出力してください:\n"
    '{"detections_path": str, "x": str, "y": str, "z": str, '
    '"units": "m|cm|mm", "symbol_id": str|null, "confidence": str|null}\n'
    "パスはドット区切り。units は座標値の大きさから推定すること。\n"
)


def llm_infer(
    schema_name: str, samples: list[dict[str, Any]], llm: LLMClient
) -> VendorMapping:
    """LLM支援のマッピング推定（live・キャッシュ記録。stubでは検証エラーになる）。"""
    request = LLMRequest(
        messages=[
            {"role": "system", "content": _LLM_PROMPT},
            {
                "role": "user",
                "content": "サンプル:\n" + json.dumps(samples[:2], ensure_ascii=False),
            },
        ]
    )
    response = llm.complete(request)
    data = json.loads(response.content or "{}")
    return VendorMapping.from_yaml_dict(
        {
            "schema": schema_name,
            "detections_path": data["detections_path"],
            "sensor_id": "fuzzed",
            "fields": {
                "position": {
                    "x": data["x"], "y": data["y"], "z": data["z"],
                    "units": data["units"],
                },
                **({"symbol_id": data["symbol_id"]} if data.get("symbol_id") else {}),
                **({"confidence": data["confidence"]} if data.get("confidence") else {}),
            },
        }
    )


def validate_mapping(
    mapping: VendorMapping, schema_name: str, samples: list[dict[str, Any]]
) -> list[str]:
    """マッピング案をサンプルに適用して検証する（SHACL前段の構造検証）。"""
    errors: list[str] = []
    for i, sample in enumerate(samples):
        obs = RawObservation(
            robot_id="onboard", vendor_schema=schema_name, sim_time=0.0, seq=i,
            payload=sample,
        )
        try:
            detections = lift(obs, mapping)
        except (KeyError, ValueError) as exc:
            errors.append(f"sample{i}: {exc}")
            continue
        for det in detections:
            if max(abs(v) for v in det.position) > 10.0:
                errors.append(
                    f"sample{i}: 位置が世界境界外 {det.position}（単位推定の誤り?）"
                )
                break
    return errors


def mapping_to_yaml(mapping: VendorMapping) -> str:
    lines = [
        f"schema: {mapping.schema_name}",
        f"detections_path: {mapping.detections_path}",
        f"sensor_id: {mapping.sensor_id}",
        "fields:",
        "  position:",
        f"    x: {mapping.position.x}",
        f"    y: {mapping.position.y}",
        f"    z: {mapping.position.z}",
        f"    units: {mapping.position.units}",
    ]
    if mapping.symbol_id:
        lines.append(f"  symbol_id: {mapping.symbol_id}")
    if mapping.confidence:
        lines.append(f"  confidence: {mapping.confidence}")
    return "\n".join(lines) + "\n"


def score_against_truth(proposed_yaml: str, truth_yaml: str) -> tuple[float, int, int]:
    """(スロット一致率, 修正行数, 手書き総行数)。差分行 = 人手で直す行。"""
    truth_lines = [line for line in truth_yaml.strip().splitlines() if line.strip()]
    proposed_lines = [line for line in proposed_yaml.strip().splitlines() if line.strip()]
    truth_set = set(truth_lines)
    proposed_set = set(proposed_lines)
    missing = truth_set - proposed_set  # 直す/足す必要のある行
    matched = len(truth_set & proposed_set)
    accuracy = matched / len(truth_set)
    return accuracy, len(missing), len(truth_lines)


def onboard(
    spec: FuzzSpec,
    samples: list[dict[str, Any]],
    mode: str,
    llm: LLMClient | None = None,
) -> OnboardResult:
    start = time.perf_counter()
    errors: list[str] = []
    proposed_yaml = ""
    try:
        if mode == "heuristic":
            mapping = heuristic_infer(spec.schema_name, samples)
        elif mode == "llm":
            if llm is None:
                raise ValueError("mode=llm には LLMクライアントが必要です")
            mapping = llm_infer(spec.schema_name, samples, llm)
        else:
            raise ValueError(f"未知のオンボーディングモード {mode!r}")
        proposed_yaml = mapping_to_yaml(mapping)
        errors = validate_mapping(mapping, spec.schema_name, samples)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        errors = [f"{type(exc).__name__}: {exc}"]
    elapsed = time.perf_counter() - start
    truth_yaml = truth_mapping_yaml(spec)
    if proposed_yaml:
        accuracy, fix_lines, total_lines = score_against_truth(proposed_yaml, truth_yaml)
    else:
        total_lines = len(truth_yaml.strip().splitlines())
        accuracy, fix_lines = 0.0, total_lines
    return OnboardResult(
        schema_name=spec.schema_name,
        mode=mode,
        proposed_yaml=proposed_yaml,
        valid=not errors,
        validation_errors=errors,
        field_accuracy=round(accuracy, 4),
        hand_fix_lines=fix_lines,
        handwritten_lines=total_lines,
        elapsed_s=round(elapsed, 4),
    )
