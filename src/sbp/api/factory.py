"""API を実物の依存で組む（M8-13）。

``create_app`` は依存を引数で受け取る設計で（テストで差し替えられるようにするため）、
**実物を組む経路がどこにも無かった**——API はテストの中で偽の依存を渡して起動したことしか
無く、``make dev`` が参照する起動スクリプトも存在しなかった（``docs/PROGRESS.md`` 所見 68）。

ここが「本番に近い配線」の 1 箇所である。4 ワーカーの ``build_worker()`` に対応する。

**認証は開発用に限る。** 本番の認証方式は未決（ADR-0008 の未決）。``SBP_ENV=dev`` のときだけ
ヘッダから要求者を作り、それ以外では ``principal_resolver`` を渡さない——``create_app`` は
未設定なら信念クエリを**すべて拒否する**（既定は拒否）。「未設定なら通す」経路を作らない。

**配線できないものは渡さない。** ストリーム購読の方式は未決（ADR-0008 の未決）なので
``None`` を渡す——``create_app`` が 503 を返すので、「動いているように見えて何もしない」状態に
ならない。

**コマンドは受け付けるだけ**（M8-08、ADR-0012）。人の判断を作業キューに積み、適用は
``learning-worker`` が行う。ハンドラが重い仕事をすると、HTTP のタイムアウトが人の判断を
失わせる。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sbp.api.app import create_app
from sbp.api.authorization import Permission, Principal
from sbp.core.config import Settings
from sbp.core.logging import get_logger
from sbp.core.modality import Modality

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
    from datetime import datetime

    from fastapi import FastAPI, Request

    from sbp.actions.contracts import ActionContract
    from sbp.core.metrics import MetricSample
    from sbp.knowledge.contributions import ContributionRecord
    from sbp.knowledge.decisions import DecisionKind, DecisionRow, DecisionStatus
    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.knowledge.expectations import ExpectationRecord

__all__ = ["build_app", "dev_principal_resolver", "lazy_contracts"]

log = get_logger(__name__)

_PRINCIPAL_HEADER = "X-SBP-Principal"
"""開発用に要求者を名乗るヘッダ。**本番ではこの経路を使わない。**"""

_DEV_ROLE = "https://sbp.example/role/dev-operator"
"""開発用のロール IRI。"""


def dev_principal_resolver(settings: Settings) -> Any:
    """開発用の要求者解決を作る（``SBP_ENV=dev`` 専用）。

    ヘッダ ``X-SBP-Principal`` に主体 IRI を入れると、その主体に**自拠点の全概念・
    observed / inferred / predicted / planned の閲覧**を許す要求者を作る。
    ``hypothetical`` は含めない——検討中の案が「決まった」と誤解される（authorization の規約）。

    **``dev`` 以外では使わない。** ヘッダを名乗るだけで権限が付くのは認証ではない。

    Args:
        settings: 設定（拠点と環境の判定に使う）。

    Returns:
        ``リクエスト -> Principal | None`` の関数。

    Raises:
        ValueError: ``SBP_ENV`` が ``dev`` 以外の場合。**ここで落とす**のが要点である
            ——本番でこの解決器が使われていたら、起動させてはいけない。
    """
    if settings.env != "dev":
        raise ValueError(
            f"dev_principal_resolver must not be used with SBP_ENV={settings.env}; "
            "naming a principal in a header is not authentication. Decide the production "
            "authentication method first (ADR-0008 open question)."
        )

    permission = Permission(
        concept_types=frozenset({"*"}),
        modalities=frozenset(
            {Modality.OBSERVED, Modality.INFERRED, Modality.PREDICTED, Modality.PLANNED}
        ),
        sites=frozenset({settings.site_id}),
    )

    def resolve(request: Request) -> Principal | None:
        """ヘッダから要求者を作る（無ければ ``None`` = 拒否）。

        Args:
            request: HTTP リクエスト。
        """
        subject = request.headers.get(_PRINCIPAL_HEADER, "").strip()
        if not subject:
            return None
        return Principal(
            subject=subject,
            roles=frozenset({_DEV_ROLE}),
            permissions=(permission,),
        )

    return resolve


def _run_sync[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """非同期の読み出しを同期の文脈から呼ぶ（GraphQL のリゾルバ用）。

    リゾルバの中から ``asyncio.run`` は呼べない（すでに実行中のループの中にいる）。別の
    スレッドで新しいループを回して結果を待つ。**接続は 1 つを共有している**ので、同時に
    複数のリゾルバが走ると psycopg の接続が競合しうる——GraphQL の 1 リクエストで
    ``discrepancies`` が複数回呼ばれる形になったら、接続プールに変える必要がある
    （未決。いまは 1 クエリ 1 呼び出し）。

    Args:
        coroutine: 実行するコルーチン。

    Returns:
        コルーチンの結果。
    """
    import asyncio  # noqa: PLC0415 — ここだけで使う
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def lazy_contracts() -> Callable[[], list[ActionContract]]:
    """アクション契約を返す関数を作る。**初回の読み出しで読み、以後は覚える。**

    ``build_app`` では読まない（構築時に I/O をしない契約。カレントディレクトリに依存する
    相対パスなので、別の場所から起動しても組み立ては落ちず、``actionTypes`` が理由つきの
    エラーを返す。M8-22 のレビュー）。
    """
    from sbp.actions import ActionRegistry  # noqa: PLC0415 — 実運用の配線でのみ使う

    cache: dict[str, list[ActionContract]] = {}

    def read() -> list[ActionContract]:
        if "contracts" not in cache:
            registry = ActionRegistry.from_directory(Path("actions"))
            cache["contracts"] = [registry.get(t) for t in registry.types()]
        return cache["contracts"]

    return read


def build_app(settings: Settings | None = None) -> FastAPI:
    """実物の依存で API を組む。

    uvicorn の ``factory=True`` から呼ばれる（ホットリロードは import 文字列を要求するため、
    アプリ実体ではなく組み立て関数を渡す）。

    配線するもの:

    - 二時制クエリ: ``AssertionStore``（Oxigraph）
    - SPARQL プロキシ: ``HttpGraphStore.query``（``create_app`` 側が更新操作を拒否する）
    - 指標: ``MetricStore.latest``（Postgres）。接続はアプリの寿命に合わせて開閉する
    - 要求者: ``SBP_ENV=dev`` のときだけヘッダから（それ以外は未設定 = 全拒否）

    Args:
        settings: 設定（既定は環境から読む）。

    Returns:
        FastAPI アプリ。
    """
    from sbp.api.commands import build_command_handler  # noqa: PLC0415
    from sbp.knowledge import (  # noqa: PLC0415
        AssertionStore,
        ConceptProposalRecord,
        ConceptProposalStore,
        ContributionStore,
        DecisionQueue,
        DiscrepancyStore,
        ExpectationStore,
        ForecastRecord,
        ForecastStore,
        HttpGraphStore,
        MetricStore,
    )

    resolved = settings or Settings()
    store = HttpGraphStore(resolved)
    beliefs = AssertionStore(store, site_id=resolved.site_id)

    # Postgres 接続はアプリの寿命に合わせる。リクエストごとに繋ぐと、スクレイプの頻度
    # （既定 30 秒）で接続が作られ続ける
    state: dict[str, Any] = {"connection": None}

    def _connection() -> Any:
        """lifespan が開いた接続。開く前に使われたら落とす。

        空や既定値を返すと、「指標が無い」「判断が積めた」のように**失敗が成功に見える**。
        """
        connection = state["connection"]
        if connection is None:
            raise RuntimeError("postgres is not connected yet (lifespan has not started)")
        return connection

    async def enqueue_decision(row: DecisionRow) -> bool:
        """人の判断を作業キューに積む（適用は learning-worker。ADR-0012）。

        Args:
            row: 判断。
        """
        return await DecisionQueue(_connection(), site=resolved.site_id).enqueue(row)

    def read_discrepancies(
        *, kind: int | None = None, include_terminal: bool = False, limit: int = 50
    ) -> list[DiscrepancyRecord]:
        """不一致キューを同期の形で返す（GraphQL のリゾルバは同期で呼ばれる）。

        **専用のイベントループで回す。** リゾルバの中から ``asyncio.run`` は呼べない
        （すでにループの中にいる）ので、別のスレッドで実行する。キューの読み出しは
        1 クエリなので、スレッドを 1 つ使う代価で足りる。

        Args:
            kind: 五類型で絞る。
            include_terminal: 終端状態も含めるか。
            limit: 返す件数の上限。
        """
        store = DiscrepancyStore(_connection(), site=resolved.site_id)
        return _run_sync(store.queue(kind=kind, include_terminal=include_terminal, limit=limit))

    def read_expectations(
        *, target: str | None = None, include_terminal: bool = False, limit: int = 50
    ) -> list[ExpectationRecord]:
        """期待ボードを同期の形で返す（M8-18）。

        Args:
            target: 対象実体で絞る。
            include_terminal: 終端状態も含めるか。
            limit: 返す件数の上限。
        """
        store = ExpectationStore(_connection(), site=resolved.site_id)
        return _run_sync(store.board(target=target, include_terminal=include_terminal, limit=limit))

    def read_proposals(
        *, include_curated: bool = False, limit: int = 50
    ) -> list[ConceptProposalRecord]:
        """curation 待ちの概念提案を同期の形で返す（M8-19）。

        Args:
            include_curated: 採否が入ったものも含めるか。
            limit: 返す件数の上限。
        """
        store = ConceptProposalStore(_connection(), site=resolved.site_id)
        return _run_sync(store.queue(include_curated=include_curated, limit=limit))

    def read_forecasts(
        *, entity: str | None = None, fluent: str | None = None, limit: int = 50
    ) -> list[ForecastRecord]:
        """予測を同期の形で返す（M9-03）。

        Args:
            entity: 実体で絞る。
            fluent: フルーエント型で絞る。
            limit: 返す件数の上限。
        """
        store = ForecastStore(_connection(), site=resolved.site_id)
        return _run_sync(store.recent(entity=entity, fluent=fluent, limit=limit))

    def read_evidence(
        entity: str, fluent: str, start: datetime, end: datetime
    ) -> list[ContributionRecord]:
        """信念の三源の寄与を同期の形で返す（M8-17、ADR-0020）。

        Args:
            entity: 実体の IRI。
            fluent: フルーエント型の IRI。
            start: 区間の始端（含む）。
            end: 区間の終端（含まない）。
        """
        store = ContributionStore(_connection(), site=resolved.site_id)
        return _run_sync(store.series(entity=entity, fluent=fluent, start=start, end=end))

    def read_decisions(
        *, subject: str, kinds: Sequence[DecisionKind] = (), limit: int = 50
    ) -> list[DecisionStatus]:
        """人の判断の状態を同期の形で返す（M8-22、ADR-0021）。

        Args:
            subject: 主語（不一致 ID・提案 ID・実体 IRI）。
            kinds: 種類で絞る。
            limit: 返す件数の上限。
        """
        queue = DecisionQueue(_connection(), site=resolved.site_id)
        return _run_sync(queue.history(subject=subject, kinds=kinds, limit=limit))

    async def read_metrics() -> list[MetricSample]:
        """系列ごとの最新値を返す。"""
        return await MetricStore(_connection(), site=resolved.site_id).latest()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        """Postgres 接続を開閉する。"""
        del app
        import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

        connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
        state["connection"] = connection
        log.info("api.wired", site_id=resolved.site_id, env=resolved.env)
        try:
            yield
        finally:
            state["connection"] = None
            await connection.close()

    app = create_app(
        settings=resolved,
        assertion_store=beliefs,
        sparql_reader=lambda query: {"results": store.query(query)},
        # **購読とコマンドは渡さない。** 購読の方式は未決（ADR-0008）、コマンドのハンドラは
        # 学習信号の適用（M8-08）が決める。None なら create_app が 503 を返す
        stream_reader=None,
        command_handler=build_command_handler(enqueue_decision),
        principal_resolver=(dev_principal_resolver(resolved) if resolved.env == "dev" else None),
        metric_reader=read_metrics,
        discrepancy_reader=read_discrepancies,
        expectation_reader=read_expectations,
        proposal_reader=read_proposals,
        forecast_reader=read_forecasts,
        evidence_reader=read_evidence,
        action_reader=lazy_contracts(),
        decision_reader=read_decisions,
    )
    app.router.lifespan_context = lifespan
    return app
