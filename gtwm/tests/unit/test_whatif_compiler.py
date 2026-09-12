"""WHAT-IF コンパイラの個体解決テスト。"""

from __future__ import annotations

import pytest

from gtwm.kg.whatif.compiler import WhatIfCompileError, compile_query
from gtwm.kg.whatif.parser import parse_whatif

pytestmark = pytest.mark.unit


def test_compile_resolves_known_entities() -> None:
    query = parse_whatif(
        "PREDICT ?queue_len AT +1min WHERE a = gt:Zone_Pick "
        "GIVEN do(gt:Equipment_Conveyor_0001.speed := 1.2 * current)"
    )
    compiled = compile_query(query)
    assert compiled.filters[0].zone_index >= 0
    assert compiled.filters[0].zone_gt_id == "gt:Zone_Pick"
    assert compiled.interventions[0].entity_gt_id == "gt:Equipment_Conveyor_0001"
    assert compiled.interventions[0].action_value == pytest.approx(1.2)


def test_compile_absent_intervention_zeroes_action_value() -> None:
    query = parse_whatif(
        "PREDICT ?queue_len AT +1min GIVEN do(gt:Equipment_Conveyor_0001.p := absent)"
    )
    compiled = compile_query(query)
    assert compiled.interventions[0].action_value == 0.0


def test_compile_unknown_entity_raises_with_alternative_suggestion() -> None:
    query = parse_whatif(
        "PREDICT ?queue_len AT +1min GIVEN do(gt:Conveyor_C1.speed := 1.2 * current)"
    )
    with pytest.raises(WhatIfCompileError, match="registry.yaml"):
        compile_query(query)


def test_compile_unknown_zone_raises() -> None:
    query = parse_whatif("PREDICT ?queue_len AT +1min WHERE a = gt:Station_S3")
    with pytest.raises(WhatIfCompileError, match="ゾーン"):
        compile_query(query)


def test_compile_non_zone_filter_value_raises() -> None:
    query = parse_whatif("PREDICT ?queue_len AT +1min WHERE a = gt:Pallet_0001")
    with pytest.raises(WhatIfCompileError):
        compile_query(query)
