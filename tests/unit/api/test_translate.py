"""NL→クエリ変換の単体テスト（設計書 §10.1、M6-06）。

重点:

1. **更新操作を含む変換は実行させない** — LLM の出力を人が書いたクエリと同じ扱いにしない。
2. **時制・様相・件数上限を補い、補ったことを見せる** — 自然言語には普通それが含まれない。
3. **クエリの意味は直さない** — 直すと変換の是非を評価できなくなる。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sbp.api import QueryLanguage, TranslationIssue, review_translation
from sbp.core.modality import Modality

T0 = datetime(2026, 10, 3, tzinfo=UTC)


class TestWriteRejection:
    @pytest.mark.parametrize(
        "query",
        [
            "DELETE WHERE { ?s ?p ?o }",
            "INSERT DATA { <a> <b> <c> }",
            "DROP GRAPH <g>",
            "SELECT * WHERE {?s ?p ?o} ; DELETE WHERE {?s ?p ?o}",
        ],
    )
    def test_sparql_writes_block_execution(self, query: str) -> None:
        result = review_translation("消して", query, language=QueryLanguage.SPARQL)
        assert TranslationIssue.WRITES in result.issues
        assert not result.executable

    def test_a_graphql_mutation_blocks_execution(self) -> None:
        result = review_translation(
            "作って", "mutation { createWorkOrder(x: 1) }", language=QueryLanguage.GRAPHQL
        )
        assert not result.executable

    def test_a_comment_mentioning_delete_does_not_block(self) -> None:
        """誤って拒否しない（が、コメント後の本文は見る）。"""
        result = review_translation(
            "状態は",
            "SELECT ?s WHERE { ?s ?p ?o } # DELETE はしない\nLIMIT 10",
            language=QueryLanguage.SPARQL,
        )
        assert result.executable

    def test_a_write_after_a_comment_still_blocks(self) -> None:
        result = review_translation(
            "状態は",
            "SELECT ?s WHERE { ?s ?p ?o } # ここまで読み取り\nDELETE WHERE { ?s ?p ?o }",
            language=QueryLanguage.SPARQL,
        )
        assert not result.executable


class TestDefaults:
    def test_missing_temporal_scope_is_filled_and_reported(self) -> None:
        """自然言語の質問には「いつ時点の、誰の主張か」が普通含まれない。"""
        result = review_translation(
            "P-101 は動いていますか",
            "SELECT ?s WHERE { ?s ?p ?o } LIMIT 10",
            language=QueryLanguage.SPARQL,
            as_of=T0,
            valid_at=T0,
        )
        assert TranslationIssue.NO_TEMPORAL_SCOPE in result.issues
        assert result.applied_defaults["asOf"] == T0.isoformat()
        assert result.applied_defaults["validAt"] == T0.isoformat()
        assert result.executable

    def test_missing_modality_is_filled(self) -> None:
        result = review_translation(
            "動いていますか",
            "SELECT ?s WHERE { ?s ?p ?o } LIMIT 10",
            language=QueryLanguage.SPARQL,
            modality=Modality.OBSERVED,
        )
        assert TranslationIssue.NO_MODALITY in result.issues
        assert result.applied_defaults["modality"] == "observed"

    def test_an_unbounded_sparql_query_gets_a_limit(self) -> None:
        """「全部見せて」が無制限のクエリになるとグラフ全体を引く。"""
        result = review_translation(
            "全部見せて", "SELECT ?s WHERE { ?s ?p ?o }", language=QueryLanguage.SPARQL
        )
        assert TranslationIssue.UNBOUNDED in result.issues
        assert "LIMIT 100" in result.query

    def test_an_existing_limit_is_respected(self) -> None:
        result = review_translation(
            "10 件", "SELECT ?s WHERE { ?s ?p ?o } LIMIT 10", language=QueryLanguage.SPARQL
        )
        assert TranslationIssue.UNBOUNDED not in result.issues
        assert result.query.count("LIMIT") == 1

    def test_a_graphql_query_needs_a_page_size(self) -> None:
        result = review_translation(
            "全部", "{ beliefs { subject } }", language=QueryLanguage.GRAPHQL
        )
        assert TranslationIssue.UNBOUNDED in result.issues

    def test_a_complete_query_has_no_issues(self) -> None:
        result = review_translation(
            "09:00 時点の推定は",
            '{ beliefs(subject: "x", asOf: "2026-09-12T09:00:00Z", modality: INFERRED, first: 10) '
            "{ subject } }",
            language=QueryLanguage.GRAPHQL,
        )
        assert result.issues == ()
        assert result.executable


class TestTransparency:
    def test_the_explanation_shows_what_was_filled_in(self) -> None:
        """使う側が「自分の質問がどう解釈されたか」を確認できなければ、誤答を正しいと思う。"""
        result = review_translation(
            "動いていますか",
            "SELECT ?s WHERE { ?s ?p ?o }",
            language=QueryLanguage.SPARQL,
            as_of=T0,
        )
        explanation = result.explain()
        assert "動いていますか" in explanation
        assert "補った既定値" in explanation

    def test_a_blocked_query_says_so(self) -> None:
        result = review_translation(
            "消して", "DELETE WHERE { ?s ?p ?o }", language=QueryLanguage.SPARQL
        )
        assert "実行しない" in result.explain()

    def test_the_llm_provenance_is_kept(self) -> None:
        result = review_translation(
            "動いていますか",
            "SELECT ?s WHERE { ?s ?p ?o } LIMIT 10",
            language=QueryLanguage.SPARQL,
            llm_provenance={"provider": "replay", "model": "fixture", "prompt_version": "1.0.0@h"},
        )
        assert result.llm_provenance["prompt_version"] == "1.0.0@h"

    def test_a_blocked_query_is_not_rewritten(self) -> None:
        """意味を直し始めると、変換の是非を評価できなくなる。"""
        original = "DELETE WHERE { ?s ?p ?o }"
        result = review_translation("消して", original, language=QueryLanguage.SPARQL)
        assert result.query == original

    def test_only_writes_block_execution(self) -> None:
        for issue in TranslationIssue:
            assert issue.blocks_execution is (issue is TranslationIssue.WRITES)
