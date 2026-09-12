"""WHAT-IF クエリの構文解析（`grammar.lark` を lark で解析し、`WhatIfQuery` に変換する）。

poc_plan.md 付録C の文法を正とする。文法自体を変更する場合は付録C を先に更新すること
（ontology.md「EPCIS と WHAT-IF」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from lark import Lark, Token, Transformer, UnexpectedInput

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"

_UNIT_SECONDS = {"s": 1.0, "min": 60.0, "h": 3600.0}

InterventionKind = Literal["absent", "literal", "mul_current", "add_current", "sub_current"]


class WhatIfSyntaxError(ValueError):
    """クエリ文字列が付録Cの文法に適合しない場合。"""


@dataclass
class Offset:
    value: float
    unit: str

    @property
    def seconds(self) -> float:
        return self.value * _UNIT_SECONDS[self.unit]

    def __str__(self) -> str:  # pragma: no cover - デバッグ用
        return f"+{self.value:g}{self.unit}" if self.value >= 0 else f"{self.value:g}{self.unit}"


@dataclass
class Triple:
    key: str
    value: str


@dataclass
class Intervention:
    entity: str
    property: str
    kind: InterventionKind
    value: float | None = None


@dataclass
class WhatIfQuery:
    vars: list[str]
    horizons: list[Offset]
    filters: list[Triple] = field(default_factory=list)
    interventions: list[Intervention] = field(default_factory=list)
    samples: int = 50
    interval: float | None = None
    model_version: str | None = None


class _QueryTransformer(Transformer[Token, WhatIfQuery]):
    def start(self, items: list) -> WhatIfQuery:
        return items[0]

    def query(self, items: list) -> WhatIfQuery:
        vars_, horizons = items[0], items[1]
        where_ = None
        given_ = None
        options: dict = {}
        for item in items[2:]:
            if item is None:
                continue
            if isinstance(item, list) and item and isinstance(item[0], Triple):
                where_ = item
            elif isinstance(item, list) and item and isinstance(item[0], Intervention):
                given_ = item
            elif isinstance(item, dict):
                options = item
        return WhatIfQuery(
            vars=vars_,
            horizons=horizons,
            filters=where_ or [],
            interventions=given_ or [],
            samples=options.get("samples", 50),
            interval=options.get("interval"),
            model_version=options.get("model_version"),
        )

    def vars(self, items: list[Token]) -> list[str]:
        return [str(tok)[1:] for tok in items]  # "?queue_len" -> "queue_len"

    def horizons(self, items: list[Offset]) -> list[Offset]:
        return items

    def offset(self, items: list[Token]) -> Offset:
        text = str(items[0])
        for unit in ("min", "h", "s"):
            if text.endswith(unit):
                return Offset(value=float(text[: -len(unit)]), unit=unit)
        raise WhatIfSyntaxError(f"未知のホライズン単位: {text}")

    def where_clause(self, items: list) -> list[Triple]:
        return items[0]

    def filter(self, items: list[Triple]) -> list[Triple]:
        return items

    def triple(self, items: list[Token]) -> Triple:
        return Triple(key=str(items[0]), value=str(items[1]))

    def given_clause(self, items: list) -> list[Intervention]:
        return items[0]

    def interventions(self, items: list[Intervention]) -> list[Intervention]:
        return items

    def do_stmt(self, items: list) -> Intervention:
        entity = items[0]
        prop = str(items[1])
        rhs = items[2]
        if isinstance(rhs, Token) and str(rhs) == "absent":
            return Intervention(entity=entity, property=prop, kind="absent")
        kind, value = rhs
        return Intervention(entity=entity, property=prop, kind=kind, value=value)

    def entity(self, items: list[Token]) -> str:
        return str(items[0])

    def property(self, items: list[Token]) -> str:
        return str(items[0])

    def mul_current(self, items: list[Token]) -> tuple[str, float]:
        return ("mul_current", float(items[0]))

    def add_current(self, items: list[Token]) -> tuple[str, float]:
        return ("add_current", float(items[0]))

    def sub_current(self, items: list[Token]) -> tuple[str, float]:
        return ("sub_current", float(items[0]))

    def literal_number(self, items: list[Token]) -> tuple[str, float]:
        return ("literal", float(items[0]))

    def options(self, items: list) -> dict:
        result: dict = {}
        for item in items:
            if item is None:
                continue
            result.update(item)
        return result

    def samples_opt(self, items: list[Token]) -> dict:
        return {"samples": int(items[0])}

    def interval_opt(self, items: list[Token]) -> dict:
        return {"interval": float(items[0])}

    def model_opt(self, items: list[Token]) -> dict:
        return {"model_version": str(items[0])}


_parser = Lark(_GRAMMAR_PATH.read_text(encoding="utf-8"), parser="lalr", maybe_placeholders=False)
_transformer = _QueryTransformer()


def parse_whatif(text: str) -> WhatIfQuery:
    """クエリ文字列を `WhatIfQuery` に変換する。文法違反は `WhatIfSyntaxError` を送出する。"""
    try:
        tree = _parser.parse(text)
    except UnexpectedInput as exc:
        raise WhatIfSyntaxError(f"WHAT-IF クエリの構文エラー: {exc}") from exc
    return _transformer.transform(tree)


__all__ = [
    "WhatIfQuery",
    "Offset",
    "Triple",
    "Intervention",
    "InterventionKind",
    "WhatIfSyntaxError",
    "parse_whatif",
]
