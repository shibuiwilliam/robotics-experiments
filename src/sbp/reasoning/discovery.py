"""概念発見ループ（設計書 §8.6、M6-04）。

検出の入口は 3 つ（設計書 §8.6 の段階 1）:

- **(a) 低被覆・高密度クラスタ**: 潜在密度が高くプローブ最大活性が低い領域。
  「よく起きているのに名前が無い」状態。
- **(b) 整合残差の持続**: ``A(f(z,a))`` と ``T(A(z),a)`` の食い違いが続く領域。
  記号側の遷移に無い変化が起きている。
- **(c) 反復する不一致**: 同じ実体・同じ類型が繰り返し解決される。
  語彙が足りず毎回人手で埋めている。

**(a) が最も重要で、最も見落としやすい。** プローブが発火しない領域は「異常が無い」ように見える
——どのプローブも反応しないので、どの指標にも現れない。潜在の密度と**プローブ最大活性の低さ**を
同時に見ることで、「頻繁に起きているが記号になっていない」領域が浮かぶ。

**LLM の提案を自動採用しない**（設計書 §8.6 の段階 4、CLAUDE.md）。この層が出すのは
:class:`ConceptProposal` までで、採否は概念オーナーが決める。採用されたら MINOR 版として
オントロジーに起票し、プローブを学習し、再接地で遡及的に事実を増やす——その一連を駆動するのは
人の判断である。自動採用を許すと、オントロジーが LLM の出力で勝手に育つ。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import numpy as np

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

__all__ = [
    "ConceptProposal",
    "DiscoverySignal",
    "DiscoveryTrigger",
    "LatentCluster",
    "ProposalStatus",
    "cluster_latents",
    "find_low_coverage_clusters",
    "repeated_discrepancy_signals",
    "residual_persistence_signals",
]

log = get_logger(__name__)

_MIN_CLUSTER = 5
"""クラスタとみなす最小件数。これ未満は外れ値であり、概念の候補にしない。"""


class DiscoveryTrigger(StrEnum):
    """発見の入口（設計書 §8.6 の段階 1）。"""

    LOW_COVERAGE_CLUSTER = "low_coverage_cluster"
    """潜在密度が高くプローブ最大活性が低い領域。"""
    ALIGNMENT_RESIDUAL = "alignment_residual"
    """整合残差が持続する領域。"""
    REPEATED_DISCREPANCY = "repeated_discrepancy"
    """同じ実体・類型の不一致が繰り返し解決されている。"""


class ProposalStatus(StrEnum):
    """提案の採否（設計書 §8.6 の段階 4、curation）。"""

    PROPOSED = "proposed"
    """LLM が提案した。**まだ何も起きていない。**"""
    ACCEPTED = "accepted"
    """概念オーナーが採用した。MINOR 版の起票へ進む。"""
    REVISED = "revised"
    """オーナーが修正して採用した（定義・上位概念が変わっている）。"""
    REJECTED = "rejected"
    """却下した。理由を残す（同じ提案が繰り返されるのを防ぐ）。"""

    @property
    def is_adopted(self) -> bool:
        """オントロジーに入るか。"""
        return self in {ProposalStatus.ACCEPTED, ProposalStatus.REVISED}

    @property
    def is_curated(self) -> bool:
        """人が採否を決めたか（curation の待ち行列から外れる）。"""
        return self is not ProposalStatus.PROPOSED

    @property
    def rank(self) -> int:
        """ライフサイクル上の位置（後退の検出に使う）。

        **採否が入った提案を ``proposed`` に戻せない**ようにするための順序である（所見 80 と
        同じ対処）。採択・修正採択・却下は**同じ位置**で、互いに遷移しない——人が判断を
        やり直すなら、新しい提案として出す。
        """
        return 0 if self is ProposalStatus.PROPOSED else 1


@dataclass(frozen=True, slots=True)
class LatentCluster:
    """潜在空間の密度クラスタ 1 つ。

    Attributes:
        cluster_id: 識別子。
        member_indices: 構成標本の添字（潜在列に対する）。
        centroid: 重心。
        mean_probe_activation: 構成標本でのプローブ最大活性の平均。
        density: クラスタの密度（重心からの平均距離の逆数）。
    """

    cluster_id: int
    member_indices: tuple[int, ...]
    centroid: NDArray[np.float64]
    mean_probe_activation: float
    density: float

    @property
    def size(self) -> int:
        """構成標本数。"""
        return len(self.member_indices)

    def is_low_coverage(self, *, activation_threshold: float) -> bool:
        """プローブが反応していないか。

        Args:
            activation_threshold: 「反応している」とみなす活性の下限。
        """
        return self.mean_probe_activation < activation_threshold


@dataclass(frozen=True, slots=True)
class DiscoverySignal:
    """発見の signal 1 件（LLM に渡す前の、機械が見つけた素材）。

    Attributes:
        trigger: どの入口から来たか。
        entities: 関係する実体の IRI。
        strength: 強さ（0〜1）。優先順位付けに使う。
        evidence: 根拠（クラスタ ID、残差の値、不一致 ID など）。
        note: 人が読む説明。
        cluster: 由来のクラスタ（(a) の場合）。
    """

    trigger: DiscoveryTrigger
    entities: tuple[str, ...]
    strength: float
    evidence: tuple[str, ...]
    note: str
    cluster: LatentCluster | None = None

    def __post_init__(self) -> None:
        """強さが確率であることを検証する。"""
        if not 0.0 <= self.strength <= 1.0:
            raise ReasoningError(f"signal strength must be in [0, 1], got {self.strength}")


@dataclass(frozen=True, slots=True)
class ConceptProposal:
    """概念の候補（設計書 §8.6 の段階 3 の出力）。

    **これは提案にすぎない。** ``status`` が ``PROPOSED`` のあいだ、オントロジーには何も起きない。

    Attributes:
        proposal_id: 識別子。
        name: ローカル名。
        definition: 一文の定義（成立条件）。
        superclass: 上位概念のローカル名。
        mutually_exclusive_with: 排他になる既存フルーエント型。
        initiates: この概念を成立させる既存の事象型。
        terminates: 不成立にする既存の事象型。
        rejected_alternatives: 既存概念で説明しようとして足りなかった点。**空なら提案しない。**
        signals: 根拠になった signal。
        status: 採否。
        curation_note: 採否の理由（却下時は必須）。
        proposed_at: 提案時刻。
        llm_provenance: ``provider / model / prompt_version`` の組。
    """

    proposal_id: UUID = field(default_factory=uuid4)
    name: str = ""
    definition: str = ""
    superclass: str = ""
    mutually_exclusive_with: tuple[str, ...] = ()
    initiates: tuple[str, ...] = ()
    terminates: tuple[str, ...] = ()
    rejected_alternatives: tuple[str, ...] = ()
    signals: tuple[DiscoverySignal, ...] = ()
    status: ProposalStatus = ProposalStatus.PROPOSED
    curation_note: str = ""
    proposed_at: datetime | None = None
    llm_provenance: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """提案として成立しているかを検証する。"""
        if not self.name or not self.definition:
            raise ReasoningError("a concept proposal needs both a name and a definition")
        if not self.rejected_alternatives:
            raise ReasoningError(
                f"proposal {self.name} does not say which existing concepts fall short; if none "
                "do, the existing vocabulary is enough and no new concept is needed (design §8.6)"
            )
        if not self.signals:
            raise ReasoningError(
                f"proposal {self.name} cites no discovery signal; a concept proposed without "
                "evidence from the data is a guess"
            )
        if self.proposed_at is not None:
            object.__setattr__(self, "proposed_at", ensure_utc(self.proposed_at))

    def curate(
        self, status: ProposalStatus, *, note: str, revised_definition: str | None = None
    ) -> ConceptProposal:
        """概念オーナーの採否を記録する（設計書 §8.6 の段階 4）。

        却下には理由を必須にする。理由が残らないと、同じ提案が次の発見サイクルで繰り返される。

        Args:
            status: 採否。
            note: 理由。
            revised_definition: ``REVISED`` のときの修正後の定義。

        Raises:
            ReasoningError: 理由が空、すでに採否済み、または ``REVISED`` で定義が無い場合。
        """
        if self.status is not ProposalStatus.PROPOSED:
            raise ReasoningError(
                f"proposal {self.name} is already {self.status.value}; curation happens once"
            )
        if not note:
            raise ReasoningError(
                "curation requires a note; without it the same proposal returns in the next "
                "discovery cycle"
            )
        if status is ProposalStatus.REVISED and not revised_definition:
            raise ReasoningError("a revised proposal must carry the revised definition")
        log.info(
            "reasoning.concept_curated",
            proposal=str(self.proposal_id),
            name=self.name,
            status=status.value,
        )
        return replace(
            self,
            status=status,
            curation_note=note,
            definition=revised_definition or self.definition,
        )

    @property
    def needs_ontology_bump(self) -> bool:
        """オントロジーの MINOR 版起票が必要か（設計書 §8.6 の段階 4）。"""
        return self.status.is_adopted


def cluster_latents(
    latents: NDArray[np.float64],
    probe_activations: NDArray[np.float64],
    *,
    radius: float,
    min_size: int = _MIN_CLUSTER,
) -> tuple[LatentCluster, ...]:
    """潜在を密度クラスタリングする（設計書 §8.6 の段階 2）。

    実装は単純な固定半径の逐次クラスタリングである。DBSCAN などを使わないのは、依存を増やす
    割に**この用途では差が出ない**からで、ここで必要なのは「近い標本が集まっている領域を見つける」
    ことだけである（クラスタ境界の厳密さは、代表事例を人に見せる段階では問題にならない）。

    ``min_size`` 未満の集まりは捨てる。外れ値を概念の候補にすると、1 回しか起きていない現象に
    名前を付けることになる。

    Args:
        latents: 潜在列（``(標本, 次元)``）。
        probe_activations: 各標本でのプローブ最大活性（``(標本,)``）。
        radius: 同じクラスタとみなす距離。
        min_size: クラスタの最小件数。

    Returns:
        クラスタの列（大きい順）。

    Raises:
        ReasoningError: 形が合わない、または半径が非正の場合。
    """
    if latents.ndim != 2:
        raise ReasoningError(f"latents must be 2-D (sample, dim), got shape {latents.shape}")
    if probe_activations.shape[0] != latents.shape[0]:
        raise ReasoningError(
            f"{latents.shape[0]} latents but {probe_activations.shape[0]} activations"
        )
    if radius <= 0.0:
        raise ReasoningError(f"radius must be positive, got {radius}")

    assigned = np.full(latents.shape[0], -1, dtype=int)
    clusters: list[tuple[int, list[int]]] = []
    for index in range(latents.shape[0]):
        if assigned[index] >= 0:
            continue
        cluster_id = len(clusters)
        members = [index]
        assigned[index] = cluster_id
        distances = np.linalg.norm(latents - latents[index], axis=1)
        for other in np.where((distances <= radius) & (assigned < 0))[0]:
            assigned[int(other)] = cluster_id
            members.append(int(other))
        clusters.append((cluster_id, members))

    built: list[LatentCluster] = []
    for cluster_id, members in clusters:
        if len(members) < min_size:
            continue
        points = latents[members]
        centroid = points.mean(axis=0)
        spread = float(np.mean(np.linalg.norm(points - centroid, axis=1)))
        built.append(
            LatentCluster(
                cluster_id=cluster_id,
                member_indices=tuple(sorted(members)),
                centroid=centroid,
                mean_probe_activation=float(np.mean(probe_activations[members])),
                # 密度は「広がりの逆数」。広がり 0（全点同一）は上限で飽和させる
                density=1.0 / max(spread, 1e-6),
            )
        )
    built.sort(key=lambda cluster: cluster.size, reverse=True)
    log.info(
        "reasoning.latents_clustered",
        samples=int(latents.shape[0]),
        clusters=len(built),
        radius=radius,
    )
    return tuple(built)


def find_low_coverage_clusters(
    clusters: Sequence[LatentCluster],
    *,
    activation_threshold: float = 0.3,
    entity: str,
) -> tuple[DiscoverySignal, ...]:
    """入口 (a): 高密度・低被覆のクラスタを signal にする（設計書 §8.6）。

    **プローブが反応しない領域はどの指標にも現れない。** 密度と活性の低さを同時に見るのが要点で、
    片方だけでは「よく起きているが名前が無い」状態が見えない。

    Args:
        clusters: クラスタの列。
        activation_threshold: 「反応している」とみなす活性の下限。
        entity: 対象実体の IRI。

    Returns:
        強さ順の signal。
    """
    signals: list[DiscoverySignal] = []
    if not clusters:
        return ()
    largest = max(cluster.size for cluster in clusters)
    for cluster in clusters:
        if not cluster.is_low_coverage(activation_threshold=activation_threshold):
            continue
        # 強さは「大きさ」と「活性の低さ」の積。小さいクラスタや少し反応している領域を上位に
        # 置かないため
        size_term = cluster.size / largest
        silence = 1.0 - cluster.mean_probe_activation / max(activation_threshold, 1e-6)
        signals.append(
            DiscoverySignal(
                trigger=DiscoveryTrigger.LOW_COVERAGE_CLUSTER,
                entities=(entity,),
                strength=float(min(1.0, max(0.0, size_term * silence))),
                evidence=(f"cluster:{cluster.cluster_id}",),
                note=(
                    f"{cluster.size} 標本の高密度クラスタでプローブ最大活性が "
                    f"{cluster.mean_probe_activation:.3f}（閾値 {activation_threshold}）"
                    "——よく起きているが記号になっていない"
                ),
                cluster=cluster,
            )
        )
    signals.sort(key=lambda signal: signal.strength, reverse=True)
    return tuple(signals)


def residual_persistence_signals(
    residuals: Sequence[float],
    *,
    entity: str,
    threshold: float,
    min_run: int = 3,
) -> tuple[DiscoverySignal, ...]:
    """入口 (b): 整合残差が持続している区間を signal にする（設計書 §8.6）。

    **一時的な残差は signal にしない。** 残差は測定値であり揺らぐ。持続していることが
    「記号側の遷移に無い変化が起きている」ことの証拠になる。

    Args:
        residuals: 時刻順の整合残差。
        entity: 対象実体の IRI。
        threshold: 残差が「大きい」とみなす閾値。
        min_run: 持続とみなす連続長。

    Returns:
        signal の列。

    Raises:
        ReasoningError: ``min_run`` が 1 未満の場合。
    """
    if min_run < 1:
        raise ReasoningError(f"min_run must be at least 1, got {min_run}")
    signals: list[DiscoverySignal] = []
    run_start: int | None = None
    for index, value in enumerate([*residuals, 0.0]):
        if value >= threshold and run_start is None:
            run_start = index
        elif value < threshold and run_start is not None:
            length = index - run_start
            if length >= min_run:
                window = residuals[run_start:index]
                signals.append(
                    DiscoverySignal(
                        trigger=DiscoveryTrigger.ALIGNMENT_RESIDUAL,
                        entities=(entity,),
                        strength=float(min(1.0, max(window) / max(threshold * 2.0, 1e-6))),
                        evidence=(f"residual_run:{run_start}-{index}",),
                        note=(
                            f"整合残差が {length} 刻み連続で閾値 {threshold} を超えた"
                            f"（最大 {max(window):.3f}）——記号側の遷移に無い変化"
                        ),
                    )
                )
            run_start = None
    return tuple(signals)


def repeated_discrepancy_signals(
    resolutions: Sequence[tuple[str, int, str]],
    *,
    min_repeats: int = 3,
) -> tuple[DiscoverySignal, ...]:
    """入口 (c): 繰り返し解決される不一致を signal にする（設計書 §8.6）。

    **「両方誤り」で解決されたものを重く見る。** 物理も記録も誤りという判定は、既存語彙では
    説明できない状況だった可能性が高い（M3-06 の学習信号がすでに概念候補を出す経路である）。

    Args:
        resolutions: ``(実体 IRI, 類型番号, 解決結果)`` の列。
        min_repeats: 繰り返しとみなす回数。

    Returns:
        signal の列。

    Raises:
        ReasoningError: ``min_repeats`` が 2 未満の場合。
    """
    if min_repeats < 2:
        raise ReasoningError(f"min_repeats must be at least 2, got {min_repeats}")
    counts: dict[tuple[str, int], list[str]] = {}
    for entity, kind, outcome in resolutions:
        counts.setdefault((entity, kind), []).append(outcome)

    signals: list[DiscoverySignal] = []
    for (entity, kind), outcomes in counts.items():
        if len(outcomes) < min_repeats:
            continue
        both_wrong = sum(1 for outcome in outcomes if outcome == "both_wrong")
        strength = min(1.0, (len(outcomes) / (min_repeats * 2)) + both_wrong * 0.2)
        signals.append(
            DiscoverySignal(
                trigger=DiscoveryTrigger.REPEATED_DISCREPANCY,
                entities=(entity,),
                strength=float(strength),
                evidence=(f"kind{kind}:{len(outcomes)}回",),
                note=(
                    f"類型 {kind} が {len(outcomes)} 回解決されている"
                    f"（うち「両方誤り」{both_wrong} 回）——語彙が足りず毎回人手で埋めている"
                ),
            )
        )
    signals.sort(key=lambda signal: signal.strength, reverse=True)
    return tuple(signals)
