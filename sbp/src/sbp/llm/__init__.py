"""LLM 層。Provider 抽象、構造化出力、プロンプト版管理、replay。LLM 利用ポリシー。

責務: 各社 SDK の唯一の入口、構造化出力の検証、replay / record / live の切り替え、
プロンプトの版管理、トークン使用量の計上。
非責務: 信念更新・照合・事象昇格（**ホットパスから呼ばない**。不変条件 11）。

用途は 4 つに限る（:class:`~sbp.llm.base.LlmTask`）: 概念提案、文書からの構造化抽出、説明の
文章化、NL→クエリ変換。「汎用の LLM 呼び出し」を作らないための制約である。

既定は ``replay``。fixture が無ければ**失敗させる**（黙って live に落ちない）。
"""

from __future__ import annotations

from sbp.llm.base import (
    LlmTask,
    Message,
    Provider,
    ProviderResponse,
    Role,
    TokenUsage,
    UsageLedger,
    request_digest,
)
from sbp.llm.factory import build_provider
from sbp.llm.registry import PromptRegistry, PromptTemplate, TaskRouting, load_routing
from sbp.llm.replay import LlmMode, ReplayProvider, fixture_path, resolve_provider

__all__ = [
    "LlmMode",
    "LlmTask",
    "Message",
    "PromptRegistry",
    "PromptTemplate",
    "Provider",
    "ProviderResponse",
    "ReplayProvider",
    "Role",
    "TaskRouting",
    "TokenUsage",
    "UsageLedger",
    "build_provider",
    "fixture_path",
    "load_routing",
    "request_digest",
    "resolve_provider",
]
