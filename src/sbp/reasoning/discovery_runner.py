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

from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc
from sbp.reasoning.concept_store import from_record
from sbp.reasoning.discovery import ProposalStatus, repeated_discrepancy_signals

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sbp.knowledge.concepts import ConceptProposalRecord
    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.reasoning.discovery import DiscoverySignal

__all__ = ["resolution_signals", "signal_fingerprint", "suppressed_fingerprints"]

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
    suppressed: set[str] = set()
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
            suppressed.update(signal_fingerprint(signal) for signal in proposal.signals)
        elif proposal.status is ProposalStatus.REJECTED:
            rejected_at = record.updated_at or record.proposed_at
            if rejected_at is None or now_ - ensure_utc(rejected_at) < cooldown:
                suppressed.update(signal_fingerprint(signal) for signal in proposal.signals)
    return frozenset(suppressed)
