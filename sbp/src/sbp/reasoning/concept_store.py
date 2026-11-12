"""概念提案と作業項目レコードの相互変換（M8-19、ADR-0015）。

不一致（:mod:`sbp.reasoning.reconciliation.persistence`）と期待
（:mod:`sbp.reasoning.expectation_store`）と同じ役目——``knowledge`` は ``ConceptProposal`` の型を
知らないので（下位層なので import できない）、この変換が両者を繋ぐ。

## 往復は完全でない（ADR-0015 の決定）

**``signals[].cluster`` は保存せず、復元もしない。** `LatentCluster.centroid` は潜在ベクトルで、
潜在索引のものである——作業項目表に置くと同じ値が 2 箇所にあり、再接地で片方だけが古くなる
（索引は WM 版で無効化されるが、この表はされない）。

だから :func:`from_record` が返す提案の signals は必ず ``cluster=None`` である。**これは欠陥では
なく決定である。** 隠さないために、docstring に書き、テストで固定してある（所見 90 の反省——
「同名なのに違う値」を黙って作らない）。

**判断に要る 2 つのスカラーは導出して持つ。** ``support_count``（構成標本数の合計）と
``coverage``（プローブ活性の平均）は重心を落とすと読み出し側で計算できないので、書き込み時に
1 回だけ計算して列に出す。UI の curation 検査（設計書 §11.2）がこれを使う。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc
from sbp.knowledge.concepts import ConceptProposalRecord
from sbp.reasoning.discovery import (
    ConceptProposal,
    DiscoverySignal,
    DiscoveryTrigger,
    ProposalStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["coverage_of", "from_record", "support_count_of", "to_record"]

log = get_logger(__name__)


def support_count_of(signals: Sequence[DiscoverySignal]) -> int:
    """提案を支持する標本数（設計書 §11.2 の判断材料）。

    **クラスタを持つ signal の構成標本数を足す。** クラスタの無い signal（整合残差・反復不一致
    由来）は標本数を持たないので 0 として数える——「標本が少ない」と「標本という概念が無い」を
    同じ 0 にするのは不正確だが、**UI の検査は「10 件未満なら偶然と区別できない」という下限の
    判定**なので、持たないものを多く見せないほうが安全側である。

    Args:
        signals: 根拠になった signal。
    """
    return sum(signal.cluster.size for signal in signals if signal.cluster is not None)


def coverage_of(signals: Sequence[DiscoverySignal]) -> float:
    """既存概念での被覆率（設計書 §11.2 の判断材料）。

    クラスタを持つ signal の ``mean_probe_activation`` の平均である——「既存プローブがどれだけ
    反応しているか」で、高いほど既存概念で説明できる（新概念が要らない）。

    Args:
        signals: 根拠になった signal。

    Returns:
        平均活性。クラスタを持つ signal が無ければ 0.0（**被覆していない**＝新概念の余地がある
        側に倒す。高い値を返すと提案が検査で落ちる）。
    """
    activations = [
        signal.cluster.mean_probe_activation for signal in signals if signal.cluster is not None
    ]
    if not activations:
        return 0.0
    return sum(activations) / len(activations)


def to_record(proposal: ConceptProposal) -> ConceptProposalRecord:
    """提案を表の行にする（重心は落とす）。

    Args:
        proposal: 概念提案。

    Returns:
        作業項目レコード。
    """
    return ConceptProposalRecord(
        proposal_id=proposal.proposal_id,
        name=proposal.name,
        status=proposal.status.value,
        status_rank=proposal.status.rank,
        superclass=proposal.superclass,
        support_count=support_count_of(proposal.signals),
        coverage=coverage_of(proposal.signals),
        proposed_at=proposal.proposed_at,
        payload=_to_payload(proposal),
    )


def from_record(record: ConceptProposalRecord) -> ConceptProposal:
    """表の行を提案に戻す。

    **``signals[].cluster`` は ``None`` になる**（ADR-0015）。重心を保存していないので復元でき
    ない。クラスタそのものが必要なら潜在索引から引き直す。

    Args:
        record: 作業項目レコード。

    Returns:
        提案（クラスタを欠く）。

    Raises:
        ValueError: 列と本体の識別子が食い違う場合。**黙って本体を信じない**——どちらが
            正しいか分からない。
        ReasoningError: 本体が提案として成立しない場合（``__post_init__`` が検証する）。
    """
    payload = record.payload
    body_id = payload.get("proposal_id")
    if body_id is not None and str(body_id) != str(record.proposal_id):
        log.error(
            "reasoning.concept_record_mismatch",
            correlation_id=str(record.proposal_id),
            payload_id=str(body_id),
            detail="列と本体の識別子が違う。書き込み側のバグ",
        )
        raise ValueError(
            f"record id {record.proposal_id} does not match payload id {body_id}; "
            "the row was written inconsistently"
        )
    proposed_at = payload.get("proposed_at")
    return ConceptProposal(
        proposal_id=record.proposal_id,
        name=str(payload.get("name", record.name)),
        definition=str(payload.get("definition", "")),
        superclass=str(payload.get("superclass", record.superclass)),
        mutually_exclusive_with=tuple(payload.get("mutually_exclusive_with", ())),
        initiates=tuple(payload.get("initiates", ())),
        terminates=tuple(payload.get("terminates", ())),
        rejected_alternatives=tuple(payload.get("rejected_alternatives", ())),
        signals=tuple(_signal_from(item) for item in payload.get("signals", ())),
        status=ProposalStatus(str(payload.get("status", record.status))),
        curation_note=str(payload.get("curation_note", "")),
        proposed_at=None if proposed_at is None else ensure_utc(_parse(proposed_at)),
        llm_provenance=dict(payload.get("llm_provenance", {})),
    )


def _to_payload(proposal: ConceptProposal) -> dict[str, Any]:
    """提案を JSON にできる dict にする（``cluster`` を除く）。

    Args:
        proposal: 概念提案。
    """
    return {
        "proposal_id": str(proposal.proposal_id),
        "name": proposal.name,
        "definition": proposal.definition,
        "superclass": proposal.superclass,
        "mutually_exclusive_with": list(proposal.mutually_exclusive_with),
        "initiates": list(proposal.initiates),
        "terminates": list(proposal.terminates),
        "rejected_alternatives": list(proposal.rejected_alternatives),
        "signals": [_signal_to(signal) for signal in proposal.signals],
        "status": proposal.status.value,
        "curation_note": proposal.curation_note,
        "proposed_at": None if proposal.proposed_at is None else proposal.proposed_at.isoformat(),
        "llm_provenance": dict(proposal.llm_provenance),
    }


def _signal_to(signal: DiscoverySignal) -> dict[str, Any]:
    """signal を dict にする（``cluster`` は入れない。ADR-0015）。

    Args:
        signal: 発見の signal。
    """
    return {
        "trigger": signal.trigger.value,
        "entities": list(signal.entities),
        "strength": signal.strength,
        "evidence": list(signal.evidence),
        "note": signal.note,
        # クラスタの素性だけ残す。重心は潜在索引のもの（ADR-0015）
        "cluster_size": 0 if signal.cluster is None else signal.cluster.size,
        "cluster_activation": (
            None if signal.cluster is None else signal.cluster.mean_probe_activation
        ),
    }


def _signal_from(item: dict[str, Any]) -> DiscoverySignal:
    """dict を signal に戻す（``cluster`` は ``None`` のまま）。

    Args:
        item: ``_signal_to`` が作った dict。
    """
    return DiscoverySignal(
        trigger=DiscoveryTrigger(str(item["trigger"])),
        entities=tuple(item.get("entities", ())),
        strength=float(item["strength"]),
        evidence=tuple(item.get("evidence", ())),
        note=str(item.get("note", "")),
        cluster=None,
    )


def _parse(value: Any) -> Any:
    """ISO 8601 文字列を ``datetime`` にする（既に ``datetime`` ならそのまま）。

    Args:
        value: 文字列または ``datetime``。
    """
    if isinstance(value, str):
        from datetime import datetime  # noqa: PLC0415 — 変換のときだけ使う

        return datetime.fromisoformat(value)
    return value
