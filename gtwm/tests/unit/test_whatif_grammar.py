"""WHAT-IF 文法（poc_plan.md 付録C）の受理/拒否テスト。"""

from __future__ import annotations

import pytest

from gtwm.kg.whatif.parser import Intervention, Offset, WhatIfSyntaxError, parse_whatif

pytestmark = pytest.mark.unit

VALID_QUERIES = [
    "PREDICT ?x AT +5s",
    "PREDICT ?x AT +1h",
    "PREDICT ?x AT +1min",
    "PREDICT ?queue_len, ?cycle_time AT +10min, +30min",
    "PREDICT ?x AT +1h WHERE a = gt:B",
    "PREDICT ?x AT +1h WHERE a = gt:B AND b = gt:C",
    "PREDICT ?x AT +1h GIVEN do(gt:A.p := 3)",
    "PREDICT ?x AT +1h GIVEN do(gt:A.p := absent)",
    "PREDICT ?x AT +1h GIVEN do(gt:A.p := 1.2 * current)",
    "PREDICT ?x AT +1h GIVEN do(gt:A.p := current + 5)",
    "PREDICT ?x AT +1h GIVEN do(gt:A.p := current - 5)",
    "PREDICT ?x AT +1h GIVEN do(gt:A.p := 3), do(gt:B.q := absent)",
    "PREDICT ?x AT +1h SAMPLES 10",
    "PREDICT ?x AT +1h INTERVAL 0.9",
    "PREDICT ?x AT +1h MODEL v1",
    "PREDICT ?x AT +1h SAMPLES 10 INTERVAL 0.9 MODEL v1",
    "PREDICT ?queue_len, ?cycle_time\nAT +10min, +30min\nWHERE station = gt:Station_S3\n"
    "GIVEN do(gt:Conveyor_C1.speed := 1.2 * current)\nSAMPLES 200\nINTERVAL 0.9",
    "PREDICT ?x AT +1h WHERE a = gt:B GIVEN do(gt:A.p := 1)",
    "PREDICT ?x AT +0.5h",
    "PREDICT ?x AT +100s SAMPLES 1",
]

INVALID_QUERIES = [
    "PREDICT ?x",
    "x AT +1h",
    "PREDICT x AT +1h",
    "PREDICT ?x AT 1h",
    "PREDICT ?x AT +1",
    "PREDICT ?x AT +1h GIVEN do(A.p := 3)",
    "PREDICT ?x AT +1h GIVEN do(gt:A.p = 3)",
    "PREDICT ?x AT +1h SAMPLES",
    "PREDICT ?x AT +1h INTERVAL",
    "WHERE a = gt:B",
    "PREDICT AT +1h",
    "PREDICT ?x AT +1h WHERE",
    "",
]


@pytest.mark.parametrize("query", VALID_QUERIES)
def test_valid_queries_parse(query: str) -> None:
    result = parse_whatif(query)
    assert result.vars
    assert result.horizons


@pytest.mark.parametrize("query", INVALID_QUERIES)
def test_invalid_queries_rejected(query: str) -> None:
    with pytest.raises(WhatIfSyntaxError):
        parse_whatif(query)


def test_canonical_example_structure() -> None:
    query = parse_whatif(
        "PREDICT ?queue_len, ?cycle_time\n"
        "AT +10min, +30min\n"
        "WHERE station = gt:Station_S3\n"
        "GIVEN do(gt:Conveyor_C1.speed := 1.2 * current)\n"
        "SAMPLES 200\n"
        "INTERVAL 0.9"
    )
    assert query.vars == ["queue_len", "cycle_time"]
    assert query.horizons == [Offset(10.0, "min"), Offset(30.0, "min")]
    assert query.filters[0].key == "station"
    assert query.filters[0].value == "gt:Station_S3"
    assert query.interventions == [
        Intervention(entity="gt:Conveyor_C1", property="speed", kind="mul_current", value=1.2)
    ]
    assert query.samples == 200
    assert query.interval == 0.9


def test_offset_seconds_conversion() -> None:
    assert Offset(10, "min").seconds == 600.0
    assert Offset(2, "h").seconds == 7200.0
    assert Offset(5, "s").seconds == 5.0
