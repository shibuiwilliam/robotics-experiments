"""隔離キューの純粋部分（M10-01）。壊れた入力が書き込みを壊さない・識別子が安定する。"""

from __future__ import annotations

import json

import pytest

from sbp.integration import describe_failure
from sbp.knowledge.quarantine import _jsonable, quarantine_id


def test_nul_and_non_finite_floats_become_postgres_safe() -> None:
    body = _jsonable({"a": "x\x00y", "b": float("nan"), "c": [float("inf"), 1.5], "d": b"raw"})
    text = json.dumps(body, allow_nan=False)
    assert "\\x00" in text and "\u0000" not in text
    assert body["b"] == "nan"
    assert body["c"] == ["inf", 1.5]
    assert isinstance(body["d"], str), "JSON にできない型は文字列にする"


def test_the_id_is_stable_for_the_same_input_and_reason() -> None:
    args = {"site": "s", "source": "src", "table": "t", "subject": "X"}
    assert quarantine_id(**args, reason="r") == quarantine_id(**args, reason="r")
    assert quarantine_id(**args, reason="r") != quarantine_id(**args, reason="r2")
    assert quarantine_id(**{**args, "site": "s2"}, reason="r") != quarantine_id(**args, reason="r")


class TestDescribeFailure:
    def test_pydantic_errors_do_not_copy_input_values_into_the_reason(self) -> None:
        from pydantic import BaseModel  # noqa: PLC0415

        class M(BaseModel):
            n: int

        with pytest.raises(Exception) as first:
            M.model_validate({"n": "secret-person-name"})
        with pytest.raises(Exception) as second:
            M.model_validate({"n": "another-value"})
        reason = describe_failure(first.value)
        assert "secret-person-name" not in reason
        assert reason == describe_failure(second.value), "値が違っても同じ種類の失敗は同じ文"

    def test_plain_exceptions_are_truncated(self) -> None:
        reason = describe_failure(ValueError("x" * 1000))
        assert len(reason) < 200
        assert "ValueError" in reason
