"""EventType から ``schemas/*.json``（JSON Schema）を生成する（``make gen``）。

設計書 §6.1「スキーマレジストリ」に従い、JSON Schema はオントロジーの EventType から生成し、
後方互換のみを許す。生成物は手編集しない。

生成するのは 2 種類:

- ``event-envelope.schema.json`` — 全イベント共通のエンベロープ（``EventEnvelope`` と往復可能）
- ``events/<local-name>.schema.json`` — 事象型ごとのスキーマ（``type`` を定数で固定したもの）
"""

from __future__ import annotations

import json
import sys
from typing import Any

from scripts._ontology import SCHEMAS_DIR, Term, event_types, load_ontology, ontology_version

_SEMVER_PATTERN = r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$"
_IRI_PATTERN = r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*:)(?://)?[^\s<>\"{}|\\^`]+$"

_MODALITIES = ["observed", "inferred", "predicted", "planned", "normative", "hypothetical"]


def _belief_schema() -> dict[str, Any]:
    """信念（判別可能 union）のスキーマ。生スコアは belief に入れない（不変条件 7）。"""
    uncertainty = {
        "type": "object",
        "description": "認識的／偶然的不確実性の分解（設計書 §5.6）。",
        "properties": {
            "epistemic": {"type": "number", "minimum": 0},
            "aleatoric": {"type": "number", "minimum": 0},
        },
        "required": ["epistemic", "aleatoric"],
        "additionalProperties": False,
    }
    common = {
        "calibrated": {
            "const": True,
            "description": "較正済みであることの明示（不変条件 7）。",
        },
        "uncertainty": {"anyOf": [uncertainty, {"type": "null"}]},
    }
    return {
        "description": "較正済みの確信度。kind で判別する（設計書 §3.5、付録 A.1）。",
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "boolean"},
                    "p": {"type": "number", "minimum": 0, "maximum": 1},
                    **common,
                },
                "required": ["kind", "p"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "categorical"},
                    "pmf": {
                        "type": "object",
                        "minProperties": 1,
                        "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    **common,
                },
                "required": ["kind", "pmf"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "numeric"},
                    "mean": {"type": "number"},
                    "var": {"type": "number", "minimum": 0},
                    "quantiles": {
                        "anyOf": [
                            {"type": "object", "additionalProperties": {"type": "number"}},
                            {"type": "null"},
                        ]
                    },
                    "unit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    **common,
                },
                "required": ["kind", "mean", "var"],
                "additionalProperties": False,
            },
        ],
    }


def _evidence_schema() -> dict[str, Any]:
    """証拠の判別可能 union。生スコアはここに残す。"""
    valid_time_ref = {"$ref": "#/$defs/validTime"}
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "probe"},
                    "probe_id": {"type": "string", "pattern": _IRI_PATTERN},
                    "wm_version": {
                        "anyOf": [{"type": "string", "pattern": _IRI_PATTERN}, {"type": "null"}]
                    },
                    "window": {"anyOf": [valid_time_ref, {"type": "null"}]},
                    "raw_score": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                },
                "required": ["kind", "probe_id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "record"},
                    "record": {"type": "string", "pattern": _IRI_PATTERN},
                    "source": {"type": "string", "pattern": _IRI_PATTERN},
                    "source_reliability": {
                        "anyOf": [{"type": "string", "pattern": _IRI_PATTERN}, {"type": "null"}]
                    },
                },
                "required": ["kind", "record", "source"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "rule"},
                    "rule": {"type": "string", "pattern": _IRI_PATTERN},
                    "norm": {
                        "anyOf": [{"type": "string", "pattern": _IRI_PATTERN}, {"type": "null"}]
                    },
                },
                "required": ["kind", "rule"],
                "additionalProperties": False,
            },
        ]
    }


def envelope_schema(version: str) -> dict[str, Any]:
    """全イベント共通のエンベロープスキーマ（設計書 §6.1）。"""
    iri = {"type": "string", "pattern": _IRI_PATTERN}
    optional_iri = {"anyOf": [iri, {"type": "null"}]}
    uuid = {"type": "string", "format": "uuid"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://sbp.example/schemas/event-envelope.schema.json",
        "title": "EventEnvelope",
        "description": (
            "全イベント共通のエンベロープ。生成物（make gen）であり手編集しない。"
            f"生成元オントロジー版 {version}。"
        ),
        "type": "object",
        "properties": {
            "event_id": {
                **uuid,
                "description": "UUIDv7。重複排除の鍵（不変条件 4）。",
            },
            "schema_version": {"type": "string", "pattern": _SEMVER_PATTERN},
            "type": iri,
            "subject": {
                **iri,
                "description": "主体実体の IRI。パーティションキーであり順序保証の単位。",
            },
            "valid_time": {"$ref": "#/$defs/validTime"},
            "tx_time": {"type": "string", "format": "date-time"},
            "modality": {
                "enum": _MODALITIES,
                "description": "事実の身分（不変条件 3）。",
            },
            "belief": {"$ref": "#/$defs/belief"},
            "evidence": {"type": "array", "items": {"$ref": "#/$defs/evidence"}},
            "producer": {
                "type": "object",
                "properties": {"agent": {"type": "string"}, "version": {"type": "string"}},
                "required": ["agent", "version"],
                "additionalProperties": False,
            },
            "causation_id": {"anyOf": [uuid, {"type": "null"}]},
            "correlation_id": {"anyOf": [uuid, {"type": "null"}]},
            "situation": optional_iri,
            "ontology_version": {"type": "string", "pattern": _SEMVER_PATTERN},
        },
        "required": [
            "event_id",
            "schema_version",
            "type",
            "subject",
            "valid_time",
            "tx_time",
            "modality",
            "belief",
            "producer",
            "ontology_version",
        ],
        "additionalProperties": False,
        "$defs": {
            "validTime": {
                "type": "object",
                "description": (
                    "有効時間。右開区間 [start, end) と変化点の不確実幅。"
                    "点時刻のイベントを出さないため uncertainty_s は必須（不変条件 8）。"
                ),
                "properties": {
                    "start": {"type": "string", "format": "date-time"},
                    "end": {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]},
                    "uncertainty_s": {"type": "number", "minimum": 0},
                },
                "required": ["start", "uncertainty_s"],
                "additionalProperties": False,
            },
            "belief": _belief_schema(),
            "evidence": _evidence_schema(),
        },
    }


def event_schema(term: Term, version: str) -> dict[str, Any]:
    """事象型ごとのスキーマ（``type`` を定数で固定し、エンベロープを参照する）。"""
    label = term.label_ja or term.label_en or term.local_name
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://sbp.example/schemas/events/{_file_stem(term)}.schema.json",
        "title": term.local_name,
        "description": (
            f"{label}。{term.definition or ''} "
            f"生成物（make gen）であり手編集しない。生成元オントロジー版 {version}。"
        ).strip(),
        "allOf": [{"$ref": "../event-envelope.schema.json"}],
        "properties": {"type": {"const": term.iri}},
        "required": ["type"],
    }


def _file_stem(term: Term) -> str:
    """ローカル名を kebab-case のファイル名に変換する。"""
    out: list[str] = []
    for index, char in enumerate(term.local_name):
        if char.isupper() and index > 0:
            out.append("-")
        out.append(char.lower())
    return "".join(out)


def _write_json(path: Any, payload: dict[str, Any]) -> bool:
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return previous != content


def main() -> int:
    """JSON Schema 群を生成する。"""
    graph = load_ontology()
    version = ontology_version()
    changed = 0

    if _write_json(SCHEMAS_DIR / "event-envelope.schema.json", envelope_schema(version)):
        changed += 1

    types = event_types(graph)
    generated = {f"{_file_stem(t)}.schema.json" for t in types}
    for term in types:
        if _write_json(
            SCHEMAS_DIR / "events" / f"{_file_stem(term)}.schema.json", event_schema(term, version)
        ):
            changed += 1

    # オントロジーから消えた事象型のスキーマは残さない（生成物は常にオントロジーの像）。
    events_dir = SCHEMAS_DIR / "events"
    if events_dir.exists():
        for stale in sorted(events_dir.glob("*.schema.json")):
            if stale.name not in generated:
                stale.unlink()
                changed += 1

    sys.stdout.write(
        f"gen_schemas: 1 envelope + {len(types)} event types -> schemas/ "
        f"({changed} file(s) changed)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
