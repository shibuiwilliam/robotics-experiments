"""FastAPI アプリ（設計書 §10.1、M5-07）。

公開するのは 4 形態（設計書 §10.1 の表）:

| 経路 | 用途 |
|---|---|
| ``/graphql`` | オントロジー上の読み取り。時制・様相はフィールド引数 |
| ``/sparql`` | 分析・監査（読み取り専用のプロキシ） |
| ``/stream/{topic}`` | イベントログの購読（不一致・期待・予測） |
| ``/commands/*`` | ActionType の実行・承認・補償 |

**SPARQL プロキシは読み取り専用にする。** ``UPDATE`` を通すと、二時制 API（``assert_`` /
``retract`` / ``query`` の 3 メソッド）を迂回して信念を書けてしまい、追記専用（不変条件 2）と
文メタデータの完全性（不変条件 1）がどちらも破れる。クエリ文字列に更新操作が含まれていたら
拒否する。

**コマンド API は GraphQL と分ける。** mutation として出すと、読み取りと同じ経路で副作用が
出せるようになり、承認フロー（設計書 §9.2）の迂回が容易になる。

**ストリームは購読だけ。** 投入（produce）の経路を API に作らない。イベントの発生源は接地層・
統合層・推論層であり、外から任意のイベントを流し込めると来歴が意味を失う。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from sbp.api.commands import bind_principal
from sbp.api.schema import BeliefSource, build_schema
from sbp.core.logging import get_logger
from sbp.core.metrics import prometheus_exposition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime

    from sbp.actions.contracts import ActionContract
    from sbp.api.authorization import Principal
    from sbp.api.schema import BeliefReader
    from sbp.core.config import Settings
    from sbp.core.metrics import MetricSample
    from sbp.knowledge.concepts import ConceptProposalRecord
    from sbp.knowledge.contributions import ContributionRecord
    from sbp.knowledge.decisions import DecisionStatus
    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.knowledge.expectations import ExpectationRecord

    MetricReader = Callable[[], Awaitable[Sequence["MetricSample"]]]
    """系列ごとの最新値を返す（``MetricStore.latest`` を包む）。"""
    ExpectationReader = Callable[..., Sequence["ExpectationRecord"]]
    ProposalReader = Callable[..., Sequence["ConceptProposalRecord"]]
    ActionReader = Callable[[], Sequence["ActionContract"]]
    """契約の一覧を返す（``ActionRegistry`` の契約。同期）。"""
    DecisionReader = Callable[..., Sequence["DecisionStatus"]]
    """判断の状態を返す（``DecisionQueue.history`` を包む。同期）。"""
    EvidenceReader = Callable[[str, str, datetime, datetime], Sequence["ContributionRecord"]]
    """``(実体, フルーエント, 始端, 終端) -> 寄与の列``（``ContributionStore.series`` を包む）。"""
    """curation 待ちの概念提案を返す（``ConceptProposalStore.queue`` を包む）。"""
    """期待ボードを返す（``ExpectationStore.board`` を包む。同期）。"""
    DiscrepancyReader = Callable[..., Sequence["DiscrepancyRecord"]]
    """不一致キューを返す（``DiscrepancyStore.queue`` を包む）。

    **同期の関数である。** GraphQL のリゾルバは同期で呼ばれるので、呼び出し側が非同期の
    読み出しを同期の形に包む（``api.factory`` を参照）。
    """

__all__ = ["create_app", "is_read_only_sparql"]

log = get_logger(__name__)

_UPDATE_KEYWORDS = re.compile(
    r"\b(INSERT|DELETE|CLEAR|DROP|CREATE|LOAD|MOVE|COPY|ADD)\b", re.IGNORECASE
)
"""SPARQL 1.1 Update の操作。1 つでも含まれていたらプロキシを通さない。"""

_COMMENT = re.compile(r"#[^\n]*")
"""SPARQL のコメント（キーワード検出の前に落とす）。"""


def is_read_only_sparql(query: str) -> bool:
    """SPARQL が読み取り専用か（``SELECT`` / ``ASK`` / ``CONSTRUCT`` / ``DESCRIBE``）。

    **コメントを落としてから判定する。** ``# DELETE`` のようなコメントで誤って拒否するのを
    避けるためだが、逆に ``SELECT ... # と書いて改行後に DELETE`` のような回避も防ぐ必要がある
    ——だからコメント除去後の本文全体を見る。

    Args:
        query: SPARQL 文字列。

    Returns:
        読み取り専用なら真。
    """
    body = _COMMENT.sub(" ", query)
    return not _UPDATE_KEYWORDS.search(body)


def create_app(
    *,
    settings: Settings,
    assertion_store: BeliefReader,
    sparql_reader: Callable[[str], dict[str, Any]] | None = None,
    stream_reader: Callable[[str, int], list[dict[str, Any]]] | None = None,
    command_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    principal_resolver: Callable[[Request], Principal | None] | None = None,
    metric_reader: MetricReader | None = None,
    discrepancy_reader: DiscrepancyReader | None = None,
    expectation_reader: ExpectationReader | None = None,
    proposal_reader: ProposalReader | None = None,
    evidence_reader: EvidenceReader | None = None,
    action_reader: ActionReader | None = None,
    decision_reader: DecisionReader | None = None,
) -> FastAPI:
    """アプリを組み立てる。

    依存を引数で受け取るのは、テストで実物のストアを差し替えられるようにするためと、
    **API 層が自分で接続を作らない**ようにするためである（接続の寿命はワーカーと同じ単位で
    管理する）。

    Args:
        settings: 設定。
        assertion_store: 二時制クエリを提供するもの（``AssertionStore`` が満たす Protocol）。
        sparql_reader: SPARQL クエリの実行関数（``None`` ならプロキシを無効にする）。
        stream_reader: ``(トピック, 最大件数)`` → イベントの列。
        command_handler: ``(コマンド名, 引数)`` → 結果。
        principal_resolver: リクエスト → 要求者（ABAC、M7-06）。``None`` だと信念クエリは
            **すべて拒否される**（既定は拒否）。認証の実装は運用環境に依るのでここでは受け取る
            だけにし、「未設定なら通す」経路を作らない。
        discrepancy_reader: 不一致キューを返すもの（``DiscrepancyStore.queue`` を包む。
            M8-12）。``None`` だと GraphQL の ``discrepancies`` が**エラーを返す**——空の列を
            返すと「不一致が無い」と「配線していない」が区別できない（所見 32）。
        proposal_reader: curation 待ちの概念提案を返すもの（``ConceptProposalStore.queue`` を
            包む。``None`` なら ``proposals`` クエリが失敗する——**空を返さない**。「提案が無い」
            と「読めない」を区別する、所見 32）。
        expectation_reader: 期待ボードを返すもの（``ExpectationStore.board`` を包む。
            M8-18）。``None`` だと GraphQL の ``expectations`` が**エラーを返す**
            （空の列を返すと「期待が無い」と「配線していない」が区別できない）。
        action_reader: アクション契約を返すもの（M8-22）。``None`` だと ``actionTypes`` が
            **エラーを返す**（空を返すと「アクションが無い」と「配線していない」が区別できない）。
        decision_reader: 判断の状態を返すもの（``DecisionQueue.history`` を包む。M8-22）。
            ``None`` だと ``decisions`` が**エラーを返す**。
        evidence_reader: 信念の三源の寄与を返すもの（``ContributionStore.series`` を包む。
            M8-17）。``None`` だと ``beliefEvidence`` が**エラーを返す**（空を返すと「寄与が
            無い」と「配線していない」が区別できない）。
        metric_reader: 系列ごとの最新値を返すもの（``MetricStore.latest`` を包む）。
            ``None`` なら ``/metrics`` は**空ではなく 503 を返す**——空の exposition を返すと
            Prometheus は「スクレイプ成功、系列 0 件」と読み、指標が止まっていることと
            「配線していない」ことが区別できない（所見 32）。

    Returns:
        FastAPI アプリ。
    """
    from strawberry.fastapi import GraphQLRouter  # noqa: PLC0415 — 任意依存を局所化

    ontology_version = _ontology_version()
    beliefs = BeliefSource(assertion_store, ontology_version=ontology_version)

    async def context(request: Request) -> dict[str, Any]:
        """GraphQL の文脈。要求者を解決して載せる。"""
        principal = None if principal_resolver is None else principal_resolver(request)
        return {
            "beliefs": beliefs,
            "settings": settings,
            "principal": principal,
            "discrepancies": discrepancy_reader,
            "expectations": expectation_reader,
            "proposals": proposal_reader,
            "evidence": evidence_reader,
            "actions": action_reader,
            "decisions": decision_reader,
        }

    app = FastAPI(
        title="SBP 意味 API",
        version=ontology_version,
        description=(
            "物理と業務の信念を同じ語彙・時間軸・確信度で返す。時制と様相はフィールド引数で"
            "指定する（設計書 §10.1）。"
        ),
    )
    app.include_router(GraphQLRouter(build_schema(), context_getter=context), prefix="/graphql")

    @app.get("/health", summary="疎通確認")
    async def health() -> dict[str, Any]:
        """API とストアの疎通を返す。"""
        return {
            "ontology_version": ontology_version,
            "store_reachable": beliefs.reachable(),
        }

    @app.get("/metrics", summary="Prometheus の exposition（observability profile が取り込む）")
    async def metrics() -> PlainTextResponse:
        """系列ごとの最新値を Prometheus 形式で返す（設計書 §12.3）。

        **最新値だけを返す。** 履歴は Prometheus 側に溜まる。全履歴を出すとスクレイプごとに
        同じ古い点を再投入することになり、Prometheus 側で時刻が付け替わって履歴が壊れる。

        Raises:
            HTTPException: ``metric_reader`` が未設定の場合（503）。空を返さないのは、
                「指標が止まっている」と「配線していない」を区別するためである。
        """
        if metric_reader is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "metric reader is not configured; returning an empty exposition would "
                    "look like a successful scrape with zero series"
                ),
            )
        samples = await metric_reader()
        return PlainTextResponse(
            prometheus_exposition(samples),
            # Prometheus の text format の媒体型。付けないと取り込み側が推測する
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post("/sparql", summary="SPARQL プロキシ（読み取り専用）")
    async def sparql(request: Request) -> JSONResponse:
        """SPARQL クエリをプロキシする（更新操作は拒否）。

        Raises:
            HTTPException: プロキシが無効、クエリが無い、または更新操作を含む場合。
        """
        if sparql_reader is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SPARQL プロキシは設定されていない",
            )
        payload = await request.json()
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query が無い")
        if not is_read_only_sparql(query):
            log.info("api.sparql_update_rejected", length=len(query))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "更新操作は受け付けない。信念の書き込みは二時制 API "
                    "（assert_ / retract）のみを通る（不変条件 1・2）"
                ),
            )
        return JSONResponse(sparql_reader(query))

    @app.get("/stream/{topic}", summary="イベントログの購読")
    async def stream(topic: str, limit: int = 20) -> dict[str, Any]:
        """トピックの直近イベントを返す（購読のみ、投入はしない）。

        Raises:
            HTTPException: 購読が設定されていない、または ``limit`` が範囲外の場合。
        """
        if stream_reader is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ストリーム購読は設定されていない",
            )
        if not 1 <= limit <= 500:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit は 1〜500")
        events = stream_reader(topic, limit)
        return {"topic": topic, "count": len(events), "events": events}

    @app.post("/commands/{name}", summary="コマンド（アクションの実行・承認・補償）")
    async def command(name: str, request: Request) -> dict[str, Any]:
        """コマンドを実行する。

        **GraphQL の mutation ではなくここに置いている**のは、副作用の経路を読み取りと分けて
        おくためである（設計書 §9.2 の承認フローを迂回させない）。

        Raises:
            HTTPException: ハンドラが設定されていない、または引数が不正な場合。
        """
        if command_handler is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="コマンド API は設定されていない",
            )
        payload = await _command_payload(name, request, principal_resolver)
        try:
            result = command_handler(name, payload)
        except Exception as exc:
            log.info("api.command_failed", command=name, error=str(exc)[:200])
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        log.info("api.command_executed", command=name)
        return result

    log.info(
        "api.app_created",
        ontology_version=ontology_version,
        sparql=sparql_reader is not None,
        stream=stream_reader is not None,
        commands=command_handler is not None,
        abac=principal_resolver is not None,
        discrepancies=discrepancy_reader is not None,
        expectations=expectation_reader is not None,
        proposals=proposal_reader is not None,
    )
    if principal_resolver is None:
        log.warning(
            "api.abac_not_configured",
            detail="principal_resolver が無いので信念クエリはすべて拒否される（既定は拒否）",
        )
    return app


def _ontology_version() -> str:
    """稼働中のオントロジー版（生成物から読む）。"""
    from sbp.core import vocab  # noqa: PLC0415 — 生成物への依存を局所化

    return vocab.ONTOLOGY_VERSION


def _command_principal(
    resolver: Callable[[Request], Principal | None] | None, request: Request
) -> Principal:
    """コマンドの要求者を解決する。解決できない・エージェントなら拒否する（H-25、ADR-0029）。

    Args:
        resolver: リクエスト → 要求者。
        request: HTTP リクエスト。

    Raises:
        HTTPException: 401（要求者を解決できない）、403（エージェント）。
    """
    principal = None if resolver is None else resolver(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="要求者を解決できない。コマンドは認証済みの主体にだけ受ける",
        )
    if principal.is_agent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="エージェントは人の判断（解決・採否・承認・回答）を出せない",
        )
    return principal


def _bind_command(name: str, payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """本文の主体の欄を要求者に結びつける（食い違えば 403）。

    Args:
        name: コマンド名。
        payload: 引数。
        principal: 認証済みの要求者。
    """
    try:
        return bind_principal(name, payload, principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PermissionError as exc:
        log.info("api.command_refused", command=name, principal=principal.subject)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _command_payload(
    name: str,
    request: Request,
    resolver: Callable[[Request], Principal | None] | None,
) -> dict[str, Any]:
    """コマンドの引数を読み、主体の欄を認証済みの要求者に結びつける。

    Args:
        name: コマンド名。
        request: HTTP リクエスト。
        resolver: リクエスト → 要求者。

    Raises:
        HTTPException: 要求者が解決できない・エージェント・引数が JSON オブジェクトでない・
            本文の主体が要求者と食い違う場合。
    """
    principal = _command_principal(resolver, request)
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="引数は JSON オブジェクト"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="引数は JSON オブジェクト"
        )
    return _bind_command(name, payload, principal)
