"""非構造文書からの構造化抽出（設計書 §7.5、M6-06）。

> オントロジーガイド付き構造化抽出（根拠スパン必須、pydantic → SHACL 検証、``inferred`` 登録）

**根拠スパンを必須にするのが要点である**（LLM 利用ポリシー）。抽出結果は規程や手順書に基づく
主張であり、後から「どこに書いてあったか」を辿れなければ監査に使えない。スパン無しの項目は
**構築時に拒否する**——「後で付ける」経路を作ると、根拠の無い項目が混ざる。

**抽出は ``observed`` にならない**（不変条件 3）。文書は誰かの主張であって観測ではない。LLM が
読み取った構造は ``inferred`` で、来歴に ``provider / model / prompt_version(hash)`` を残す。

**検証の順序は pydantic → SHACL**（設計書 §7.5）。pydantic が形を、SHACL が意味（オントロジーの
形状制約）を見る。順序が逆だと、形が崩れた入力を SHACL に渡して読みにくいエラーになる。

**原文に無いことを補わない。** 値が書かれていない項目は省略する（``None``）。LLM が「普通はこう
だろう」で埋めた値は、根拠スパンが付けられないので :class:`ExtractedField` を構築できない
——型でそれを防いでいる。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import IntegrationError
from sbp.core.logging import get_logger
from sbp.core.modality import Modality

if TYPE_CHECKING:
    pass

__all__ = [
    "EvidenceSpan",
    "ExtractedField",
    "ExtractionResult",
    "NormExtraction",
    "verify_spans",
]

log = get_logger(__name__)


class EvidenceSpan(BaseModel):
    """文書内の根拠位置（LLM 利用ポリシーが必須とする）。

    Attributes:
        document: 文書の IRI。
        start: 開始文字オフセット（0 始まり）。
        end: 終了文字オフセット（右開）。
        quote: 引用文字列。``document`` の ``[start, end)`` と一致すること。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _span_is_right_open_and_matches_the_quote(self) -> Self:
        """区間が右開（不変条件 8 と同じ規約）で、長さが引用と合うことを検証する。"""
        if self.end <= self.start:
            raise IntegrationError(
                f"evidence span [{self.start}, {self.end}) in {self.document} is empty; spans are "
                "right-open like every other interval in SBP (invariant 8)"
            )
        if self.end - self.start != len(self.quote):
            raise IntegrationError(
                f"evidence span [{self.start}, {self.end}) is {self.end - self.start} characters "
                f"but the quote is {len(self.quote)}; a span that does not match its quote cannot "
                "be verified against the document"
            )
        return self

    def matches(self, text: str) -> bool:
        """文書本文と照合する。

        Args:
            text: 文書の本文。
        """
        return text[self.start : self.end] == self.quote


class ExtractedField(BaseModel):
    """抽出した 1 項目（値 + 根拠）。

    Attributes:
        name: 項目名。
        value: 抽出した値。
        evidence: 根拠スパン。**必須**。
        unit: 単位（数値のとき。原文に無ければ抽出しない）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    value: Any
    evidence: EvidenceSpan
    unit: str | None = None

    @model_validator(mode="after")
    def _a_value_needs_evidence(self) -> Self:
        """値が空でないことを検証する。"""
        if self.value is None or self.value == "":
            raise IntegrationError(
                f"field {self.name} has evidence but no value; omit the field instead of "
                "recording an empty one (the prompt says not to fill in what the document "
                "does not state)"
            )
        return self


class ExtractionResult(BaseModel):
    """抽出 1 回分の結果。

    Attributes:
        document: 文書の IRI。
        fields: 抽出した項目。
        llm_provenance: ``provider / model / prompt_version`` の組。
        modality: 様相。``inferred`` 固定（文書は観測ではない）。
        unspecified: 原文に書かれていなかった項目の名前（省略したことを残す）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str = Field(min_length=1)
    fields: tuple[ExtractedField, ...] = Field(min_length=1)
    llm_provenance: dict[str, str]
    modality: Modality = Modality.INFERRED
    unspecified: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _modality_and_provenance_are_sound(self) -> Self:
        """様相と来歴を検証する（不変条件 3、LLM 利用ポリシー）。"""
        if self.modality is not Modality.INFERRED:
            raise IntegrationError(
                f"document extraction must be inferred, not {self.modality.value}; a document is "
                "someone's claim, not an observation (invariant 3)"
            )
        missing = {"provider", "model", "prompt_version"} - set(self.llm_provenance)
        if missing:
            raise IntegrationError(
                f"extraction provenance is missing {sorted(missing)}; without it the output "
                "cannot be re-explained or attributed (LLM policy)"
            )
        for field in self.fields:
            if field.evidence.document != self.document:
                raise IntegrationError(
                    f"field {field.name} cites document {field.evidence.document} but this "
                    f"extraction is about {self.document}"
                )
        return self

    def field(self, name: str) -> ExtractedField:
        """項目名で引く。

        Args:
            name: 項目名。

        Raises:
            IntegrationError: その項目が無い場合（省略されたなら ``unspecified`` に入っている）。
        """
        for item in self.fields:
            if item.name == name:
                return item
        hint = " (listed as unspecified)" if name in self.unspecified else ""
        raise IntegrationError(f"extraction has no field {name!r}{hint}")

    def as_values(self) -> dict[str, Any]:
        """項目名 → 値（下流の pydantic モデルに渡す形）。"""
        return {item.name: item.value for item in self.fields}


class NormExtraction(BaseModel):
    """規程文書から抽出する構造（設計書 §7.4 の ``Norm`` に対応）。

    抽出の対象を型で固定するのが要点である。自由な dict を返すと、下流が何を期待してよいか
    分からず、SHACL 検証もできない。

    Attributes:
        rule_ref: 規則の参照名（``ontology/norms/*.lp`` の規則名に対応させる）。
        source_clause: 出典の節。
        severity: 重大度（``advisory`` / ``important`` / ``critical``）。
        applies_to: 適用対象の型（ローカル名）。
        deadline: 期限（ISO 8601 の期間。原文が「速やかに」なら ``None``）。
        unspecified_urgency: 期限が定性表現だったか。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_ref: str = Field(min_length=1)
    source_clause: str = Field(min_length=1)
    severity: str
    applies_to: str = Field(min_length=1)
    deadline: str | None = None
    unspecified_urgency: bool = False

    @model_validator(mode="after")
    def _severity_is_known_and_deadline_is_consistent(self) -> Self:
        """重大度と期限の整合を検証する。"""
        if self.severity not in {"advisory", "important", "critical"}:
            raise IntegrationError(
                f"unknown severity {self.severity!r}; the ontology defines advisory / important / "
                "critical (NormSeverity)"
            )
        if self.deadline is not None and self.unspecified_urgency:
            raise IntegrationError(
                "a norm cannot have both a concrete deadline and unspecified urgency; the prompt "
                "asks for one or the other so that 'speedily' is never turned into a number"
            )
        return self


def verify_spans(result: ExtractionResult, *, text: str) -> tuple[str, ...]:
    """根拠スパンを文書本文と照合する。

    **LLM の出力を信じない。** スパンのオフセットは幻覚しうる。照合して合わない項目を返し、
    呼び出し側が落とす。照合せずに登録すると、「根拠がある」と記録された項目の根拠が実際には
    どこも指していない状態になる——最も悪い種類の嘘である。

    Args:
        result: 抽出結果。
        text: 文書の本文。

    Returns:
        照合に失敗した項目の名前。

    Raises:
        IntegrationError: 本文が空の場合。
    """
    if not text:
        raise IntegrationError("cannot verify spans against an empty document body")
    failed = tuple(field.name for field in result.fields if not field.evidence.matches(text))
    if failed:
        log.info(
            "integration.extraction_spans_failed",
            document=result.document,
            failed=list(failed),
            total=len(result.fields),
        )
    return failed


def accepted_fields(result: ExtractionResult, *, text: str) -> tuple[ExtractedField, ...]:
    """照合に通った項目だけを返す。

    Args:
        result: 抽出結果。
        text: 文書の本文。
    """
    failed = set(verify_spans(result, text=text))
    return tuple(field for field in result.fields if field.name not in failed)


def to_assertion_payload(
    result: ExtractionResult,
    *,
    subject: str,
    predicate: str,
    text: str,
) -> dict[str, Any] | None:
    """照合済みの項目から、二時制アサーションに渡す素材を作る。

    アサーション自体はここで作らない（``integration`` は永続化の形を決めない）。返すのは
    ``knowledge`` の API に渡す値の組である。

    Args:
        result: 抽出結果。
        subject: 主語 IRI。
        predicate: 述語 IRI。
        text: 文書の本文（スパン照合に使う）。

    Returns:
        素材の dict。照合に通った項目が無ければ ``None``。
    """
    accepted = accepted_fields(result, text=text)
    if not accepted:
        log.info("integration.extraction_rejected_entirely", document=result.document)
        return None
    return {
        "subject": subject,
        "predicate": predicate,
        "values": {field.name: field.value for field in accepted},
        "modality": result.modality,
        "provenance": (
            f"extraction:{result.document}"
            f"?provider={result.llm_provenance['provider']}"
            f"&model={result.llm_provenance['model']}"
            f"&prompt={result.llm_provenance['prompt_version']}"
        ),
        "evidence": tuple(
            f"{field.evidence.document}#chars={field.evidence.start}-{field.evidence.end}"
            for field in accepted
        ),
    }
