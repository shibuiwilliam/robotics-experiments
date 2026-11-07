"""概念発見の周期実行の部品（M8-09、ADR-0019）。

発見の入口と抑制の純粋な部分である。永続化も LLM も持たない——learning-worker が解決済みの不一致と
既存の提案を渡し、結果（提案）を表に書く。

**運用の入口は (c) 反復する不一致だけ。** (a) 低被覆クラスタは潜在（M8-24）、(b) 整合残差の持続は
残差の指標（M8-15）が要り、どちらの入力も運用に無い。入力の空の入口を空のまま動かすと「動いている」
ように見えて何も出ない（所見 32 の形）ので、signal の供給は呼び出し側が差し込む形にしてある。

**再提案の抑制は signal の指紋で行う**（H-10、ADR-0019）。指紋は
``(trigger, 実体の集合, 証拠の種別)`` で、**件数のような時間とともに変わる値を含めない**
（反復不一致の証拠は ``kind2:5回`` だが、指紋に使うのは ``kind2``。件数を含めると、解決が 1 件増える
たびに別の signal になり抑制が効かない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core.errors import LlmError, ReasoningError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc
from sbp.llm.base import LlmTask
from sbp.llm.registry import PromptRegistry, load_routing
from sbp.reasoning.concept_store import from_record
from sbp.reasoning.discovery import (
    ConceptProposal,
    ProposalStatus,
    repeated_discrepancy_signals,
)
from sbp.reasoning.discovery_prompt import PROPOSAL_SCHEMA, ProposalOutput, render_messages

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sbp.knowledge.concepts import ConceptProposalRecord
    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.llm.base import Provider
    from sbp.reasoning.discovery import DiscoverySignal

__all__ = [
    "DiscoveryReport",
    "resolution_signals",
    "run_discovery",
    "signal_fingerprint",
    "suppressed_fingerprints",
]

log = get_logger(__name__)

_ALWAYS_SUPPRESSING = frozenset(
    {ProposalStatus.PROPOSED, ProposalStatus.ACCEPTED, ProposalStatus.REVISED}
)
"""この状態の提案がある指紋は、冷却に関係なく抑える（判断待ち・採択済み・修正採択済み）。"""


def signal_fingerprint(signal: DiscoverySignal) -> str:
    """signal の指紋（再提案の抑制の単位）。

    ``trigger``・実体の集合・証拠の種別（``:`` より前）から作る。**件数や強さを含めない**——
    同じ状況が続くだけで強さや件数は変わるので、含めると同じ signal が毎周期別物になる。

    Args:
        signal: 発見の signal。
    """
    entities = ",".join(sorted(signal.entities))
    kinds = ",".join(sorted({item.split(":", 1)[0] for item in signal.evidence}))
    return f"{signal.trigger.value}|{entities}|{kinds}"


def resolution_signals(
    records: Sequence[DiscrepancyRecord], *, min_repeats: int
) -> tuple[DiscoverySignal, ...]:
    """解決済みの不一致から入口 (c)「反復する不一致」の signal を作る。

    Args:
        records: 解決済み（``Resolved`` / ``Learned``）の不一致の行。
        min_repeats: 反復とみなす回数（2 以上）。

    Returns:
        signal の列（強い順）。解決結果を持たない行は数えない。
    """
    resolutions: list[tuple[str, int, str]] = []
    for record in records:
        resolution = record.payload.get("resolution")
        outcome = resolution.get("outcome") if isinstance(resolution, dict) else None
        if not isinstance(outcome, str):
            continue
        resolutions.extend((subject, record.kind, outcome) for subject in record.subjects)
    return repeated_discrepancy_signals(resolutions, min_repeats=min_repeats)


def suppressed_fingerprints(
    existing: Sequence[ConceptProposalRecord], *, at: datetime, cooldown: timedelta
) -> frozenset[str]:
    """再提案を抑える指紋の集合（H-10、ADR-0019）。

    - ``proposed`` / ``accepted`` / ``revised`` の提案の指紋は**常に**抑える。
    - ``rejected`` の提案の指紋は、却下した時刻（行の ``updated_at``。無ければ提案時刻）から
      ``cooldown`` の間だけ抑える。冷却後に同じ signal が出れば、状況が変わった可能性があるので
      再提案する。

    読み戻せない行（本体が提案として成立しない）は飛ばして警告する。1 件の壊れた行で発見全体を
    止めない。

    Args:
        existing: 既存の提案の行（採否済みを含む）。
        at: 現在時刻。
        cooldown: 却下の冷却期間。
    """
    blocking = _blocking_proposals(existing, at=at, cooldown=cooldown)
    return frozenset(
        signal_fingerprint(signal) for proposal in blocking for signal in proposal.signals
    )


def _blocking_proposals(
    existing: Sequence[ConceptProposalRecord], *, at: datetime, cooldown: timedelta
) -> list[ConceptProposal]:
    """いま再提案を妨げている提案（判断待ち・採択済み・冷却中の却下）。

    指紋の抑制と、提案名の重複の判定が**同じ集合**を見る——冷却を過ぎた却下の提案と同じ名前を
    LLM が返しても、それは再提案であって重複ではない。

    Args:
        existing: 既存の提案の行。
        at: 現在時刻。
        cooldown: 却下の冷却期間。
    """
    blocking: list[ConceptProposal] = []
    now_ = ensure_utc(at)
    for record in existing:
        try:
            proposal = from_record(record)
        except Exception as exc:
            log.warning(
                "discovery.proposal_unreadable",
                correlation_id=str(record.proposal_id),
                error=str(exc),
            )
            continue
        if proposal.status in _ALWAYS_SUPPRESSING:
            blocking.append(proposal)
        elif proposal.status is ProposalStatus.REJECTED:
            rejected_at = record.updated_at or record.proposed_at
            if rejected_at is None or now_ - ensure_utc(rejected_at) < cooldown:
                blocking.append(proposal)
    return blocking


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """発見 1 周期の結果。**件数を内訳で持つ**——「提案が 0 件」の理由（抑制・LLM の失敗・既存で
    足りる）がログに出ないと、動いていないのか、出すものが無いのかが区別できない。

    Attributes:
        signals: 入口が出した signal の数。
        suppressed: 指紋で抑えた数（LLM を呼んでいない）。
        deferred: 周期あたりの上限で次周期に回した数。
        failed: LLM の失敗・出力の崩れで提案にならなかった数。
        declined: LLM が「既存の語彙で足りる」と答えた（または既存と同名の）数。
        proposals: 生成した提案（``PROPOSED``）。
    """

    signals: int
    suppressed: int
    deferred: int
    failed: int
    declined: int
    proposals: tuple[ConceptProposal, ...]


def run_discovery(
    signals: Sequence[DiscoverySignal],
    *,
    existing: Sequence[ConceptProposalRecord],
    provider: Provider,
    at: datetime,
    cooldown: timedelta,
    max_proposals: int,
    existing_fluents: Sequence[str],
) -> DiscoveryReport:
    """signal から提案を作る（抑制 → 上限 → LLM の順。ADR-0019）。

    **signal 1 件ごとに 1 回呼ぶ**（プロンプトの制約: 1 回の提案は 1 概念まで）。強い signal から
    ``max_proposals`` 件まで。LLM を呼ぶ前に指紋で抑えるので、抑制された signal は費用が掛からない。

    **LLM の失敗は提案を作らない。** 例外は握らずに数える（replay で fixture が無い、出力が崩れた、
    プロバイダの失敗）——黙って成功にすると、発見が止まっていることに気付けない。1 件の失敗で残りを
    止めない。出力は pydantic で検証してから提案にする（設計書 §7.5）。**自動採用しない**
    （``PROPOSED`` のまま。不変条件 11）。

    同じ周期で既存と同名の提案が返ってきたら採らない（LLM は同じ状況に同じ名前を付けやすい）。

    Args:
        signals: 入口が出した signal。
        existing: 既存の提案の行（採否済みを含む）。
        provider: LLM プロバイダ（replay / live）。
        at: 現在時刻（提案時刻・冷却の判定）。
        cooldown: 却下の冷却期間。
        max_proposals: 1 周期あたりに LLM を呼ぶ上限。
        existing_fluents: 既存のフルーエント型のローカル名（プロンプトに渡す）。

    Returns:
        結果と内訳。
    """
    blocking = _blocking_proposals(existing, at=at, cooldown=cooldown)
    blocked = frozenset(
        signal_fingerprint(signal) for proposal in blocking for signal in proposal.signals
    )
    ordered = sorted(signals, key=lambda signal: signal.strength, reverse=True)
    candidates = [signal for signal in ordered if signal_fingerprint(signal) not in blocked]
    suppressed = len(ordered) - len(candidates)
    selected, deferred = candidates[:max_proposals], max(0, len(candidates) - max_proposals)

    routing = load_routing()[LlmTask.CONCEPT_PROPOSAL]
    template = PromptRegistry().get(LlmTask.CONCEPT_PROPOSAL, routing.prompt_version)
    taken = {proposal.name.lower() for proposal in blocking}
    proposals: list[ConceptProposal] = []
    failed = declined = 0
    for signal in selected:
        messages = render_messages(template.body, [signal], existing=existing_fluents)
        try:
            response = provider.complete_structured(
                messages,
                schema=PROPOSAL_SCHEMA,
                task=LlmTask.CONCEPT_PROPOSAL,
                prompt_version=template.version,
            )
            output: ProposalOutput = response.validated(ProposalOutput)
        except LlmError as exc:
            failed += 1
            log.warning(
                "discovery.proposal_failed",
                fingerprint=signal_fingerprint(signal),
                error=str(exc),
            )
            continue
        if output.name.lower() in taken:
            declined += 1
            log.info("discovery.proposal_duplicate_name", name=output.name)
            continue
        try:
            proposal = ConceptProposal(
                name=output.name,
                definition=output.definition,
                superclass=output.superclass,
                mutually_exclusive_with=output.mutually_exclusive_with,
                initiates=output.initiates,
                terminates=output.terminates,
                rejected_alternatives=output.rejected_alternatives,
                signals=(signal,),
                proposed_at=at,
                llm_provenance=response.provenance(),
            )
        except ReasoningError as exc:
            declined += 1
            log.info("discovery.proposal_declined", error=str(exc))
            continue
        taken.add(output.name.lower())
        proposals.append(proposal)
    report = DiscoveryReport(
        signals=len(ordered),
        suppressed=suppressed,
        deferred=deferred,
        failed=failed,
        declined=declined,
        proposals=tuple(proposals),
    )
    log.info(
        "discovery.cycle",
        signals=report.signals,
        suppressed=report.suppressed,
        deferred=report.deferred,
        failed=report.failed,
        declined=report.declined,
        proposed=len(report.proposals),
    )
    return report
