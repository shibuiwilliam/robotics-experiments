"""イベントの JSON Schema 検証（設計書 §6.1「スキーマレジストリ」）。

Producer は送信前に ``schemas/`` の JSON Schema で検証する（knowledge ルール）。pydantic による
型検証に加えてスキーマ検証も通すのは、**生成物（schemas/）が契約の正**であり、コードの型が
それから逸れていないことを送信のたびに確かめるためである。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from sbp.core.errors import SchemaValidationError
from sbp.core.events import EventEnvelope

__all__ = ["SCHEMAS_DIR", "envelope_validator", "validate_event"]

SCHEMAS_DIR: Final = Path(__file__).resolve().parents[3] / "schemas"
"""生成済み JSON Schema の置き場（``make gen`` の生成物）。"""

_ENVELOPE_SCHEMA_PATH: Final = SCHEMAS_DIR / "event-envelope.schema.json"


@lru_cache(maxsize=1)
def envelope_validator() -> Draft202012Validator:
    """エンベロープスキーマの validator を返す（プロセス内でキャッシュする）。

    Raises:
        SchemaValidationError: スキーマファイルが無い、または壊れている場合。
    """
    if not _ENVELOPE_SCHEMA_PATH.exists():
        raise SchemaValidationError(
            f"{_ENVELOPE_SCHEMA_PATH} is missing; run `make gen` to generate it from ontology/"
        )
    schema: dict[str, Any] = json.loads(_ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_event(event: EventEnvelope) -> bytes:
    """イベントを JSON Schema で検証し、送信用のバイト列を返す。

    Args:
        event: 送信するイベント。

    Returns:
        UTF-8 の JSON バイト列。

    Raises:
        SchemaValidationError: 生成済みスキーマに適合しない場合。pydantic を通っていても
            スキーマと乖離していれば送信しない（契約の正は ``schemas/`` 側）。
    """
    payload = event.model_dump_json().encode("utf-8")
    try:
        envelope_validator().validate(json.loads(payload))
    except JsonSchemaValidationError as exc:
        raise SchemaValidationError(
            f"event {event.event_id} does not conform to event-envelope.schema.json: "
            f"{exc.message} (at {'/'.join(str(p) for p in exc.absolute_path)})"
        ) from exc
    return payload
