"""API 層。GraphQL、SPARQL プロキシ、ストリーム購読、コマンド。設計書 §10.1。

責務: 意味 API の公開、エージェント向けツール、認可。
非責務: モデル学習、永続化の実装、SPARQL 文字列の組み立て（``knowledge`` の内側に閉じる）。

**読み取りと副作用の経路を分けている。** GraphQL は mutation を持たず、アクションの実行は
``/commands/*`` に置く。同じ経路で副作用を出せるようにすると、承認フロー（設計書 §9.2）の
迂回が容易になる。SPARQL プロキシは読み取り専用で、更新操作は拒否する（不変条件 1・2）。
"""

from __future__ import annotations

from sbp.api.app import create_app, is_read_only_sparql
from sbp.api.schema import BeliefReader, BeliefSource, BeliefType, Query, build_schema

__all__ = [
    "BeliefReader",
    "BeliefSource",
    "BeliefType",
    "Query",
    "build_schema",
    "create_app",
    "is_read_only_sparql",
]
