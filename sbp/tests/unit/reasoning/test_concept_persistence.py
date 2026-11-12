"""概念提案と作業項目レコードの相互変換（M8-19、ADR-0015）。

重点:

1. **往復は完全でない** — `signals[].cluster` は保存せず復元しない（潜在索引のもの）。
   これは決定なので**テストで固定する**（所見 90 の反省——「黙って違う値」を作らない）。
2. **判断材料は導出して持つ** — 支持する標本数と被覆率。重心を落とすと読み出しで計算できない。
3. **終端の判定と `rank` が `reasoning` の列挙と一致する** — 食い違うと curation の待ち行列が
   おかしくなる。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest

from sbp.knowledge.concepts import ConceptProposalRecord
from sbp.reasoning.concept_store import (
    coverage_of,
    from_record,
    support_count_of,
    to_record,
)
from sbp.reasoning.discovery import (
    ConceptProposal,
    DiscoverySignal,
    DiscoveryTrigger,
    LatentCluster,
    ProposalStatus,
)

T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _cluster(*, size: int = 548, activation: float = 0.0) -> LatentCluster:
    return LatentCluster(
        cluster_id=7,
        member_indices=tuple(range(size)),
        centroid=np.zeros(16, dtype=np.float64),
        mean_probe_activation=activation,
        density=1.5,
    )


def _signal(*, cluster: LatentCluster | None = None) -> DiscoverySignal:
    return DiscoverySignal(
        trigger=DiscoveryTrigger.LOW_COVERAGE_CLUSTER,
        entities=(PUMP,),
        strength=0.873,
        evidence=("cluster:7",),
        note="既知プローブが反応しない高密度クラスタ",
        cluster=cluster,
    )


def _proposal(**overrides: object) -> ConceptProposal:
    defaults: dict[str, object] = {
        "name": "VibrationAnomaly",
        "definition": "既存の Faulted では説明できない振動の増大が停止の前段に現れる",
        "superclass": "Fluent",
        "rejected_alternatives": ("Faulted の閾値調整", "UnderMaintenance の拡張"),
        "signals": (_signal(cluster=_cluster()),),
        "proposed_at": T0,
        "llm_provenance": {"provider": "anthropic", "prompt_version": "abc123"},
    }
    return ConceptProposal(**(defaults | overrides))  # pyright: ignore[reportArgumentType]


class TestDerivedScalars:
    """判断材料（設計書 §11.2）。"""

    def test_the_support_count_sums_cluster_sizes(self) -> None:
        """支持する標本数はクラスタの構成標本数の合計。"""
        signals = (_signal(cluster=_cluster(size=100)), _signal(cluster=_cluster(size=48)))

        assert support_count_of(signals) == 148

    def test_signals_without_a_cluster_count_as_zero(self) -> None:
        """クラスタを持たない signal は標本数 0 として数える。

        整合残差・反復不一致由来の signal は標本という概念を持たない。**持たないものを多く
        見せない**——UI の検査は「10 件未満なら偶然と区別できない」という下限の判定なので、
        安全側に倒す。
        """
        assert support_count_of((_signal(cluster=None),)) == 0

    def test_the_coverage_averages_probe_activation(self) -> None:
        """被覆率はプローブ活性の平均（高いほど既存概念で説明できる）。"""
        signals = (
            _signal(cluster=_cluster(activation=0.2)),
            _signal(cluster=_cluster(activation=0.6)),
        )

        assert coverage_of(signals) == pytest.approx(0.4)

    def test_no_cluster_means_no_coverage(self) -> None:
        """クラスタが無ければ被覆率 0（新概念の余地がある側に倒す）。

        高い値を返すと、クラスタ由来でない提案が「既存概念で足りる」として検査で落ちる。
        """
        assert coverage_of((_signal(cluster=None),)) == 0.0

    def test_the_record_carries_the_derived_values(self) -> None:
        """導出値が列に出る（読み出しでは計算できない）。"""
        record = to_record(_proposal(signals=(_signal(cluster=_cluster(size=548)),)))

        assert record.support_count == 548
        assert record.coverage == 0.0


class TestRoundTrip:
    """往復（完全でない）。"""

    def test_everything_but_the_cluster_round_trips(self) -> None:
        """重心以外は戻る。"""
        original = _proposal()

        restored = from_record(to_record(original))

        assert restored.proposal_id == original.proposal_id
        assert restored.name == original.name
        assert restored.definition == original.definition
        assert restored.superclass == original.superclass
        assert restored.rejected_alternatives == original.rejected_alternatives
        assert restored.status is original.status
        assert restored.proposed_at == original.proposed_at
        assert restored.llm_provenance == original.llm_provenance
        assert len(restored.signals) == len(original.signals)
        assert restored.signals[0].trigger is original.signals[0].trigger
        assert restored.signals[0].strength == original.signals[0].strength
        assert restored.signals[0].evidence == original.signals[0].evidence

    def test_the_cluster_is_deliberately_not_restored(self) -> None:
        """**重心は復元しない**（ADR-0015 の決定。欠陥ではない）。

        `LatentCluster.centroid` は潜在ベクトルで、潜在索引のものである——作業項目表に置くと
        同じ値が 2 箇所にあり、再接地で片方だけが古くなる（索引は WM 版で無効化されるが、
        この表はされない）。

        **このテストが決定の記録である。** 将来 `cluster` が復元されるようになったら、
        ADR-0015 を読み直すために落ちる。
        """
        original = _proposal(signals=(_signal(cluster=_cluster()),))
        assert original.signals[0].cluster is not None

        restored = from_record(to_record(original))

        assert restored.signals[0].cluster is None

    def test_a_curated_proposal_round_trips(self) -> None:
        """採否が入った提案も戻る（理由を含む）。"""
        original = _proposal().curate(ProposalStatus.REJECTED, note="既存概念の閾値調整で足りる")

        restored = from_record(to_record(original))

        assert restored.status is ProposalStatus.REJECTED
        assert restored.curation_note == "既存概念の閾値調整で足りる"

    def test_an_inconsistent_payload_is_refused(self) -> None:
        """列と本体の識別子が食い違う行を拒否する。"""
        record = to_record(_proposal())
        broken = ConceptProposalRecord(
            proposal_id=uuid4(),
            name=record.name,
            status=record.status,
            status_rank=record.status_rank,
            payload=record.payload,
        )

        with pytest.raises(ValueError, match="does not match payload id"):
            from_record(broken)


class TestStatusRank:
    """`knowledge` と `reasoning` の一致。"""

    def test_the_pending_flag_matches_the_enum(self) -> None:
        """``ConceptProposalRecord.is_pending`` が ``ProposalStatus`` の判定と一致する。

        **``knowledge`` は ``reasoning`` の列挙を import できない**ので値を写してある。
        食い違うと curation の待ち行列が空になる（または全件出る）。
        """
        for status in ProposalStatus:
            record = ConceptProposalRecord(
                proposal_id=uuid4(),
                name="X",
                status=status.value,
                status_rank=status.rank,
            )
            assert record.is_pending == (not status.is_curated), status.value

    def test_the_rank_separates_pending_from_curated(self) -> None:
        """``rank`` が未判断と判断済みを分ける（後退検出の前提）。

        3 つの判断（``accepted`` / ``revised`` / ``rejected``）は**同じ位置**である——互いに
        遷移しない。判断をやり直すなら新しい提案として出す。
        """
        assert ProposalStatus.PROPOSED.rank == 0
        assert (
            ProposalStatus.ACCEPTED.rank
            == ProposalStatus.REVISED.rank
            == ProposalStatus.REJECTED.rank
            == 1
        )
        assert all(status.is_curated for status in ProposalStatus if status.rank == 1)
