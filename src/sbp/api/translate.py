"""自然言語 → クエリ変換（設計書 §10.1、M6-06）。

**変換結果をそのまま実行しない。** LLM が出したクエリは「人が書いたクエリ」と同じ扱いにしない。
検査を通してから実行する:

1. **更新操作を含まないこと**（:func:`~sbp.api.app.is_read_only_sparql` と同じ検査）。
2. **時制・様相の指定があること**。``asOf`` / ``validAt`` / ``modality`` を省いたクエリは、
   「いつの、誰の主張か」を問わずに答えを返す。自然言語の質問には普通それが含まれないので、
   **変換側が明示的に補う**必要がある。
3. **件数の上限があること**。「全部見せて」が無制限のクエリになると、グラフ全体を引く。

**変換の意図を残す。** 元の自然言語、生成されたクエリ、補った既定値を一緒に返す。使う側が
「自分の質問がどう解釈されたか」を確認できなければ、間違った答えを正しいと思って受け取る。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.modality import Modality

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "QueryLanguage",
    "TranslatedQuery",
    "TranslationIssue",
    "review_translation",
]

log = get_logger(__name__)

_DEFAULT_LIMIT = 100
"""補う件数上限。「全部見せて」が無制限のクエリにならないようにする。"""

_UPDATE_KEYWORDS = re.compile(
    r"\b(INSERT|DELETE|CLEAR|DROP|CREATE|LOAD|MOVE|COPY|ADD|mutation)\b", re.IGNORECASE
)
"""更新操作（SPARQL Update と GraphQL mutation）。"""

_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
"""SPARQL の件数上限。"""


class QueryLanguage(StrEnum):
    """変換先の言語。"""

    SPARQL = "sparql"
    """分析・監査向け。"""
    GRAPHQL = "graphql"
    """アプリ向け。時制・様相はフィールド引数。"""


class TranslationIssue(StrEnum):
    """変換結果の問題。"""

    WRITES = "writes"
    """更新操作を含む。**実行してはならない。**"""
    NO_TEMPORAL_SCOPE = "no_temporal_scope"
    """時制の指定が無い（``asOf`` / ``validAt``）。"""
    NO_MODALITY = "no_modality"
    """様相の指定が無い。"""
    UNBOUNDED = "unbounded"
    """件数上限が無い。"""

    @property
    def blocks_execution(self) -> bool:
        """実行を止めるべき問題か。

        更新操作だけが絶対の拒否である。他の 3 つは**既定値を補って実行してよい**が、補った
        ことを利用者に見せる必要がある。
        """
        return self is TranslationIssue.WRITES


@dataclass(frozen=True, slots=True)
class TranslatedQuery:
    """変換結果とその来歴。

    Attributes:
        question: 元の自然言語。
        language: 変換先の言語。
        query: 生成されたクエリ。
        issues: 検査で見つかった問題。
        applied_defaults: 補った既定値（利用者に見せる）。
        llm_provenance: ``provider / model / prompt_version`` の組。
    """

    question: str
    language: QueryLanguage
    query: str
    issues: tuple[TranslationIssue, ...] = ()
    applied_defaults: dict[str, str] = field(default_factory=dict)
    llm_provenance: dict[str, str] = field(default_factory=dict)

    @property
    def executable(self) -> bool:
        """実行してよいか。"""
        return not any(issue.blocks_execution for issue in self.issues)

    def explain(self) -> str:
        """人が読む説明（「自分の質問がどう解釈されたか」）。"""
        parts = [f"質問「{self.question}」を {self.language.value} に変換した"]
        if self.applied_defaults:
            filled = ", ".join(
                f"{key}={value}" for key, value in sorted(self.applied_defaults.items())
            )
            parts.append(f"補った既定値: {filled}")
        if self.issues:
            parts.append("検査: " + ", ".join(issue.value for issue in self.issues))
        if not self.executable:
            parts.append("**実行しない**（更新操作を含む）")
        return " / ".join(parts)


def review_translation(
    question: str,
    query: str,
    *,
    language: QueryLanguage,
    llm_provenance: dict[str, str] | None = None,
    as_of: datetime | None = None,
    valid_at: datetime | None = None,
    modality: Modality = Modality.INFERRED,
    limit: int = _DEFAULT_LIMIT,
) -> TranslatedQuery:
    """変換結果を検査し、足りない指定を補う。

    **補うのは時制・様相・件数上限だけ**で、クエリの意味は変えない。意味を直し始めると「LLM の
    出力を人が書き直す」ことになり、変換の是非を評価できなくなる（直した後のものしか見ない）。

    Args:
        question: 元の自然言語。
        query: LLM が生成したクエリ。
        language: 変換先の言語。
        llm_provenance: LLM の来歴。
        as_of: 補う取引時刻。
        valid_at: 補う有効時刻。
        modality: 補う様相。
        limit: 補う件数上限。

    Returns:
        検査結果付きの変換。
    """
    issues: list[TranslationIssue] = []
    defaults: dict[str, str] = {}
    revised = query

    if _UPDATE_KEYWORDS.search(_strip_comments(query, language)):
        issues.append(TranslationIssue.WRITES)
        log.info("api.translation_rejected_write", language=language.value)

    has_temporal = any(token in query for token in ("asOf", "validAt", "txFrom", "validFrom"))
    if not has_temporal:
        issues.append(TranslationIssue.NO_TEMPORAL_SCOPE)
        if as_of is not None:
            defaults["asOf"] = as_of.isoformat()
        if valid_at is not None:
            defaults["validAt"] = valid_at.isoformat()

    if "modality" not in query:
        issues.append(TranslationIssue.NO_MODALITY)
        defaults["modality"] = modality.value

    if language is QueryLanguage.SPARQL:
        match = _LIMIT.search(query)
        if match is None:
            issues.append(TranslationIssue.UNBOUNDED)
            defaults["limit"] = str(limit)
            if TranslationIssue.WRITES not in issues:
                revised = f"{query.rstrip()}\nLIMIT {limit}"
    elif "first:" not in query and "limit:" not in query:
        issues.append(TranslationIssue.UNBOUNDED)
        defaults["limit"] = str(limit)

    translated = TranslatedQuery(
        question=question,
        language=language,
        query=revised,
        issues=tuple(issues),
        applied_defaults=defaults,
        llm_provenance=dict(llm_provenance or {}),
    )
    log.info(
        "api.translation_reviewed",
        language=language.value,
        executable=translated.executable,
        issues=[issue.value for issue in issues],
        defaults=sorted(defaults),
    )
    return translated


def _strip_comments(query: str, language: QueryLanguage) -> str:
    """コメントを落とす（キーワード検出の前に）。

    SPARQL は ``#``、GraphQL も ``#`` でコメントする。コメントを残すと ``# DELETE しない`` のような
    記述で誤って拒否し、落としすぎると ``SELECT ... # 改行 DELETE`` を見逃す——だから落とした
    **本文全体**を見る。
    """
    del language  # どちらも # 始まりの行コメント
    return re.sub(r"#[^\n]*", " ", query)
