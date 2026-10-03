"""契約テスト: 生成物（``schemas/*.json``, ``vocab.py``）と pydantic モデルの一致。

CLAUDE.md テスト方針の「契約テスト」に対応する。生成物とコードが乖離したら落ちる。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from sbp.core import vocab
from sbp.core.assertion import SCHEMA_VERSION
from sbp.core.belief import BooleanBelief, CategoricalBelief, NumericBelief
from sbp.core.events import EventEnvelope, ProbeEvidence, Producer, RecordEvidence, ValidTime
from sbp.core.modality import Modality

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"
ENVELOPE_SCHEMA_PATH = SCHEMAS_DIR / "event-envelope.schema.json"

T0 = datetime(2026, 9, 12, 9, 41, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """生成済みのエンベロープスキーマ。"""
    return json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def envelope_validator(envelope_schema: dict[str, Any]) -> Draft202012Validator:
    """スキーマ自体が妥当であることを確認したうえで validator を返す。"""
    Draft202012Validator.check_schema(envelope_schema)
    return Draft202012Validator(envelope_schema)


def _event(**overrides: Any) -> EventEnvelope:
    """設計書 §5.8 の TypedProbabilisticEvent に相当するイベント。"""
    defaults: dict[str, Any] = {
        "type": vocab.ent.StopCmd,
        "subject": "https://sbp.example/site/siteA/asset/P-101",
        "valid_time": ValidTime(start=T0, end=T0 + timedelta(seconds=40), uncertainty_s=20.0),
        "tx_time": T0 + timedelta(seconds=42),
        "modality": Modality.INFERRED,
        "belief": BooleanBelief(p=0.91),
        "evidence": (
            ProbeEvidence(
                probe_id="https://sbp.example/site/siteA/probe/running-v7",
                wm_version="https://sbp.example/site/siteA/wm/pump-koopman-3.2",
                raw_score=0.88,
            ),
        ),
        "producer": Producer(agent="bridge@siteA", version="2.4.1"),
        "ontology_version": vocab.ONTOLOGY_VERSION,
    }
    return EventEnvelope(**(defaults | overrides))


class TestEnvelopeRoundTrip:
    def test_pydantic_output_validates_against_generated_schema(
        self, envelope_validator: Draft202012Validator
    ) -> None:
        """pydantic が出した JSON が生成スキーマに適合する。"""
        payload = json.loads(_event().model_dump_json())
        envelope_validator.validate(payload)

    def test_round_trip_preserves_values(self) -> None:
        """JSON を経由しても値が保存される。"""
        original = _event()
        restored = EventEnvelope.model_validate_json(original.model_dump_json())
        assert restored == original

    @pytest.mark.parametrize(
        "belief",
        [
            BooleanBelief(p=0.5),
            CategoricalBelief(pmf={"ent:StopCmd": 0.35, "ent:Fault": 0.56, "other": 0.09}),
            NumericBelief(mean=2.5, var=0.1, quantiles={"0.05": 2.0, "0.95": 3.0}),
        ],
    )
    def test_all_belief_kinds_validate(
        self, belief: Any, envelope_validator: Draft202012Validator
    ) -> None:
        """3 種の信念すべてがスキーマの oneOf を満たす。"""
        payload = json.loads(_event(belief=belief).model_dump_json())
        envelope_validator.validate(payload)

    @pytest.mark.parametrize(
        "evidence",
        [
            (ProbeEvidence(probe_id="https://sbp.example/site/siteA/probe/p1"),),
            (
                RecordEvidence(
                    record="https://sbp.example/site/siteA/record/WO-123",
                    source=vocab.ent.CmmsSource,
                ),
            ),
        ],
    )
    def test_all_evidence_kinds_validate(
        self, evidence: Any, envelope_validator: Draft202012Validator
    ) -> None:
        payload = json.loads(_event(evidence=evidence).model_dump_json())
        envelope_validator.validate(payload)

    def test_schema_rejects_missing_required_field(
        self, envelope_validator: Draft202012Validator
    ) -> None:
        """スキーマが必須フィールドの欠落を検出する（検査が機能していることの確認）。"""
        payload = json.loads(_event().model_dump_json())
        del payload["ontology_version"]
        assert not envelope_validator.is_valid(payload)

    def test_schema_rejects_unknown_modality(
        self, envelope_validator: Draft202012Validator
    ) -> None:
        """不変条件 3: 様相は 6 値のみ。"""
        payload = json.loads(_event().model_dump_json())
        payload["modality"] = "believed"
        assert not envelope_validator.is_valid(payload)

    def test_schema_rejects_uncalibrated_belief(
        self, envelope_validator: Draft202012Validator
    ) -> None:
        """不変条件 7: 較正済みでない信念は通らない。"""
        payload = json.loads(_event().model_dump_json())
        payload["belief"]["calibrated"] = False
        assert not envelope_validator.is_valid(payload)

    def test_schema_requires_uncertainty_in_valid_time(
        self, envelope_validator: Draft202012Validator
    ) -> None:
        """点時刻のイベントを出さない（``uncertainty_s`` 必須）。"""
        payload = json.loads(_event().model_dump_json())
        del payload["valid_time"]["uncertainty_s"]
        assert not envelope_validator.is_valid(payload)

    def test_envelope_schema_version_matches_code(self, envelope_schema: dict[str, Any]) -> None:
        """スキーマが要求する ``schema_version`` のパターンにコードの版が適合する。"""
        pattern = envelope_schema["properties"]["schema_version"]["pattern"]
        assert re.match(pattern, SCHEMA_VERSION)
