"""人の判断の適用ワーカー（M8-08、ADR-0012 / M8-21、ADR-0014）。

人の判断（不一致の解決・概念提案の採否・**アクションの承認**）を作業キューから引いて適用する。
**5 番目のワーカー**で、既定では起動しない（`scripts/dev.py --workers learning`）。

**全部を自動適用しない**（ADR-0012 の問い 3）。

| 成果 | 扱い |
|---|---|
| ``observation_requests`` | **自動で適用**（作業キューに積む）。副作用が無い |
| ``corrections``（書き戻し） | **既定 dry で実行**。``live`` は運用者が明示する |
| ``reliability_updates`` | 記録するだけ。1 件の解決で全照合の尤度を動かさない |
| ``bags``（負例） | 記録するだけ。再学習は昇格ゲートを通る別の仕事 |
| ``concept_candidates`` | 記録するだけ。オントロジーの MINOR 起票は人の手作業 |

**アクションの実行**（M8-21、ADR-0014）は設計書 §9.2 の順序どおりに適用する:
前提検証（三面）→ 承認 → 実行 → ``ActionEvent`` → ``Expectation`` 生成 → 永続化。
**順序を省略しない。** 既定では契約レジストリ・信念の読み出し・期待の書き込みが配線されて
いないので、何も実行できない（失敗として記録される）——業務システムを変える経路は運用者が
明示する、という書き戻しと同じ扱いである。

**期限切れの期待の検証**（M8-27、ADR-0017）: 判断を適用する周期とは別の周期
（``SBP_REASONING_EXPECTATION_VERIFY_PERIOD_S``）で、期限を過ぎた ``Active`` の期待を検証する。
違反は不一致として起票し、判定不能は観測要求を積み、最後に期待の状態を進める。信念・期待の書き込み・
観測要求のキューが揃わなければ検証しない（``verification_unwired`` に数える）。

**概念発見**（M8-09、ADR-0019）: ``SBP_REASONING_DISCOVERY_PERIOD_S`` ごとに、解決済みの不一致の
反復（入口 (c)）から概念の提案を作る。signal の指紋で再提案を抑え（H-10）、1 周期の上限まで
LLM に提案させる。提案は ``proposed`` のまま表に書く（自動採用しない）。LLM プロバイダが無ければ
発見しない。

**照合ワーカーに相乗りしない理由。** 照合は窓を回すホットパスで、書き戻しは外部システムへの
同期呼び出しである。詰まった書き戻しが検出を止める（ADR-0012 の問い 2）。

**不一致の本体は表から読む**（M8-12、ADR-0013）。読めれば証拠 IRI を持った不一致で
``convert_resolution`` を回せるので、記録訂正の提案が出る。読めない場合（表に無い古い判断、
表を持たない実行）は**キューの行から最小の不一致を再構成して**代替し、代替したことを
戻り値とログに出す——証拠を持たないので提案が出ないことを隠さない。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from sbp.actions import (
    ActionRequest,
    ActionStage,
    Approval,
    ExecutionCommand,
    Override,
    RateLimitState,
    check_preconditions,
    expectation_for,
)
from sbp.core import vocab
from sbp.core.config import Settings, WritebackMode
from sbp.core.errors import ActionError, KnowledgeError
from sbp.core.logging import get_logger
from sbp.core.time import Interval, now
from sbp.integration.human_answer import AnswerCommand, answer_event, answer_topic
from sbp.knowledge.decisions import DecisionKind
from sbp.knowledge.quarantine import QuarantineReviewCommand, QuarantineStore
from sbp.reasoning.concept_store import to_record as concept_to_record
from sbp.reasoning.discovery import ProposalStatus
from sbp.reasoning.discovery_runner import resolution_signals, run_discovery
from sbp.reasoning.expectation import ExpectationStatus
from sbp.reasoning.expectation_runner import verify_if_overdue
from sbp.reasoning.expectation_store import from_record as expectation_from_record
from sbp.reasoning.expectation_store import to_record as expectation_to_record
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Resolution,
    ResolutionOutcome,
    Severity,
    convert_resolution,
    from_record,
)
from sbp.reasoning.reconciliation.persistence import to_record as discrepancy_to_record
from services.metrics import MetricRecorder
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime

    from psycopg import AsyncConnection

    from sbp.actions import ActionRegistry
    from sbp.core.events import EventEnvelope
    from sbp.integration.writeback import WritebackCommand
    from sbp.knowledge.concepts import ConceptProposalRecord
    from sbp.knowledge.decisions import DecisionQueue, DecisionRow
    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.knowledge.expectations import ExpectationRecord
    from sbp.knowledge.observations import ObservationRow
    from sbp.llm.base import Provider
    from sbp.reasoning.expectation import Expectation
    from sbp.reasoning.reconciliation.learning import LearningArtifacts
    from sbp.reasoning.reconciliation.severity import BusinessValueRegistry

    DiscrepancyReader = Callable[[str], Awaitable["DiscrepancyRecord | None"]]
    """``不一致 ID -> 作業項目レコード``（``DiscrepancyStore.get`` を包む）。"""
    ObservationSink = Callable[[Sequence["ObservationRow"]], Awaitable[int]]
    """観測要求を作業キューに積む（``ObservationQueue.enqueue``）。"""
    WritebackRunner = Callable[["WritebackCommand"], Awaitable[object]]
    """書き戻しを 1 件実行する（既定 dry）。"""
    BeliefReader = Callable[[str], Awaitable[dict[str, float]]]
    """``実体 IRI -> フルーエント型 IRI: 確信度``（``knowledge.current_beliefs`` を包む）。"""
    ExpectationSink = Callable[["ExpectationRecord"], Awaitable[None]]
    """期待を作業項目表に書く（``ExpectationStore.upsert``）。"""
    ProposalCurator = Callable[[str, str, int, str], Awaitable[bool]]
    """``(提案 ID, 採否, rank, 理由) -> 更新できたか``（``ConceptProposalStore.curate``）。"""
    OverdueReader = Callable[[datetime, int, frozenset[UUID]], Awaitable[list["ExpectationRecord"]]]
    """``(時刻, 件数, 除く期待) -> 期限切れの Active``（``ExpectationStore.overdue_active``）。"""
    RecordReader = Callable[[str, datetime], Awaitable[dict[str, datetime]]]
    """``(実体, 時刻) -> 記録型ごとの最初の計上時刻``（``RecordIndex.first_seen``）。"""
    DiscrepancySink = Callable[["DiscrepancyRecord"], Awaitable[None]]
    """不一致を作業項目表に書く（``DiscrepancyStore.upsert``）。"""
    EventSink = Callable[[str, "EventEnvelope"], Awaitable[object]]
    """``(トピック, イベント)`` を送る（``KafkaEventProducer.send`` を包む）。"""
    RequestReader = Callable[[str, str], Awaitable["ObservationRow | None"]]
    """``(不一致 ID, フルーエント) -> 観測要求``（``ObservationQueue.get``）。"""
    ResolvedReader = Callable[[], Awaitable[list["DiscrepancyRecord"]]]
    """解決済みの不一致を読む（``DiscrepancyStore.resolved``）。"""
    ProposalLister = Callable[[], Awaitable[list["ConceptProposalRecord"]]]
    """既存の提案を採否済みも含めて読む（``ConceptProposalStore.queue(include_curated=True)``）。"""
    ProposalSaver = Callable[["ConceptProposalRecord"], Awaitable[None]]
    """提案を作業項目表に書く（``ConceptProposalStore.upsert``）。"""
    RequestSatisfier = Callable[[str, str, datetime], Awaitable[object]]
    """``(不一致 ID, フルーエント, 時刻)`` で要求を満たす（``mark_satisfied``）。"""

__all__ = [
    "AnswerPath",
    "ConceptDiscovery",
    "ExpectationVerification",
    "LearningWorker",
    "build_worker",
    "command_id_for",
    "main",
    "reconstruct_discrepancy",
]

log = get_logger(__name__)

WORKER_NAME = "learning-worker"

_VERIFY_MAX_FAILURES = 3
"""期待の検証がこの回数失敗した行は、このプロセスの間は引かない（隔離）。

毎周期失敗する行は期限の古い順で先頭に居座り、``batch_size`` 件を占めると後ろの期待を
飢えさせる。隔離は警告ログに出し、再起動で再試行する（永続化しない: 原因の修正後に
再起動すれば戻る）。"""

_DISCOVERY_RETRY_S = 300.0
"""概念発見の依存（不一致・提案の読み出し）が一時的に失敗したときの再試行間隔（秒）。"""

_DISCOVERY_LIST_LIMIT = 5000
"""既存の提案を読む上限。超えたら警告する（古い採択済みの提案が抑制から外れる恐れ）。"""

_DEFAULT_PERIOD_S = 30.0
"""キューを引く周期（秒）。人の判断は毎秒来るものではない。"""


@dataclass(frozen=True, slots=True)
class AnswerPath:
    """人の確認回答の適用に要る依存（ADR-0018）。

    Attributes:
        events: 主張イベントの送信先。
        request: 回答が名指す観測要求の読み出し（存在の確認）。
        satisfy: 観測要求を満たす。
    """

    events: EventSink
    request: RequestReader
    satisfy: RequestSatisfier


type QuarantineReviewer = Callable[[UUID, str, datetime], Awaitable[bool]]
"""隔離の行に確認の記録を立てる（``QuarantineStore.mark_reviewed`` を包む。ADR-0035）。"""


@dataclass(frozen=True, slots=True)
class ConceptDiscovery:
    """概念発見の周期実行に要る依存（ADR-0019）。

    Attributes:
        resolved: 解決済みの不一致の読み出し（入口 (c) の入力）。
        existing: 既存の提案の読み出し（再提案の抑制）。
        save: 提案の書き込み。
        provider: LLM プロバイダ（replay / live）。
        fluents: 既存のフルーエント型のローカル名（プロンプトに渡す）。
    """

    resolved: ResolvedReader
    existing: ProposalLister
    save: ProposalSaver
    provider: Provider
    fluents: Sequence[str]


@dataclass(frozen=True, slots=True)
class ExpectationVerification:
    """期待の周期検証に要る依存（ADR-0017）。

    期待の書き込み・信念の読み出し・観測要求のキューはワーカーが別に持っている（アクションの
    実行と共有）。ここに束ねるのは検証だけが使うもの。

    Attributes:
        overdue: 期限切れの ``Active`` の読み出し。
        records: 記録の存在の読み出し（記号側の検証）。
        discrepancies: 違反の起票先。
        values: 業務価値のレジストリ（起票する不一致の深刻度）。
        entity_types: 実体 → ObjectType の対応を引く（同期。周期ごとに 1 回、別スレッドで呼ぶ）。
    """

    overdue: OverdueReader
    records: RecordReader
    discrepancies: DiscrepancySink
    values: BusinessValueRegistry
    entity_types: Callable[[], dict[str, str]]


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
        discrepancies: DiscrepancyReader | None = None,
        observations: ObservationSink | None = None,
        writeback: WritebackRunner | None = None,
        contracts: ActionRegistry | None = None,
        beliefs: BeliefReader | None = None,
        expectations: ExpectationSink | None = None,
        proposals: ProposalCurator | None = None,
        verification: ExpectationVerification | None = None,
        answers: AnswerPath | None = None,
        quarantine: QuarantineReviewer | None = None,
        discovery: ConceptDiscovery | None = None,
        metrics: MetricRecorder | None = None,
        period_s: float = _DEFAULT_PERIOD_S,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        decisions: 判断の作業キュー（``None`` なら何も適用しない）。
        discrepancies: 不一致の本体の読み出し（``None`` なら最小の不一致で代替する）。
            読めれば証拠 IRI を持つので記録訂正の提案が出る（ADR-0013）。
        observations: 観測要求の積み込み（``None`` なら積まない）。
        writeback: 書き戻しの実行（``None`` なら**提案として記録するだけ**。
            既定が ``None`` なのは意図的である——書き戻しは業務システムを変えるので、
            配線は運用者が明示する）。
        contracts: アクションの意味契約（``None`` なら実行を適用しない）。
        beliefs: 前提の物理面を見るための信念の読み出し（``None`` なら**前提は必ず落ちる**
            ——「知らないことを満たしているとみなさない」の帰結である。設計書 §9.3）。
        expectations: 期待の書き込み（``None`` なら**実行しない**。実行したのに期待を
            書けないと、効果の検証が永久にできず学習が止まる）。
        proposals: 概念提案の採否の反映（``None`` なら**提案が画面に残り続ける**。採否は
            記録されるが、提案は ``proposed`` のまま）。
        verification: 期限切れの期待の検証（``None`` なら検証しない。期待は ``Active`` のまま
            期限を過ぎてもボードに残る。所見 103）。``beliefs`` / ``expectations`` /
            ``observations`` も要る。
        answers: 人の確認回答の適用（``None`` なら**回答を適用しない**——失敗として記録される。
            送れない回答を適用済みにすると、人の確認が信念に届かないまま消える）。
        discovery: 概念発見（``None`` なら発見しない。UI の概念 curation は空のまま。
            ADR-0019）。LLM プロバイダが無ければ ``build_worker`` は ``None`` を渡す。
        metrics: 指標の記録（``None`` なら測らない）。解決の適用で ``resolution_time`` /
            ``reliability_updates`` / ``learning_yield`` を書く（M8-15）。
        period_s: キューを引く周期（秒）。
        """
        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME,
            settings=self.settings,
            checks=readiness_checks(self.settings),
        )
        self._decisions = decisions
        self._discrepancies = discrepancies
        self._observations = observations
        self._writeback = writeback
        self._contracts = contracts
        self._beliefs = beliefs
        self._expectations = expectations
        self._proposals = proposals
        self._verification = verification
        self._answers = answers
        self._quarantine = quarantine
        self._discovery = discovery
        self._metrics = metrics
        self.resolved_total = 0
        self.productive_total = 0
        self._last_discover: datetime | None = None
        self._unproductive: dict[str, int] = {}
        self.proposed_total = 0
        self.discovery_failed_total = 0
        if discovery is None:
            log.info(
                "worker.discovery_not_wired",
                worker=WORKER_NAME,
                detail="概念発見をしない（LLM プロバイダが無い、または配線されていない）",
            )
        self._closers: list[Callable[[], Awaitable[None]]] = []
        self._last_verify: datetime | None = None
        self._verify_failures: dict[UUID, int] = {}
        self.verified_total = 0
        self.verify_failed_total = 0
        # **プロセス内にしか無い。** 再起動で消えるので、冪等の鍵が防ぐのは「同じ判断の
        # 二度適用」だけで、「別の判断で短時間に 2 回」は再起動後に通る（ADR-0014 の未決 1）
        self._rate_limits = RateLimitState()
        self.period = timedelta(seconds=period_s)
        self._last_poll: datetime | None = None
        self.applied_total = 0
        self.failed_total = 0
        self.verification_unwired: tuple[str, ...] = tuple(
            name
            for name, given in (
                ("verification", verification),
                ("beliefs", beliefs),
                ("expectations", expectations),
                ("observations", observations),
            )
            if given is None
        )
        if self.verification_unwired:
            log.info(
                "worker.expectation_verification_not_wired",
                worker=WORKER_NAME,
                missing=list(self.verification_unwired),
                detail="期限切れの期待を検証しない（Active のまま期限を過ぎてボードに残る）",
            )
        if decisions is None:
            log.warning(
                "worker.no_decision_queue",
                worker=WORKER_NAME,
                detail="判断のキューが無いので何も適用しない",
            )
        if contracts is None:
            log.info(
                "worker.actions_not_wired",
                worker=WORKER_NAME,
                detail="契約レジストリが無いのでアクションを実行しない（承認は記録される）",
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
        """1 ループ。未適用の判断を適用し、期限切れの期待を検証する。

        Returns:
            適用できた判断の件数と、検証した期待の件数の合計。
        """
        applied = await self._apply_decisions()
        total = applied + await self._verify_overdue() + await self._discover()
        if self._metrics is not None:
            await self._metrics.flush(at=now())
        return total

    @property
    def unproductive_signals(self) -> dict[str, int]:
        """提案にならなかった signal の指紋と回数（次の周期で順位を下げる。プロセス内のみ）。"""
        return dict(self._unproductive)

    def discover_due(self, *, at: datetime) -> bool:
        """この時点で概念発見を回すべきか。

        Args:
            at: 判定時刻。
        """
        if self._last_discover is None:
            return True
        return (at - self._last_discover).total_seconds() >= (
            self.settings.reasoning.discovery_period_s
        )

    async def _discover(self) -> int:
        """解決済みの不一致から概念を発見し、提案を表に書く（M8-09、ADR-0019）。

        入口は (c) 反復する不一致だけ（(a)(b) の入力は運用に無い）。signal を指紋で抑え、上限まで
        LLM に提案させる。**提案は ``PROPOSED`` のまま**（自動採用しない。不変条件 11）。
        LLM は同期の呼び出しなので別スレッドで回す。依存の障害・提案 1 件の書き込み失敗で
        ループを止めない。

        Returns:
            表に書いた提案の件数。
        """
        d = self._discovery
        if d is None:
            return 0
        at = now()
        if not self.discover_due(at=at):
            return 0
        self._last_discover = at
        reasoning = self.settings.reasoning
        try:
            resolved = await d.resolved()
            signals = resolution_signals(resolved, min_repeats=reasoning.discovery_min_repeats)
            if not signals:
                return 0
            existing = await d.existing()
            report = await asyncio.to_thread(
                run_discovery,
                signals,
                existing=existing,
                provider=d.provider,
                at=at,
                cooldown=timedelta(days=reasoning.discovery_rejection_cooldown_days),
                max_proposals=reasoning.discovery_max_proposals_per_cycle,
                existing_fluents=d.fluents,
                unproductive_counts=self._unproductive,
            )
        except Exception as exc:  # 依存の一時的な障害でワーカーのループを止めない
            self.discovery_failed_total += 1
            # 次の周期（既定 1 日）まで待たず、短い間隔で再試行する。読み出しの一時的な障害で
            # 1 日分の発見を失わない
            retry = min(reasoning.discovery_period_s, _DISCOVERY_RETRY_S)
            self._last_discover = at - timedelta(seconds=reasoning.discovery_period_s - retry)
            log.warning("worker.discovery_unavailable", worker=WORKER_NAME, error=str(exc))
            return 0
        for fingerprint in report.unproductive:
            self._unproductive[fingerprint] = self._unproductive.get(fingerprint, 0) + 1
        self.discovery_failed_total += report.failed
        written = 0
        for proposal in report.proposals:
            try:
                await d.save(concept_to_record(proposal))
                written += 1
            except Exception as exc:  # 1 件の失敗で残りを止めない。次の周期で再提案される
                self.discovery_failed_total += 1
                log.warning(
                    "worker.discovery_save_failed",
                    worker=WORKER_NAME,
                    correlation_id=str(proposal.proposal_id),
                    error=str(exc),
                )
        self.proposed_total += written
        return written

    def verify_due(self, *, at: datetime) -> bool:
        """この時点で期待の検証を回すべきか。

        Args:
            at: 判定時刻。
        """
        if self._last_verify is None:
            return True
        period = self.settings.reasoning.expectation_verify_period_s
        return (at - self._last_verify).total_seconds() >= period

    async def _verify_overdue(self) -> int:
        """期限切れの ``Active`` を検証して状態を進める（M8-27、ADR-0017）。

        **書く順序**: 不一致・観測要求を先に、期待の状態を最後に書く。状態を先に進めると、
        副作用の書き込みに失敗したとき期待が ``Active`` でなくなり、再試行の対象から外れて
        違反が学習信号に出ない。副作用は冪等（不一致の ID は期待から決まり、観測要求は
        ``(不一致, フルーエント)`` で 1 行）なので、状態の書き込みが失敗しても再実行できる。

        1 件の失敗で残りを止めない（ログに残して次へ）。

        Returns:
            検証して状態を進めた期待の件数。
        """
        v = self._verification
        if (
            v is None
            or self._beliefs is None
            or self._expectations is None
            or self._observations is None
        ):
            # 観測要求を積めないと、判定不能の期待が要求を残さず終わる。配線漏れは起動時に数える
            return 0
        at = now()
        if not self.verify_due(at=at):
            return 0
        self._last_verify = at

        quarantined = frozenset(
            item for item, count in self._verify_failures.items() if count >= _VERIFY_MAX_FAILURES
        )
        try:
            rows = await v.overdue(at, self.settings.reasoning.batch_size, quarantined)
            if not rows:
                return 0
            types = await asyncio.to_thread(v.entity_types)
        except Exception as exc:  # 依存の一時的な障害でワーカーのループを止めない
            self.verify_failed_total += 1
            log.warning("worker.expectation_verify_unavailable", worker=WORKER_NAME, error=str(exc))
            return 0
        verified = 0
        for row in rows:
            try:
                expectation = expectation_from_record(row)
                beliefs = await self._beliefs(expectation.target)
                present = await v.records(expectation.target, expectation.created_at)
                verdict = verify_if_overdue(
                    expectation,
                    beliefs=beliefs,
                    first_records=present,
                    at=at,
                    severity_for=lambda item: self._severity_of(item, types=types, at=at),
                )
                if verdict is None:
                    continue
                if verdict.discrepancy is not None:
                    try:
                        await v.discrepancies(discrepancy_to_record(verdict.discrepancy))
                    except KnowledgeError:
                        # 同じ不一致が既に先へ進んでいる（人が振り分けた等）。
                        # **記録済みとして続ける**——止めると期待が Active のまま失敗し続ける
                        log.info(
                            "worker.expectation_discrepancy_already_recorded",
                            worker=WORKER_NAME,
                            discrepancy=str(verdict.discrepancy.discrepancy_id),
                        )
                if verdict.observation_requests and self._observations is not None:
                    await self._observations(verdict.observation_requests)
                await self._expectations(expectation_to_record(verdict.expectation))
                verified += 1
                log.info(
                    "worker.expectation_verified",
                    worker=WORKER_NAME,
                    correlation_id=str(expectation.expectation_id),
                    entity=expectation.target,
                    status=verdict.expectation.status.value,
                    discrepancy=str(verdict.discrepancy.discrepancy_id)
                    if verdict.discrepancy
                    else None,
                    observation_requests=len(verdict.observation_requests),
                )
            except Exception as exc:  # 1 件の失敗で残りを止めない。次の周期で再試行する
                self.verify_failed_total += 1
                failures = self._verify_failures.get(row.expectation_id, 0) + 1
                self._verify_failures[row.expectation_id] = failures
                log.warning(
                    "worker.expectation_verify_failed",
                    worker=WORKER_NAME,
                    correlation_id=str(row.expectation_id),
                    failures=failures,
                    quarantined=failures >= _VERIFY_MAX_FAILURES,
                    error=str(exc),
                )
        self.verified_total += verified
        return verified

    def _severity_of(
        self, expectation: Expectation, *, types: dict[str, str], at: datetime
    ) -> Severity:
        """期待の対象の型の業務価値から深刻度を作る（起票する不一致・観測要求の効用）。

        確信度は判定の種類による: 違反は期待区間の外という明確な判定（1.0）、判定不能は
        中間（0.5）。持続は期待の作成から検証までの長さ。型が引けなければ、照合の文脈と同じく
        実体自身を型とみなし、登録の無い型の既定（基準額の半分）に落ちる。
        """
        from sbp.reasoning.reconciliation.severity import compute_severity  # noqa: PLC0415

        assert self._verification is not None
        values = self._verification.values
        confidence = 1.0 if expectation.status is ExpectationStatus.VIOLATED else 0.5
        return compute_severity(
            value=values.get(types.get(expectation.target, expectation.target)),
            confidence=confidence,
            persistence=at - expectation.created_at,
            value_scale=values.scale,
            persistence_scale=timedelta(
                seconds=self.settings.reasoning.severity_persistence_scale_s
            ),
        )

    async def _apply_decisions(self) -> int:
        """未適用の判断を適用する。

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
            return await self._apply_curation(row)
        if row.kind is DecisionKind.ACTION:
            return await self._apply_action(row, at=at)
        if row.kind is DecisionKind.ANSWER:
            return await self._apply_answer(row)
        if row.kind is DecisionKind.QUARANTINE_REVIEW:
            return await self._apply_quarantine_review(row, at=at)
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
        discrepancy, source = await self._load_discrepancy(row, resolution=resolution, at=at)
        artifacts = convert_resolution(
            discrepancy,
            resolution=resolution,
            corrections=row.payload.get("corrections") or {},
            at=at,
        )
        enqueued = await self._enqueue_observations(artifacts, site=self.settings.site_id)
        written = await self._run_writebacks(artifacts)
        summary = {
            "outcome": resolution.outcome.value,
            # 本体を読めたか。**読めなかったことを隠さない**——証拠を持たない不一致からは
            # 記録訂正の提案が出ないので、「訂正が 0 件」の理由が分かる必要がある
            "discrepancy_source": source,
            "observation_requests_enqueued": enqueued,
            "writebacks_run": written,
            # **記録するだけのもの。** 件数を残すことで「何が適用されずに残っているか」が
            # UI から読める（ADR-0012 の問い 3）
            "reliability_updates_recorded": len(artifacts.reliability_updates),
            "bags_recorded": len(artifacts.bags),
            "concept_candidates_recorded": len(artifacts.concept_candidates),
            "writeback_mode": self.settings.writeback_mode.value,
        }
        self._measure_resolution(discrepancy, resolution, artifacts, enqueued)
        log.info(
            "worker.resolution_applied",
            worker=WORKER_NAME,
            decision_id=row.decision_id,
            correlation_id=row.subject,
            **summary,
        )
        return summary

    def _measure_resolution(
        self,
        discrepancy: Discrepancy,
        resolution: Resolution,
        artifacts: LearningArtifacts,
        enqueued: int,
    ) -> None:
        """解決の適用を指標にする（M8-15）。``learning_yield`` は累計の割合。

        **測定の失敗で判断を失敗にしない。** ここは副作用（観測要求・書き戻し）の後なので、
        例外が出ると判断が再試行されて副作用が重複する。測定時刻は判断ごとに取る——同じ周期の
        標本が同じ ``(名前, 時刻, ラベル)`` で上書きし合わないように。

        学習信号にならなかった解決（記録訂正も負例も概念候補も観測要求も出なかったもの）が
        分母に入るので、「解決しても何も学ばない」運用が割合に出る。
        """
        try:
            self._record_resolution(discrepancy, resolution, artifacts, enqueued, at=now())
        except Exception:
            log.exception("worker.resolution_metrics_failed", worker=WORKER_NAME)

    def _record_resolution(
        self,
        discrepancy: Discrepancy,
        resolution: Resolution,
        artifacts: LearningArtifacts,
        enqueued: int,
        *,
        at: datetime,
    ) -> None:
        self.resolved_total += 1
        productive = bool(
            artifacts.reliability_updates
            or artifacts.bags
            or artifacts.concept_candidates
            or enqueued
        )
        self.productive_total += int(productive)
        if self._metrics is None:
            return
        labels = {"kind": str(int(discrepancy.kind))}
        self._metrics.record(
            "resolution_time",
            max((resolution.resolved_at - discrepancy.detected_at).total_seconds(), 0.0),
            at=at,
            labels=labels,
        )
        self._metrics.record(
            "reliability_updates", float(len(artifacts.reliability_updates)), at=at, labels=labels
        )
        self._metrics.record("learning_yield", self.productive_total / self.resolved_total, at=at)

    async def _load_discrepancy(
        self, row: DecisionRow, *, resolution: Resolution, at: datetime
    ) -> tuple[Discrepancy, str]:
        """不一致の本体を表から読む（無ければ最小の不一致で代替）。

        **読めなかったことを戻り値に出す。** 代替した不一致は証拠 IRI を持たないので記録訂正の
        提案が出ない——「訂正が 0 件」の理由が「訂正不要」なのか「証拠が無い」のか区別できる
        必要がある。

        Args:
            row: 判断。
            resolution: 解決の記録。
            at: 処理時刻。

        Returns:
            ``(不一致, 由来)``。由来は ``"store"``（表から読めた）か
            ``"reconstructed"``（代替した）。
        """
        if self._discrepancies is not None:
            record = await self._discrepancies(row.subject)
            if record is not None:
                return from_record(record), "store"
            log.warning(
                "worker.discrepancy_not_found",
                worker=WORKER_NAME,
                correlation_id=row.subject,
                detail="表に本体が無い。最小の不一致で代替する（記録訂正の提案は出ない）",
            )
        return reconstruct_discrepancy(row, resolution=resolution, at=at), "reconstructed"

    async def _apply_curation(self, row: DecisionRow) -> dict[str, Any]:
        """概念提案の採否を記録し、**提案の状態を進める**（M8-19、ADR-0015）。

        **採択してもオントロジーには触らない**（M6-04 の決定）。MINOR 版の起票は人の手作業で、
        自動で語彙を増やさない。

        **状態を進めるのがここの仕事である。** 初版は記録するだけで、採否を入れても提案が
        ``proposed`` のまま残り、**同じ提案が永久に curation の画面に出続けていた**。

        **本体を読み戻さずに更新する。** 読み戻すと重心を欠いた提案を書き直すことになり、
        payload が劣化する（ADR-0015 の往復の非対称性）。

        Args:
            row: 判断（``kind=curation``）。

        Returns:
            適用の要約。
        """
        accepted = bool(row.payload.get("accepted"))
        note = str(row.payload.get("reason", ""))
        status = ProposalStatus.ACCEPTED if accepted else ProposalStatus.REJECTED
        curated = False
        if self._proposals is not None:
            curated = await self._proposals(row.subject, status.value, status.rank, note)
            if not curated:
                # 行が無い、または既に採否が入っている。**例外にしない**——人の判断は
                # 失わせず、「提案が見つからなかった」ことを要約に残す
                log.warning(
                    "worker.proposal_not_updated",
                    worker=WORKER_NAME,
                    correlation_id=row.subject,
                    detail="提案が表に無い、または既に採否が入っている",
                )
        summary = {
            "accepted": accepted,
            "proposal_id": row.subject,
            "status": status.value,
            # 表の状態を進められたか（偽なら提案が画面に残り続ける）
            "proposal_updated": curated,
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

    def add_closer(self, closer: Callable[[], Awaitable[None]]) -> None:
        """終了時に呼ぶ後始末を足す（``build_worker`` が持つ producer の停止など）。

        Args:
            closer: 非同期の後始末。
        """
        self._closers.append(closer)

    async def aclose(self) -> None:
        """後始末を呼ぶ（失敗しても残りを呼ぶ）。"""
        for closer in reversed(self._closers):
            try:
                await closer()
            except Exception as exc:  # 停止処理の失敗で他の後始末を止めない
                log.warning("worker.close_failed", worker=WORKER_NAME, error=str(exc))

    async def _apply_quarantine_review(self, row: DecisionRow, *, at: datetime) -> dict[str, Any]:
        """隔離の確認を隔離の行に反映する（M10-02、ADR-0035）。

        **無い・確認済み・他拠点の隔離への確認は失敗させる**（誤った識別子の可能性が高く、
        黙って成功にすると「確認した」と読める）。

        Args:
            row: 確認の判断。
            at: 処理時刻。

        Returns:
            何を適用したかの要約。

        Raises:
            ValueError: 確認を反映する依存が無い、または隔離の行が未確認で存在しない場合。
        """
        if self._quarantine is None:
            raise ValueError(
                "cannot apply a quarantine review: the quarantine store is not wired; "
                "marking it done would leave the row unreviewed"
            )
        command = QuarantineReviewCommand.model_validate(row.payload)
        reviewed = await self._quarantine(
            command.quarantine_id, command.resolution, row.received_at or at
        )
        if not reviewed:
            raise ValueError(
                f"quarantine {command.quarantine_id} is unknown, already reviewed, or belongs to "
                "another site"
            )
        log.info(
            "worker.quarantine_reviewed",
            worker=WORKER_NAME,
            correlation_id=command.decision_id,
            quarantine_id=str(command.quarantine_id),
        )
        return {"quarantine_id": str(command.quarantine_id), "resolution": command.resolution}

    async def _apply_answer(self, row: DecisionRow) -> dict[str, Any]:
        """人の確認回答を主張イベントにして送り、観測要求を満たす（M8-29、ADR-0018）。

        **順序は 送信 → 充足。** 送信が失敗すれば要求は満たされず、判断は失敗として残る。
        送信が成功して充足の書き込みが失敗した場合も判断は失敗として残り（自動では再試行しない。
        ADR-0012 の未決）、要求は未充足のままなので、スケジューラが同じ質問を再び選びうる。
        人が同じ ``decision_id`` で再投入すれば、同じ ``event_id`` のイベントが送られ、消費側の
        冪等記録が弾く。

        **キューに無い要求への回答は失敗させる**（誤送信の可能性が高い）。満たされた要求への
        回答は受ける（訂正の回答がありうる。充足は冪等）。

        Args:
            row: 回答の判断。

        Returns:
            何を適用したかの要約。

        Raises:
            ValueError: 回答を送る依存が無い、要求がキューに無い、実体が要求と食い違う場合。
        """

        if self._answers is None:
            raise ValueError(
                "cannot apply an answer: the answer path is not wired (no event sink); "
                "applying it would mark the answer done while it never reached the belief"
            )
        command = AnswerCommand.model_validate(row.payload)
        request = await self._answers.request(command.discrepancy_id, command.fluent)
        if request is None:
            raise ValueError(
                f"no observation request ({command.discrepancy_id}, {command.fluent}); "
                "an answer to an unknown request is likely misdirected"
            )
        if request.entity != command.entity:
            raise ValueError(
                f"answer entity {command.entity} differs from the request's {request.entity}"
            )
        reasoning = self.settings.reasoning
        received = row.received_at or now()
        event = answer_event(
            site_id=self.settings.site_id,
            decision_id=command.decision_id,
            entity=command.entity,
            fluent=command.fluent,
            state=command.state,
            answered_by=command.answered_by,
            at=received,
            confidence=reasoning.human_answer_confidence,
            uncertainty_s=reasoning.human_answer_uncertainty_s,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        await self._answers.events(answer_topic(self.settings.site_id), event)
        await self._answers.satisfy(command.discrepancy_id, command.fluent, received)
        log.info(
            "worker.answer_applied",
            worker=WORKER_NAME,
            correlation_id=command.decision_id,
            entity=command.entity,
            fluent=command.fluent,
            state=command.state,
            event_id=str(event.event_id),
        )
        return {
            "event_id": str(event.event_id),
            "state": command.state,
            "request": f"{command.discrepancy_id}:{command.fluent}",
        }

    async def _trusted_severity(self, command: ExecutionCommand) -> float:
        """承認の要否の判定に使う深刻度（クライアントの申告を下げる方向に信じない）。

        コマンドの ``severity`` は呼び出し側の申告で、``0`` を送れば ``severity_at_least_high`` の
        契約で承認の記録が作られない（M8-22 のレビュー）。起因の不一致（``correlation_id``）が
        表で読めるなら、その深刻度を下限にする。読めなければ申告をそのまま使い、ログに残す。

        Args:
            command: 実行要求。
        """
        if command.correlation_id is None or self._discrepancies is None:
            return command.severity
        record = await self._discrepancies(str(command.correlation_id))
        if record is None:
            log.warning(
                "worker.action_discrepancy_not_found",
                worker=WORKER_NAME,
                correlation_id=str(command.correlation_id),
                detail="起因の不一致が表に無い。深刻度は申告のまま使う",
            )
            return command.severity
        return max(command.severity, record.severity_score)

    async def _apply_action(self, row: DecisionRow, *, at: datetime) -> dict[str, Any]:
        """承認されたアクションを実行する（設計書 §9.2、ADR-0014）。

        **順序を省略しない**: 前提検証（三面）→ 承認 → 実行 → ``ActionEvent`` →
        ``Expectation`` 生成 → 永続化。

        **期待を書けなければ実行しない。** 実行したのに期待を書けないと、効果の検証が永久に
        できず学習が止まる——「実行はしたが何も確かめられない」状態を作らない。だから配線の
        検査を実行の**前**に置く。

        Args:
            row: 判断（``ExecutionCommand`` の内容が ``payload``）。
            at: 処理時刻。

        Returns:
            何を適用したかの要約。

        Raises:
            ActionError: 配線が無い、契約が無い、前提が満たせない、レート制限に当たる場合。
                キューの ``error`` に残り、``failed_at`` が立つ。**自動では再試行されない**
                （``pending`` は ``failed_at IS NULL`` の行だけを引く）。前提は後で満たされうるが、
                人が理由を読んで新しい判断として再投入する（ADR-0021）。
        """
        command = ExecutionCommand.model_validate(row.payload)
        if self._contracts is None or self._expectations is None:
            raise ActionError(
                f"cannot execute {command.action_type}: the contract registry or the expectation "
                "store is not wired; actions change the business system so the path is explicit"
            )
        contract = self._contracts.get(command.action_type)
        severity = await self._trusted_severity(command)

        # 前提の物理面。**読めなければ空で渡す**——`check_preconditions` が「信念が無い」を
        # 未達として扱う（知らないことを満たしているとみなさない。設計書 §9.3）
        beliefs = {} if self._beliefs is None else await self._beliefs(command.target)
        report = check_preconditions(contract, target=command.target, beliefs=beliefs)

        request = ActionRequest(
            contract=contract,
            target=command.target,
            requested_by=command.approved_by,
            severity=severity,
            # **冪等の鍵を判断から導く。** 既定は `uuid4` で、再試行ごとに別の値になる
            # ——部分的に失敗した実行を再試行すると、業務システムが 2 つの命令を見る
            command_id=command_id_for(row.decision_id),
            correlation_id=command.correlation_id,
        )
        override = (
            None
            if command.override_reason is None
            else Override(
                reason=command.override_reason,
                overridden_by=command.approved_by,
                overridden_at=at,
                failed_faces=report.failed_faces(),
            )
        )
        stage = request.check(report, override=override)
        if stage is ActionStage.REJECTED:
            raise ActionError(
                f"{command.action_type} on {command.target} failed its preconditions: "
                f"{report.explain()}"
            )

        # **このコマンドが承認そのものである**（ADR-0014）。承認が不要な契約でも
        # `approve` を通す——段階を飛ばすと実行が段階検査に落ちる
        request.approve(
            Approval(
                role=contract.approvals.role,
                approved_by=command.approved_by,
                approved_at=at,
                note=command.reason,
            )
            if contract.requires_approval(severity=severity)
            else None
        )
        event = request.execute(
            at=at, executed_by=command.approved_by, rate_limits=self._rate_limits
        )

        expectation = expectation_for(
            contract,
            event,
            ontology_version=vocab.ONTOLOGY_VERSION,
            record_effect_floor=self.settings.actions.record_effect_floor,
            note=command.reason,
        )
        await self._expectations(expectation_to_record(expectation))

        summary = {
            "kind": "action",
            "action_type": command.action_type,
            "target": command.target,
            "action_event": str(event.event_id),
            "expectation_id": str(expectation.expectation_id),
            "overridden": event.was_overridden,
            "symbolic_effects": len(expectation.effects_of("symbolic")),
            "rollout_effects": len(expectation.effects_of("rollout")),
            "beliefs_seen": len(beliefs),
        }
        log.info(
            "worker.action_executed",
            worker=WORKER_NAME,
            correlation_id=str(command.correlation_id) if command.correlation_id else None,
            event_id=str(event.event_id),
            entity=command.target,
            decision_id=row.decision_id,
            **{key: summary[key] for key in ("action_type", "expectation_id", "overridden")},
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


_COMMAND_NAMESPACE = UUID("6f9c1f2e-2a3b-4c5d-8e7f-0a1b2c3d4e5f")
"""``decision_id`` から冪等の ``command_id`` を導く名前空間（ADR-0014）。

固定値である必要がある——変えると、過去の判断を再試行したときに別の命令として扱われる。
"""


def command_id_for(decision_id: str) -> UUID:
    """判断の識別子から決定的な冪等の鍵を作る。

    **再試行で同じ値になることが要点である。** `ActionRequest.command_id` の既定は `uuid4` で、
    適用が部分的に失敗して再試行されると別の命令になり、業務システムが 2 回変わる。

    Args:
        decision_id: 判断の識別子（UI が生成した冪等の鍵）。
    """
    return uuid5(_COMMAND_NAMESPACE, decision_id)


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


def quarantine_reviewer(connection: AsyncConnection, site_id: str) -> QuarantineReviewer:
    """隔離の確認を反映する関数（``QuarantineStore.mark_reviewed`` を包む）。

    Args:
        connection: Postgres 接続。
        site_id: 拠点識別子。
    """
    store = QuarantineStore(connection, site=site_id)

    async def review(event_id: UUID, resolution: str, at: datetime) -> bool:
        """隔離の行に確認の記録を立てる。"""
        return await store.mark_reviewed(event_id, resolution=resolution, at=at)

    return review


async def build_worker(
    settings: Settings | None = None, *, site_id: str
) -> tuple[LearningWorker, DecisionQueue]:
    """実運用の配線を組む（判断キュー → 学習信号 → 観測要求キュー / アクション実行）。

    **書き戻しは配線しない。** 業務システムを変える経路は運用者が明示する
    （``SBP_WRITEBACK_MODE=live`` だけでは足りない——コネクタの資格情報が要る）。

    **アクションの実行は配線する**（ADR-0014）。契約（``actions/*.yaml``）・信念の読み出し・
    期待の書き込みを繋ぐ。**実行自体が業務システムを変えるのは書き戻しを経由したときだけ**で、
    それは上記のとおり配線していない——いまは ``ActionEvent`` と ``Expectation`` が出るだけである。

    Args:
        settings: 設定（既定は環境から読む）。
        site_id: 拠点識別子。

    Returns:
        ``(ワーカー, 判断キュー)``。
    """
    import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

    from sbp.actions import ActionRegistry  # noqa: PLC0415
    from sbp.eventlog.producer import KafkaEventProducer  # noqa: PLC0415
    from sbp.knowledge import (  # noqa: PLC0415
        AssertionStore,
        ConceptProposalStore,
        DecisionQueue,
        DiscrepancyStore,
        ExpectationStore,
        HttpGraphStore,
        MetricStore,
        ObservationQueue,
        RecordIndex,
        current_beliefs,
        most_specific_types,
    )
    from sbp.reasoning.reconciliation.severity import BusinessValueRegistry  # noqa: PLC0415

    resolved = settings or Settings()
    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
    decisions = DecisionQueue(connection, site=site_id)
    observations = ObservationQueue(connection, site=site_id)
    store = DiscrepancyStore(connection, site=site_id)
    expectations = ExpectationStore(connection, site=site_id)
    proposals = ConceptProposalStore(connection, site=site_id)
    contracts = ActionRegistry.from_directory(Path("actions"))
    graph = HttpGraphStore(resolved)
    assertions = AssertionStore(graph, site_id=site_id)
    values = BusinessValueRegistry.from_directory(
        Path("value"), default_scale=resolved.reasoning.severity_value_scale
    )
    records = RecordIndex(connection, site=site_id)

    async def read_beliefs(entity: str) -> dict[str, float]:
        """実体の信念を読む（前提の物理面）。

        SPARQL は同期なので別スレッドに逃がす——ワーカーのループを止めない。

        Args:
            entity: 対象実体の IRI。
        """
        return await asyncio.to_thread(current_beliefs, assertions, entity=entity)

    async def curate_proposal(proposal_id: str, status: str, rank: int, note: str) -> bool:
        """概念提案の採否を表に反映する。

        Args:
            proposal_id: 提案の識別子。
            status: 新しい採否の値。
            rank: その採否の `ProposalStatus.rank`。
            note: 採否の理由。
        """
        return await proposals.curate(proposal_id, status=status, rank=rank, note=note)

    async def write_expectation(record: ExpectationRecord) -> None:
        """期待を作業項目表に書く。

        Args:
            record: 期待のレコード。
        """
        await expectations.upsert(record)

    async def overdue(
        at: datetime, limit: int, exclude: frozenset[UUID]
    ) -> list[ExpectationRecord]:
        """期限切れの ``Active`` を引く（隔離した行を除く）。"""
        return await expectations.overdue_active(at=at, limit=limit, exclude=exclude)

    async def present_records(entity: str, since: datetime) -> dict[str, datetime]:
        """期待の作成後に対象へ記録のある記録型を引く（記号側の検証）。"""
        return await records.first_seen(entity, since=since)

    def entity_types() -> dict[str, str]:
        """実体の型を知識グラフから引く（深刻度の業務価値）。"""
        return most_specific_types(graph, types=values.object_types, site=site_id)

    verification = ExpectationVerification(
        overdue=overdue,
        records=present_records,
        discrepancies=store.upsert,
        values=values,
        entity_types=entity_types,
    )

    discovery = _build_discovery(resolved, store=store, proposals=proposals)
    producer = KafkaEventProducer(resolved)
    await producer.start()

    async def send_event(topic: str, event: EventEnvelope) -> object:
        """主張イベントを送ってから戻る（送信の後に要求を満たすため、待つ）。"""
        return await producer.send(topic, event)

    async def get_request(discrepancy_id: str, fluent: str) -> ObservationRow | None:
        """回答が名指す観測要求を引く。"""
        return await observations.get(discrepancy_id=discrepancy_id, fluent=fluent)

    async def satisfy_request(discrepancy_id: str, fluent: str, at: datetime) -> object:
        """観測要求を満たす。"""
        return await observations.mark_satisfied(
            discrepancy_id=discrepancy_id, fluent=fluent, at=at
        )

    try:
        worker = LearningWorker(
            resolved,
            decisions=decisions,
            discrepancies=store.get,
            observations=observations.enqueue,
            contracts=contracts,
            beliefs=read_beliefs,
            expectations=write_expectation,
            proposals=curate_proposal,
            verification=verification,
            answers=AnswerPath(events=send_event, request=get_request, satisfy=satisfy_request),
            quarantine=quarantine_reviewer(connection, site_id),
            discovery=discovery,
            metrics=MetricRecorder(site=site_id, sink=MetricStore(connection, site=site_id).write),
        )
    except BaseException:
        # 組み立てに失敗したら producer を残さない（後始末はワーカーが持つので、ここだけ自前）
        await producer.stop()
        raise
    worker.add_closer(producer.stop)
    log.info(
        "worker.wired",
        worker=WORKER_NAME,
        site_id=site_id,
        action_types=len(contracts.types()),
    )
    return worker, decisions


def _build_discovery(settings: Settings, *, store: Any, proposals: Any) -> ConceptDiscovery | None:
    """概念発見の依存を組む。LLM プロバイダが作れなければ ``None``（発見しない）。

    **replay は fixture が無ければ呼ぶたびに失敗する**（黙って live に落ちない）ので、運用の既定
    （``SBP_LLM_MODE=replay``）では fixture のある signal だけが提案になる。``live`` / ``record`` は
    anthropic の live プロバイダ（H-04 の決定、ADR-0022）を使い、**鍵・モデルが無ければ組めない**
    （``LlmError`` → 発見しない。黙って replay に落ちない）。

    Args:
        settings: 設定。
        store: 不一致の作業項目表（``DiscrepancyStore``）。
        proposals: 概念提案の作業項目表（``ConceptProposalStore``）。
    """
    from sbp.core.errors import LlmError  # noqa: PLC0415 — 実運用の配線でのみ使う
    from sbp.llm import build_provider  # noqa: PLC0415

    try:
        provider = build_provider(settings)
    except LlmError as exc:
        log.warning("worker.discovery_no_provider", worker=WORKER_NAME, error=str(exc))
        return None

    async def existing() -> list[ConceptProposalRecord]:
        """既存の提案を採否済みも含めて読む（再提案の抑制）。"""
        found = await proposals.queue(limit=_DISCOVERY_LIST_LIMIT, include_curated=True)
        if len(found) >= _DISCOVERY_LIST_LIMIT:
            # 古い採択済み・判断待ちの提案が窓から落ちると、その signal が再提案される
            log.warning(
                "worker.discovery_existing_truncated",
                worker=WORKER_NAME,
                limit=_DISCOVERY_LIST_LIMIT,
                detail="再提案の抑制が一部効かない。保持窓か専用の問い合わせを検討する",
            )
        return found

    fluents = sorted(iri.rsplit("#", 1)[-1] for iri in vocab.FLUENT_TYPES)
    return ConceptDiscovery(
        resolved=store.resolved,
        existing=existing,
        save=proposals.upsert,
        provider=provider,
        fluents=fluents,
    )


def main() -> int:
    """エントリポイント。

    判断は ``learning_decisions`` 表から取る（ADR-0012）。**書き戻しは未配線**で、記録訂正は
    提案として記録されるだけである。
    """

    async def _run() -> None:
        settings = Settings()
        worker, _queue = await build_worker(settings, site_id=settings.site_id)
        try:
            await worker.run()
        finally:
            await worker.aclose()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
