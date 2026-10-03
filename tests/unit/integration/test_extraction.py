"""文書抽出の単体テスト（設計書 §7.5、M6-06）。

重点:

1. **根拠スパン無しの項目を作れない** — 後から辿れない抽出は監査に使えない。
2. **スパンを文書と照合する** — LLM のオフセットは幻覚しうる。
3. **抽出は inferred** — 文書は誰かの主張であって観測ではない（不変条件 3）。
4. **原文に無いことを補わない** — 値が空の項目は構築時に拒否する。
"""

from __future__ import annotations

import pytest

from sbp.core.errors import IntegrationError
from sbp.core.modality import Modality
from sbp.integration import (
    EvidenceSpan,
    ExtractedField,
    ExtractionResult,
    NormExtraction,
    verify_spans,
)
from sbp.integration.extraction import accepted_fields, to_assertion_payload

DOC = "https://sbp.example/doc/maintenance-standard"
TEXT = "4.2 点検は保全完了後 24 時間以内に実施すること。"
PROVENANCE = {"provider": "replay", "model": "fixture", "prompt_version": "1.0.0@abcd"}


def _span(start: int = 11, end: int = 21) -> EvidenceSpan:
    return EvidenceSpan(document=DOC, start=start, end=end, quote=TEXT[start:end])


def _result(**overrides: object) -> ExtractionResult:
    values: dict[str, object] = {
        "document": DOC,
        "fields": (ExtractedField(name="deadline", value="PT24H", evidence=_span()),),
        "llm_provenance": PROVENANCE,
    }
    values.update(overrides)
    return ExtractionResult(**values)  # type: ignore[arg-type]


class TestEvidenceSpan:
    def test_a_valid_span_matches_the_document(self) -> None:
        assert _span().matches(TEXT)

    def test_an_empty_span_is_rejected(self) -> None:
        """区間は右開（不変条件 8 と同じ規約）。"""
        with pytest.raises(IntegrationError, match="is empty"):
            EvidenceSpan(document=DOC, start=5, end=5, quote="x")

    def test_a_span_whose_length_disagrees_with_the_quote_is_rejected(self) -> None:
        """長さが合わないスパンは文書と照合できない。"""
        with pytest.raises(IntegrationError, match="does not match its quote"):
            EvidenceSpan(document=DOC, start=0, end=5, quote="長すぎる引用文字列")

    def test_a_wrong_offset_fails_the_match(self) -> None:
        span = EvidenceSpan(document=DOC, start=0, end=3, quote="XYZ")
        assert not span.matches(TEXT)


class TestExtractedField:
    def test_evidence_is_mandatory(self) -> None:
        """「後で付ける」経路を作ると、根拠の無い項目が混ざる。"""
        with pytest.raises(Exception, match="evidence"):
            ExtractedField(name="deadline", value="PT24H")  # type: ignore[call-arg]

    def test_an_empty_value_is_rejected(self) -> None:
        """原文に無い項目は省略する（空で記録しない）。"""
        with pytest.raises(IntegrationError, match="has evidence but no value"):
            ExtractedField(name="deadline", value="", evidence=_span())

    def test_a_unit_can_be_carried(self) -> None:
        assert ExtractedField(name="t", value=24, evidence=_span(), unit="h").unit == "h"


class TestExtractionResult:
    def test_the_modality_is_inferred(self) -> None:
        """文書は誰かの主張であって観測ではない（不変条件 3）。"""
        assert _result().modality is Modality.INFERRED

    def test_observed_is_refused(self) -> None:
        with pytest.raises(IntegrationError, match="must be inferred"):
            _result(modality=Modality.OBSERVED)

    def test_incomplete_provenance_is_refused(self) -> None:
        """LLM 利用ポリシー: provider / model / prompt_version を残す。"""
        with pytest.raises(IntegrationError, match="missing"):
            _result(llm_provenance={"provider": "p"})

    def test_a_field_citing_another_document_is_refused(self) -> None:
        other = ExtractedField(
            name="x",
            value="y",
            evidence=EvidenceSpan(
                document="https://sbp.example/doc/other", start=0, end=1, quote="4"
            ),
        )
        with pytest.raises(IntegrationError, match="cites document"):
            _result(fields=(other,))

    def test_an_empty_extraction_is_refused(self) -> None:
        with pytest.raises(Exception, match="fields"):
            _result(fields=())

    def test_an_unspecified_field_is_reported_as_such(self) -> None:
        """省略したことが残らないと、「書いていなかった」のか「見落とした」のか分からない。"""
        result = _result(unspecified=("severity",))
        with pytest.raises(IntegrationError, match="listed as unspecified"):
            result.field("severity")

    def test_values_are_available_for_downstream_models(self) -> None:
        assert _result().as_values() == {"deadline": "PT24H"}


class TestSpanVerification:
    def test_a_matching_span_passes(self) -> None:
        assert verify_spans(_result(), text=TEXT) == ()

    def test_a_hallucinated_span_is_caught(self) -> None:
        """照合せずに登録すると、「根拠がある」と記録された項目の根拠がどこも指していない。"""
        bogus = ExtractedField(
            name="ghost",
            value="y",
            evidence=EvidenceSpan(document=DOC, start=0, end=2, quote="ZZ"),
        )
        assert verify_spans(_result(fields=(bogus,)), text=TEXT) == ("ghost",)

    def test_an_empty_document_is_refused(self) -> None:
        with pytest.raises(IntegrationError, match="empty document body"):
            verify_spans(_result(), text="")

    def test_only_verified_fields_are_accepted(self) -> None:
        good = ExtractedField(name="deadline", value="PT24H", evidence=_span())
        bogus = ExtractedField(
            name="ghost",
            value="y",
            evidence=EvidenceSpan(document=DOC, start=0, end=2, quote="ZZ"),
        )
        accepted = accepted_fields(_result(fields=(good, bogus)), text=TEXT)
        assert [field.name for field in accepted] == ["deadline"]


class TestAssertionPayload:
    def test_the_payload_carries_provenance_and_evidence(self) -> None:
        payload = to_assertion_payload(
            _result(), subject="https://sbp.example/ent#Pump", predicate="p", text=TEXT
        )
        assert payload is not None
        assert payload["modality"] is Modality.INFERRED
        assert "provider=replay" in payload["provenance"]
        assert payload["evidence"][0].endswith("#chars=11-21")

    def test_an_entirely_rejected_extraction_yields_nothing(self) -> None:
        bogus = ExtractedField(
            name="ghost",
            value="y",
            evidence=EvidenceSpan(document=DOC, start=0, end=2, quote="ZZ"),
        )
        assert (
            to_assertion_payload(_result(fields=(bogus,)), subject="s", predicate="p", text=TEXT)
            is None
        )


class TestNormExtraction:
    def test_a_valid_extraction_passes(self) -> None:
        norm = NormExtraction(
            rule_ref="inspection_before_restart",
            source_clause="4.2",
            severity="important",
            applies_to="Pump",
            deadline="PT24H",
        )
        assert norm.deadline == "PT24H"

    def test_an_unknown_severity_is_refused(self) -> None:
        with pytest.raises(IntegrationError, match="unknown severity"):
            NormExtraction(rule_ref="r", source_clause="1", severity="urgent", applies_to="Pump")

    def test_a_qualitative_deadline_stays_qualitative(self) -> None:
        """「速やかに」を数値に変換しない。"""
        norm = NormExtraction(
            rule_ref="r",
            source_clause="1",
            severity="advisory",
            applies_to="Pump",
            unspecified_urgency=True,
        )
        assert norm.deadline is None

    def test_both_a_deadline_and_unspecified_urgency_is_refused(self) -> None:
        with pytest.raises(IntegrationError, match="cannot have both"):
            NormExtraction(
                rule_ref="r",
                source_clause="1",
                severity="advisory",
                applies_to="Pump",
                deadline="PT24H",
                unspecified_urgency=True,
            )
