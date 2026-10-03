"""解決結果 → 学習信号（設計書 §8.2 の「解決結果 → 学習信号」表）。

ここが中核テーゼ 5 の後半——**不一致は解決されて消えるのではなく、学習信号になって消える**
——の実装である。これが無いと照合エンジンは「食い違いを数える装置」にとどまり、同じ食い違いが
翌月も同じ件数で出る。

| 解決結果 | 処置 |
|---|---|
| 物理が正しい | 記録訂正の書き戻し提案 ＋ 情報源信頼性モデルの更新 |
| 記録が正しい | プローブ／WM の**負例バッグ**生成（能動学習へ） |
| 両方誤り | 概念候補（オントロジー欠落の疑い）＋ Situation 保存 |
| 判定不能 | 追加観測要求（エピステミック・スケジューラへ） |

**「両方誤り」で信頼性モデルを動かさない**のが要点である。どちらが悪かったか分かっていない
のに記録側の誤記率を上げると、オントロジーの欠落を情報源の品質問題として処理してしまい、
本当の原因（語彙が足りない）が隠れる。

生成物は :class:`LearningArtifacts` にまとめ、不一致に戻す参照だけを
:class:`~sbp.reasoning.reconciliation.discrepancy.LearningSignals` に入れる。実際の適用
（書き戻しの実行、プローブの再学習、概念の採否）はこのモジュールの外で、人の承認を経て行う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sbp.bridge.mil import TimeBag
from sbp.core.logging import get_logger
from sbp.integration.writeback import WritebackCommand, WritebackTarget
from sbp.reasoning.reconciliation.discrepancy import (
    DiscrepancyStatus,
    LearningSignals,
    ResolutionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Mapping as MappingABC
    from datetime import datetime

    from sbp.core.records import Record
    from sbp.core.time import Interval
    from sbp.integration.mappings import MappingRegistry
    from sbp.integration.reliability import ReliabilityModel, ReliabilityRegistry
    from sbp.reasoning.reconciliation.discrepancy import Discrepancy, Resolution

__all__ = [
    "ConceptCandidate",
    "LearningArtifacts",
    "ObservationRequest",
    "apply_resolution",
    "convert_resolution",
]

log = get_logger(__name__)

_MIN_BAG_WEIGHT = 0.05
"""負例バッグの最小重み。信頼性の低い情報源の主張でも完全には捨てない。"""


@dataclass(frozen=True, slots=True)
class ConceptCandidate:
    """オントロジー欠落の候補（設計書 §8.6 の入力。概念発見ループが受け取る）。

    Attributes:
        entity: 対象実体の IRI。
        interval: 説明できなかった区間。
        reason: なぜ既存語彙で説明できないのか。
        discrepancy_id: 由来の不一致。
        evidence: 根拠の証拠 IRI。
    """

    entity: str
    interval: Interval
    reason: str
    discrepancy_id: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    """追加観測の要求（設計書 §8.7 のエピステミック・スケジューラが受け取る）。

    Attributes:
        entity: 観測したい実体の IRI。
        fluent: 確かめたいフルーエントの IRI。
        interval: 対象区間。
        reason: なぜ追加観測が必要か。
        utility: 期待効用のヒント（深刻度をそのまま使う。スケジューラが EIG と掛ける）。
        discrepancy_id: 由来の不一致。
    """

    entity: str
    fluent: str
    interval: Interval
    reason: str
    utility: float
    discrepancy_id: str


@dataclass(frozen=True, slots=True)
class LearningArtifacts:
    """解決 1 件から生まれた学習成果。

    Attributes:
        corrections: 記録訂正の書き戻し提案（実行は人の承認後）。
        reliability_updates: 更新後の信頼性モデル（レジストリへの反映は呼び出し側）。
        bags: プローブ／WM の弱ラベル（負例バッグ）。
        concept_candidates: 概念候補。
        observation_requests: 追加観測の要求。
        signals: 不一致に戻す参照（``LearningSignals``）。
    """

    corrections: tuple[WritebackCommand, ...]
    reliability_updates: tuple[ReliabilityModel, ...]
    bags: tuple[TimeBag, ...]
    concept_candidates: tuple[ConceptCandidate, ...]
    observation_requests: tuple[ObservationRequest, ...]
    signals: LearningSignals

    @property
    def is_empty(self) -> bool:
        """何も学べなかったか。"""
        return self.signals.is_empty


def convert_resolution(
    discrepancy: Discrepancy,
    *,
    resolution: Resolution,
    records: MappingABC[str, Record] | None = None,
    reliability: ReliabilityRegistry | None = None,
    mappings: MappingRegistry | None = None,
    corrections: MappingABC[str, Any] | None = None,
    at: datetime,
) -> LearningArtifacts:
    """解決結果を学習信号に変換する（設計書 §8.2 の表）。

    Args:
        discrepancy: 解決された不一致。
        resolution: 解決の記録。
        records: 証拠 IRI → 記録（訂正提案と信頼性更新に使う）。
        reliability: 信頼性モデルのレジストリ。
        mappings: 写像のレジストリ（訂正提案の宛先テーブルと列名を引く）。
        corrections: 訂正内容（``Record.payload`` のキー → 正しい値）。``physical_correct``
            のときだけ使う。
        at: 変換時刻。

    Returns:
        学習成果。
    """
    del at  # 現時点では成果物に時刻を持たせていない（書き戻しは自身の issued_at を持つ）
    outcome = resolution.outcome
    known = records or {}
    evidence_records = [
        (reference, known[reference]) for reference in discrepancy.evidence if reference in known
    ]

    corrections_out: list[WritebackCommand] = []
    reliability_out: list[ReliabilityModel] = []
    bags: list[TimeBag] = []
    concepts: list[ConceptCandidate] = []
    requests: list[ObservationRequest] = []

    if outcome is ResolutionOutcome.PHYSICAL_CORRECT:
        corrections_out, reliability_out = _from_physical_correct(
            discrepancy,
            resolution=resolution,
            evidence_records=evidence_records,
            reliability=reliability,
            mappings=mappings,
            corrections=corrections,
        )
    elif outcome is ResolutionOutcome.RECORD_CORRECT:
        bags, reliability_out = _from_record_correct(
            discrepancy, evidence_records=evidence_records, reliability=reliability
        )
    elif outcome is ResolutionOutcome.BOTH_WRONG:
        # どちらが悪いか分からないので信頼性モデルは動かさない（原因を隠さないため）
        concepts = _from_both_wrong(discrepancy, resolution=resolution)
    else:  # ResolutionOutcome.UNDETERMINED
        requests = _from_undetermined(discrepancy, resolution=resolution)

    signals = LearningSignals(
        labels_emitted=tuple(f"{bag.entity}#{bag.concept}" for bag in bags),
        reliability_updates=tuple(
            f"{model.source}|{model.record_type}" for model in reliability_out
        ),
        concept_candidates=tuple(
            f"{candidate.entity}#candidate-{candidate.discrepancy_id}" for candidate in concepts
        ),
        observation_requests=tuple(
            f"{request.entity}#observe-{request.discrepancy_id}" for request in requests
        ),
    )
    log.info(
        "reconciliation.learning_signals_built",
        discrepancy=str(discrepancy.discrepancy_id),
        outcome=outcome.value,
        corrections=len(corrections_out),
        bags=len(bags),
        concepts=len(concepts),
        requests=len(requests),
    )
    return LearningArtifacts(
        corrections=tuple(corrections_out),
        reliability_updates=tuple(reliability_out),
        bags=tuple(bags),
        concept_candidates=tuple(concepts),
        observation_requests=tuple(requests),
        signals=signals,
    )


def _from_physical_correct(
    discrepancy: Discrepancy,
    *,
    resolution: Resolution,
    evidence_records: list[tuple[str, Record]],
    reliability: ReliabilityRegistry | None,
    mappings: MappingRegistry | None,
    corrections: MappingABC[str, Any] | None,
) -> tuple[list[WritebackCommand], list[ReliabilityModel]]:
    """物理が正しい: 記録訂正の提案と、情報源信頼性モデルの更新。"""
    commands: list[WritebackCommand] = []
    models: list[ReliabilityModel] = []
    reason = (
        f"不一致 {discrepancy.discrepancy_id}（類型 {int(discrepancy.kind)}）の解決: "
        f"物理観測が正しいと判断された。{resolution.note}"
    )
    for reference, record in evidence_records:
        if corrections and mappings is not None:
            command = _build_correction(
                record, corrections=corrections, mappings=mappings, reason=reason
            )
            if command is not None:
                commands.append(command)
        if reliability is not None:
            models.append(_updated_model(record, reliability, outcome="physical_correct"))
        log.info(
            "reconciliation.learning_physical_correct",
            discrepancy=str(discrepancy.discrepancy_id),
            record=reference,
        )
    return commands, models


def _from_record_correct(
    discrepancy: Discrepancy,
    *,
    evidence_records: list[tuple[str, Record]],
    reliability: ReliabilityRegistry | None,
) -> tuple[list[TimeBag], list[ReliabilityModel]]:
    """記録が正しい: プローブ／WM の負例バッグと、信頼性モデルの上方更新。"""
    weight = _bag_weight(evidence_records, reliability)
    source = evidence_records[0][0] if evidence_records else None
    bags = [
        TimeBag(
            concept=fluent,
            entity=subject,
            interval=discrepancy.interval,
            # 記録が正しかった = プローブの主張が誤り → 負例バッグ
            positive=False,
            weight=weight,
            source_record=source,
        )
        for subject in discrepancy.subjects
        for fluent in discrepancy.assertions
    ]
    models = [
        _updated_model(record, reliability, outcome="record_correct")
        for _, record in evidence_records
        if reliability is not None
    ]
    return bags, models


def _from_both_wrong(discrepancy: Discrepancy, *, resolution: Resolution) -> list[ConceptCandidate]:
    """両方誤り: オントロジー欠落の候補（概念発見ループが受け取る）。"""
    return [
        ConceptCandidate(
            entity=subject,
            interval=discrepancy.interval,
            reason=(
                f"物理も記録も誤りと判断された（類型 {int(discrepancy.kind)}）。"
                f"既存語彙では説明できない状況の疑い。{resolution.note}"
            ),
            discrepancy_id=str(discrepancy.discrepancy_id),
            evidence=discrepancy.evidence,
        )
        for subject in discrepancy.subjects
    ]


def _from_undetermined(
    discrepancy: Discrepancy, *, resolution: Resolution
) -> list[ObservationRequest]:
    """判定不能: 追加観測の要求（エピステミック・スケジューラが受け取る）。"""
    return [
        ObservationRequest(
            entity=subject,
            fluent=fluent,
            interval=discrepancy.interval,
            reason=(
                f"不一致 {discrepancy.discrepancy_id} は現有の証拠では判定できない。"
                f"{resolution.note}"
            ),
            utility=discrepancy.severity.score,
            discrepancy_id=str(discrepancy.discrepancy_id),
        )
        for subject in discrepancy.subjects
        for fluent in discrepancy.assertions or ("",)
    ]


def apply_resolution(
    discrepancy: Discrepancy,
    *,
    resolution: Resolution,
    records: MappingABC[str, Record] | None = None,
    reliability: ReliabilityRegistry | None = None,
    mappings: MappingRegistry | None = None,
    corrections: MappingABC[str, Any] | None = None,
    at: datetime,
) -> tuple[Discrepancy, LearningArtifacts]:
    """解決を記録し、学習信号へ変換して ``Learned`` まで進める。

    不一致は ``Assigned`` であること。``Resolved`` を経由して ``Learned`` にするのは、
    「解決したが何も学ばなかった」状態を残さないためである（学習信号が空なら ``Resolved``
    のまま返す）。

    Args:
        discrepancy: ``Assigned`` の不一致。
        resolution: 解決の記録。
        records: 証拠 IRI → 記録。
        reliability: 信頼性モデルのレジストリ。
        mappings: 写像のレジストリ。
        corrections: 訂正内容。
        at: 処理時刻。

    Returns:
        進めた後の不一致と学習成果。
    """
    resolved = discrepancy.transition(
        DiscrepancyStatus.RESOLVED, at=at, note=resolution.note, resolution=resolution
    )
    artifacts = convert_resolution(
        resolved,
        resolution=resolution,
        records=records,
        reliability=reliability,
        mappings=mappings,
        corrections=corrections,
        at=at,
    )
    if artifacts.is_empty:
        log.info(
            "reconciliation.resolution_taught_nothing",
            discrepancy=str(discrepancy.discrepancy_id),
            outcome=resolution.outcome.value,
        )
        return resolved, artifacts
    learned = resolved.transition(
        DiscrepancyStatus.LEARNED,
        at=at,
        note=f"学習信号 {len(artifacts.signals.labels_emitted)} ラベル",
        learning=artifacts.signals,
    )
    return learned, artifacts


def _updated_model(
    record: Record,
    reliability: ReliabilityRegistry,
    *,
    outcome: Literal["physical_correct", "record_correct"],
) -> ReliabilityModel:
    """1 件の解決から信頼性モデルを更新する。"""
    model = reliability.for_record(record)
    lag = record.reporting_lag
    return model.update_from_resolution(
        outcome=outcome, observed_lag_s=lag.total_seconds() if lag is not None else None
    )


def _bag_weight(
    evidence_records: list[tuple[str, Record]], reliability: ReliabilityRegistry | None
) -> float:
    """負例バッグの重みを情報源の信頼性から導く。

    誤記の多い情報源が「記録が正しい」と判定された場合でも、その負例ラベルは弱くする。
    判定自体が記録に引きずられている可能性があるためである。
    """
    if not evidence_records or reliability is None:
        return 1.0
    model = reliability.for_record(evidence_records[0][1])
    if model.confusion is None:
        return 1.0
    return min(1.0, max(_MIN_BAG_WEIGHT, model.confusion.true_positive))


def _build_correction(
    record: Record,
    *,
    corrections: MappingABC[str, Any],
    mappings: MappingRegistry,
    reason: str,
) -> WritebackCommand | None:
    """記録訂正の書き戻しコマンドを組み立てる。

    ``Record.payload`` のキーで渡された訂正を、写像を逆に辿って業務システムの列名に戻す。
    写像が無ければ提案を作らない——どの列を書くか分からないまま業務システムを触らない。
    """
    mapping = mappings.for_record_type(record.record_type)
    if mapping is None:
        log.info(
            "reconciliation.correction_skipped_no_mapping",
            record_type=record.record_type,
            external_id=record.external_id,
        )
        return None
    changes: dict[str, Any] = {}
    for payload_key, value in corrections.items():
        column = mapping.column_for(payload_key)
        if column is None:
            log.info(
                "reconciliation.correction_skipped_unknown_field",
                mapping=mapping.mapping,
                payload_key=payload_key,
            )
            continue
        changes[column] = value
    if not changes:
        return None
    return WritebackCommand(
        target=WritebackTarget(
            source=record.source, table=mapping.table, external_id=record.external_id
        ),
        changes=changes,
        compensation="https://sbp.example/ent#RevertRecordCorrection",
        reason=reason,
    )
