"""学習信号の適用ワーカー（M8-08、ADR-0012）。

人の判断（不一致の解決・概念提案の採否）を作業キューから引き、学習信号に変換して適用する。
**5 番目のワーカー**で、既定では起動しない（`scripts/dev.py --workers learning`）。

**全部を自動適用しない**（ADR-0012 の問い 3）。

| 成果 | 扱い |
|---|---|
| ``observation_requests`` | **自動で適用**（作業キューに積む）。副作用が無い |
| ``corrections``（書き戻し） | **既定 dry で実行**。``live`` は運用者が明示する |
| ``reliability_updates`` | 記録するだけ。1 件の解決で全照合の尤度を動かさない |
| ``bags``（負例） | 記録するだけ。再学習は昇格ゲートを通る別の仕事 |
| ``concept_candidates`` | 記録するだけ。オントロジーの MINOR 起票は人の手作業 |

**照合ワーカーに相乗りしない理由。** 照合は窓を回すホットパスで、書き戻しは外部システムへの
同期呼び出しである。詰まった書き戻しが検出を止める（ADR-0012 の問い 2）。

**不一致の本体を読む先が無い**（M8-12）。``convert_resolution`` は ``Discrepancy`` を要求する
ので、キューの行から**最小の不一致を再構成して**渡している。これは本体を読めるようになったら
差し替える——再構成した不一致は証拠 IRI を持たないので、**記録訂正の提案が出ない**（証拠から
記録を引けない）。その制限はログに出す。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sbp.core.config import Settings, WritebackMode
from sbp.core.logging import get_logger
from sbp.core.time import Interval, now
from sbp.knowledge.decisions import DecisionKind
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Resolution,
    ResolutionOutcome,
    Severity,
    convert_resolution,
)
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime

    from sbp.integration.writeback import WritebackCommand
    from sbp.knowledge.decisions import DecisionQueue, DecisionRow
    from sbp.knowledge.observations import ObservationRow
    from sbp.reasoning.reconciliation.learning import LearningArtifacts

    ObservationSink = Callable[[Sequence["ObservationRow"]], Awaitable[int]]
    """観測要求を作業キューに積む（``ObservationQueue.enqueue``）。"""
    WritebackRunner = Callable[["WritebackCommand"], Awaitable[object]]
    """書き戻しを 1 件実行する（既定 dry）。"""

__all__ = ["LearningWorker", "build_worker", "main", "reconstruct_discrepancy"]

log = get_logger(__name__)

WORKER_NAME = "learning-worker"

_DEFAULT_PERIOD_S = 30.0
"""キューを引く周期（秒）。人の判断は毎秒来るものではない。"""


def readiness_checks(settings: Settings) -> tuple[ReadinessCheck, ...]:
    """このワーカーの依存（知識グラフ）。

    **イベントログに依存しない。** 判断はキューから来て、成果はキューと書き戻しに出る。

    Args:
        settings: 設定。
    """
    from services.belief_worker import http_reachable  # noqa: PLC0415 — 疎通検査を共有する

    return (
        ReadinessCheck(name="oxigraph", probe=lambda: http_reachable(settings.sparql_query_url)),
    )


class LearningWorker:
    """人の判断を引いて学習信号を適用する。

    Attributes:
        settings: 設定。
        runtime: 実行ループ。
        applied_total: 適用できた判断の累計。
        failed_total: 適用に失敗した判断の累計。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        decisions: DecisionQueue | None = None,
        observations: ObservationSink | None = None,
        writeback: WritebackRunner | None = None,
        period_s: float = _DEFAULT_PERIOD_S,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        decisions: 判断の作業キュー（``None`` なら何も適用しない）。
        observations: 観測要求の積み込み（``None`` なら積まない）。
        writeback: 書き戻しの実行（``None`` なら**提案として記録するだけ**。
            既定が ``None`` なのは意図的である——書き戻しは業務システムを変えるので、
            配線は運用者が明示する）。
        period_s: キューを引く周期（秒）。
        """
        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME,
            settings=self.settings,
            checks=readiness_checks(self.settings),
        )
        self._decisions = decisions
        self._observations = observations
        self._writeback = writeback
        self.period = timedelta(seconds=period_s)
        self._last_poll: datetime | None = None
        self.applied_total = 0
        self.failed_total = 0
        if decisions is None:
            log.warning(
                "worker.no_decision_queue",
                worker=WORKER_NAME,
                detail="判断のキューが無いので何も適用しない",
            )
        if writeback is None:
            log.info(
                "worker.writeback_not_wired",
                worker=WORKER_NAME,
                detail="書き戻しは提案として記録するだけ（業務システムを変える経路は明示配線）",
            )

    def poll_due(self, *, at: datetime) -> bool:
        """この時点でキューを引くべきか。

        Args:
            at: 判定時刻。
        """
        if self._last_poll is None:
            return True
        return at - self._last_poll >= self.period

    async def step(self) -> int:
        """1 ループ。未適用の判断を適用する。

        Returns:
            適用できた判断の件数。
        """
        if self._decisions is None:
            return 0
        at = now()
        if not self.poll_due(at=at):
            return 0
        self._last_poll = at

        pending = await self._decisions.pending(limit=self.settings.reasoning.batch_size)
        if not pending:
            return 0

        applied = 0
        for row in pending:
            if await self._apply(row, at=at):
                applied += 1
        log.info(
            "worker.learning_cycle",
            worker=WORKER_NAME,
            pending=len(pending),
            applied=applied,
            failed=len(pending) - applied,
        )
        return applied

    async def _apply(self, row: DecisionRow, *, at: datetime) -> bool:
        """判断 1 件を適用する。

        **失敗を握りつぶさない。** 例外はキューの ``failed_at`` と ``error`` に残し、
        ループは次の判断へ進む——1 件の失敗で残りの判断を止めると、人の判断が溜まる。

        Args:
            row: 判断。
            at: 処理時刻。

        Returns:
            適用できたか。
        """
        assert self._decisions is not None  # step() が確認済み
        try:
            summary = await self._dispatch(row, at=at)
        except Exception as exc:
            self.failed_total += 1
            await self._decisions.mark_failed(
                row.decision_id, at=at, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        await self._decisions.mark_applied(row.decision_id, at=at, artifacts=summary)
        self.applied_total += 1
        return True

    async def _dispatch(self, row: DecisionRow, *, at: datetime) -> dict[str, Any]:
        """種類で分岐する。

        Args:
            row: 判断。
            at: 処理時刻。

        Returns:
            何を適用したかの要約。

        Raises:
            ValueError: 未知の種類（表の CHECK を通っているので起こらないが、型で閉じる）。
        """
        if row.kind is DecisionKind.RESOLUTION:
            return await self._apply_resolution(row, at=at)
        if row.kind is DecisionKind.CURATION:
            return self._apply_curation(row)
        raise ValueError(f"unknown decision kind {row.kind!r}")

    async def _apply_resolution(self, row: DecisionRow, *, at: datetime) -> dict[str, Any]:
        """不一致の解決を学習信号に変換して適用する。

        Args:
            row: 判断（``kind=resolution``）。
            at: 処理時刻。

        Returns:
            適用の要約。
        """
        resolution = Resolution(
            outcome=ResolutionOutcome(str(row.payload["outcome"])),
            note=str(row.payload["note"]),
            resolved_by=str(row.payload["resolved_by"]),
            resolved_at=row.received_at or at,
        )
        artifacts = convert_resolution(
            reconstruct_discrepancy(row, resolution=resolution, at=at),
            resolution=resolution,
            corrections=row.payload.get("corrections") or {},
            at=at,
        )
        enqueued = await self._enqueue_observations(artifacts, site=self.settings.site_id)
        written = await self._run_writebacks(artifacts)
        summary = {
            "outcome": resolution.outcome.value,
            "observation_requests_enqueued": enqueued,
            "writebacks_run": written,
            # **記録するだけのもの。** 件数を残すことで「何が適用されずに残っているか」が
            # UI から読める（ADR-0012 の問い 3）
            "reliability_updates_recorded": len(artifacts.reliability_updates),
            "bags_recorded": len(artifacts.bags),
            "concept_candidates_recorded": len(artifacts.concept_candidates),
            "writeback_mode": self.settings.writeback_mode.value,
        }
        log.info(
            "worker.resolution_applied",
            worker=WORKER_NAME,
            decision_id=row.decision_id,
            correlation_id=row.subject,
            **summary,
        )
        return summary

    def _apply_curation(self, row: DecisionRow) -> dict[str, Any]:
        """概念提案の採否を記録する。

        **採択してもオントロジーには触らない**（M6-04 の決定）。MINOR 版の起票は人の手作業で、
        自動で語彙を増やさない。

        Args:
            row: 判断（``kind=curation``）。

        Returns:
            適用の要約。
        """
        accepted = bool(row.payload.get("accepted"))
        summary = {
            "accepted": accepted,
            "proposal_id": row.subject,
            # 採択しても語彙は増えない。人が ontology/ を編集して make gen を回す
            "ontology_changed": False,
        }
        log.info(
            "worker.curation_recorded",
            worker=WORKER_NAME,
            decision_id=row.decision_id,
            correlation_id=row.subject,
            **summary,
        )
        return summary

    async def _enqueue_observations(self, artifacts: LearningArtifacts, *, site: str) -> int:
        """追加観測の要求を作業キューに積む（M8-05 の供給元）。

        Args:
            artifacts: 学習成果。
            site: 拠点識別子（キューは拠点ごと）。

        Returns:
            積んだ件数。
        """
        del site  # キューが拠点を知っている
        if self._observations is None or not artifacts.observation_requests:
            return 0
        from sbp.knowledge.observations import ObservationRow  # noqa: PLC0415

        rows = [
            ObservationRow(
                entity=request.entity,
                fluent=request.fluent,
                interval=request.interval,
                reason=request.reason,
                utility=request.utility,
                discrepancy_id=request.discrepancy_id,
            )
            for request in artifacts.observation_requests
        ]
        return await self._observations(rows)

    async def _run_writebacks(self, artifacts: LearningArtifacts) -> int:
        """記録訂正の書き戻しを実行する（既定 dry）。

        **``writeback`` が無ければ実行しない。** 業務システムを変える経路は明示配線にして
        あるので、未配線は「提案として記録するだけ」である。

        Args:
            artifacts: 学習成果。

        Returns:
            実行した件数。
        """
        if self._writeback is None or not artifacts.corrections:
            return 0
        if self.settings.writeback_mode is WritebackMode.DRY:
            log.info(
                "worker.writeback_dry",
                worker=WORKER_NAME,
                count=len(artifacts.corrections),
                detail="dry なので業務システムを変えない（SBP_WRITEBACK_MODE=live で実行）",
            )
        count = 0
        for command in artifacts.corrections:
            await self._writeback(command)
            count += 1
        return count

    async def run(self, *, max_iterations: int | None = None) -> int:
        """起動する。

        Args:
            max_iterations: テスト用の上限。
        """
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations)


def reconstruct_discrepancy(
    row: DecisionRow, *, resolution: Resolution, at: datetime
) -> Discrepancy:
    """キューの行から最小の不一致を再構成する。

    **本来は不一致の本体を読むべきである**（M8-12）。``convert_resolution`` は
    ``Discrepancy`` を要求するが、本体を書く先がまだ無いので、判断に含まれる情報だけで
    組む。再構成した不一致は**証拠 IRI を持たない**ので、記録訂正の提案は出ない
    （証拠から記録を引けない）。この制限は呼び出し側がログに出す。

    **識別子は引き継ぐ。** ``Discrepancy.discrepancy_id`` は既定で新しい UUID を作るので、
    渡さないと学習信号（観測要求など）が元の不一致に辿れなくなる——実際に統合テストで
    そうなった（所見 77）。

    Args:
        row: 判断。
        resolution: 解決の記録。
        at: 処理時刻。

    Returns:
        ``Resolved`` 直前の最小の不一致。

    Raises:
        ValueError: ``row.subject`` が UUID でない場合。**推測で新しい ID を作らない**
            ——辿れない学習信号は、無いほうがまだ分かる。
    """
    from sbp.core import vocab  # noqa: PLC0415 — オントロジー版だけに使う

    start = row.received_at or at
    try:
        discrepancy_id = UUID(row.subject)
    except ValueError as exc:
        raise ValueError(
            f"decision subject {row.subject!r} is not a UUID; a Discrepancy is identified by "
            "a UUID and inventing a new one would make the learning signals untraceable"
        ) from exc
    return Discrepancy(
        discrepancy_id=discrepancy_id,
        kind=DiscrepancyKind.STATE,
        # 主体は不明（本体が読めない）。**不一致 ID を主体にしない**——実体 IRI の位置に
        # 不一致の ID が入ると、観測要求が存在しない実体を指す
        subjects=(row.payload.get("entity") or row.subject,),
        detected_at=start,
        # 区間は不明。**0 幅にできない**（右開区間は空を許さない）ので、受け付け時刻から
        # 1 秒の区間にする。本体を読めるようになれば実際の区間が入る
        interval=Interval(start, start + timedelta(seconds=1)),
        severity=Severity(
            score=0.0,
            value_at_stake=0.0,
            # **確信度を推測しない。** 本体が読めないので 0 にしておく——「分からない」を
            # 中間の値で埋めると、学習信号の重みが根拠なく付く
            confidence=0.0,
            persistence_s=0.0,
        ),
        status=DiscrepancyStatus.ASSIGNED,
        assignee_role=resolution.resolved_by,
        detector="human-resolution",
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


async def build_worker(
    settings: Settings | None = None, *, site_id: str
) -> tuple[LearningWorker, DecisionQueue]:
    """実運用の配線を組む（判断キュー → 学習信号 → 観測要求キュー）。

    **書き戻しは配線しない。** 業務システムを変える経路は運用者が明示する
    （``SBP_WRITEBACK_MODE=live`` だけでは足りない——コネクタの資格情報が要る）。

    Args:
        settings: 設定（既定は環境から読む）。
        site_id: 拠点識別子。

    Returns:
        ``(ワーカー, 判断キュー)``。
    """
    import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

    from sbp.knowledge import DecisionQueue, ObservationQueue  # noqa: PLC0415

    resolved = settings or Settings()
    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
    decisions = DecisionQueue(connection, site=site_id)
    observations = ObservationQueue(connection, site=site_id)

    worker = LearningWorker(
        resolved,
        decisions=decisions,
        observations=observations.enqueue,
    )
    log.info("worker.wired", worker=WORKER_NAME, site_id=site_id)
    return worker, decisions


def main() -> int:
    """エントリポイント。

    判断は ``learning_decisions`` 表から取る（ADR-0012）。**書き戻しは未配線**で、記録訂正は
    提案として記録されるだけである。
    """

    async def _run() -> None:
        settings = Settings()
        worker, _queue = await build_worker(settings, site_id=settings.site_id)
        await worker.run()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
