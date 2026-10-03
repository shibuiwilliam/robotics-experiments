"""`Provider` 抽象と呼び出し記録（LLM 利用ポリシー、M6-01）。

**入口はここだけ。** 他のモジュールから各社 SDK を直接呼ばない（import-linter が機械検査して
いる）。理由は 3 つある。

1. **リトライ・タイムアウト・トークン計上を一元化する。** 各所で SDK を呼ぶと、再試行の方針が
   ばらつき、どこで何トークン使ったかが分からなくなる。
2. **replay を強制できる。** テストで実 LLM を使わないという方針（CLAUDE.md）は、入口が
   1 つでなければ守れない。
3. **来歴を欠かせない。** 出力には ``provider / model / prompt_version(hash)`` を必ず残す
   （LLM 利用ポリシー）。入口が分散するとここが抜ける。

**``task`` を必須の引数にしている。** どの用途で呼んだかが分からないと、``routing.yaml`` による
プロバイダ選択も、fixture の格納先も、トークン使用量の内訳も決まらない。「汎用の LLM 呼び出し」
を作らないための制約である。

**ホットパスから呼ばない**（不変条件 11）。信念更新・照合・事象昇格から LLM を呼ぶ経路は
import-linter の契約で禁止している。この層は「概念提案、文書からの構造化抽出、説明の文章化、
NL→クエリ変換」のためにある。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sbp.core.errors import LlmError, StructuredOutputError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "LlmTask",
    "Message",
    "Provider",
    "ProviderResponse",
    "Role",
    "TokenUsage",
    "request_digest",
]

log = get_logger(__name__)


class Role(StrEnum):
    """メッセージの役割。"""

    SYSTEM = "system"
    """役割・制約の指示。"""
    USER = "user"
    """入力。"""
    ASSISTANT = "assistant"
    """過去の応答（少数例の提示に使う）。"""


class LlmTask(StrEnum):
    """LLM の用途（不変条件 11 が許す 4 つに限る）。

    列挙にしているのは「汎用の LLM 呼び出し」を作れないようにするためである。新しい用途が
    必要になったら、ここに足す前に**それがホットパスでないこと**を確認する。
    """

    CONCEPT_PROPOSAL = "concept_proposal"
    """概念発見ループの候補提案（設計書 §8.6）。人の採否を待つ。"""
    DOCUMENT_EXTRACTION = "document_extraction"
    """非構造文書からの構造化抽出（設計書 §7.5）。根拠スパン必須。"""
    EXPLANATION_NARRATIVE = "explanation_narrative"
    """説明グラフの文章化（設計書 §8.8）。説明の内容は変えない。"""
    QUERY_TRANSLATION = "query_translation"
    """自然言語 → GraphQL / SPARQL 変換（設計書 §10.1）。"""


@dataclass(frozen=True, slots=True)
class Message:
    """1 メッセージ。

    Attributes:
        role: 役割。
        content: 本文。
    """

    role: Role
    content: str

    def __post_init__(self) -> None:
        """本文が空でないことを検証する。"""
        if not self.content.strip():
            raise LlmError(f"a {self.role.value} message must have content")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """トークン使用量（structlog に出す。LLM 利用ポリシー）。

    Attributes:
        prompt: 入力トークン。
        completion: 出力トークン。
    """

    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        """合計。"""
        return self.prompt + self.completion


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """構造化出力 1 回分の結果。

    Attributes:
        content: 検証前の生の出力（dict）。
        provider: プロバイダ名。
        model: モデル ID。
        prompt_version: プロンプトの版。
        prompt_hash: プロンプト本文のハッシュ（来歴に残す）。
        usage: トークン使用量。
        task: 用途。
        from_fixture: fixture から再生したか。
    """

    content: dict[str, Any]
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    usage: TokenUsage
    task: LlmTask
    from_fixture: bool = False

    def provenance(self) -> dict[str, str]:
        """来歴に入れる組（LLM 利用ポリシーが必須とする 3 つ）。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": f"{self.prompt_version}@{self.prompt_hash}",
        }

    def validated(self, model_type: type[Any]) -> Any:
        """pydantic モデルで検証して返す。

        **検証を通さずに使える経路を作らない。** LLM の出力は構造が崩れうるもので、崩れた
        まま下流に流すと「推定」としても扱えない（設計書 §7.5 の順序: pydantic → SHACL）。

        Args:
            model_type: 検証に使う pydantic モデル。

        Raises:
            StructuredOutputError: 検証に失敗した場合。
        """
        try:
            return model_type.model_validate(self.content)
        except Exception as exc:
            raise StructuredOutputError(
                f"{self.provider}/{self.model} returned output that does not match "
                f"{model_type.__name__} for task {self.task.value}: {exc}"
            ) from exc


@runtime_checkable
class Provider(Protocol):
    """LLM プロバイダの契約（LLM 利用ポリシー）。"""

    @property
    def name(self) -> str:
        """プロバイダ名（``anthropic`` / ``openai`` / ``gemini`` / ``replay``）。"""
        ...

    def complete_structured(
        self,
        messages: Sequence[Message],
        *,
        schema: dict[str, Any],
        task: LlmTask,
        prompt_version: str,
    ) -> ProviderResponse:
        """構造化出力を得る。

        Args:
            messages: メッセージ列。
            schema: 期待する JSON Schema。
            task: 用途。
            prompt_version: プロンプトの版。
        """
        ...

    def embed(self, texts: Sequence[str], *, task: LlmTask) -> list[list[float]]:
        """埋め込みを得る。

        Args:
            texts: 埋め込む文字列。
            task: 用途。
        """
        ...


def request_digest(
    messages: Sequence[Message], *, schema: dict[str, Any], prompt_version: str
) -> str:
    """要求の内容ハッシュ（fixture の鍵）。

    **メッセージ・スキーマ・プロンプト版のすべてを含める。** どれかを外すと、入力が変わったのに
    同じ fixture が再生され、「変更したのにテストが通る」状態になる。

    Args:
        messages: メッセージ列。
        schema: JSON Schema。
        prompt_version: プロンプトの版。

    Returns:
        16 桁の 16 進ハッシュ。
    """
    payload = json.dumps(
        {
            "messages": [[message.role.value, message.content] for message in messages],
            "schema": schema,
            "prompt_version": prompt_version,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class UsageLedger:
    """トークン使用量の累計（用途別）。

    用途別に分けるのが要点である。合計だけでは「説明の文章化が高い」のか「文書抽出が多い」のか
    が分からず、コストを下げる手が打てない。

    Attributes:
        by_task: 用途 → 累計使用量。
    """

    by_task: dict[LlmTask, TokenUsage] = field(default_factory=dict)

    def record(self, response: ProviderResponse) -> None:
        """1 回分を加える。

        Args:
            response: 応答。
        """
        current = self.by_task.get(response.task, TokenUsage())
        self.by_task[response.task] = TokenUsage(
            prompt=current.prompt + response.usage.prompt,
            completion=current.completion + response.usage.completion,
        )
        log.info(
            "llm.usage",
            task=response.task.value,
            provider=response.provider,
            model=response.model,
            prompt_tokens=response.usage.prompt,
            completion_tokens=response.usage.completion,
            from_fixture=response.from_fixture,
        )

    @property
    def total(self) -> int:
        """全用途の合計トークン。"""
        return sum(usage.total for usage in self.by_task.values())
