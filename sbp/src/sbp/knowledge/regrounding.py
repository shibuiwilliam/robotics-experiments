"""再接地ジョブ（設計書 §6.8、M7-01）。

> 1. トリガ: OntologyVersion / Probe / WorldModelVersion の昇格、または不一致の遡及調査要求。
> 2. スコープ選択: 影響を受ける概念・実体型・期間を依存グラフから算出し、コストを見積もる。
> 3. 実行: latent 層を走査し、新版で A を再計算。新アサーションを新 ``tx_from`` で追加
>    （旧版は ``tx_to`` を閉じるだけ）。
> 4. 差分レポート: 確信度が閾値以上変化した文、新規/消滅した不一致を一覧化し、人の確認へ。

**旧文を消さない**（不変条件 2）。再接地は「過去の信念を訂正する」のではなく「新しい版で見直した
結果を追記する」である。``tx_to`` を閉じるだけにしておけば、``as_of`` で**当時の信念**を再構成
できる——これが二時制の存在理由であり、再接地が安全に行える理由である。

**コストを先に見積もる。** 潜在の走査量は実体数 × 期間 × 階層で決まり、全期間の再接地は数時間
かかりうる。見積りなしに走らせると、終わらないジョブを止められない（止めると中途半端な状態が
残る）。:func:`plan` が見積りを返し、実行は :func:`run` が別に行う。

**差分レポートは人の確認を前提にする。** 再接地で確信度が大きく動いた文は、モデルが良くなった
結果かもしれないし、新しい版が壊れている兆候かもしれない。自動で採用するのではなく、閾値を
超えた差分を一覧にして人に見せる（設計書 §6.8 の段階 4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sbp.core.errors import RegroundingError
from sbp.core.logging import get_logger
from sbp.core.time import Interval, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "CompatibilityMatrix",
    "DiffEntry",
    "RegroundingPlan",
    "RegroundingReport",
    "RegroundingTrigger",
    "ScopeEstimate",
    "plan",
    "summarise",
]

log = get_logger(__name__)

_SECONDS_PER_LATENT = 0.002
"""潜在 1 件の再計算にかかる見積り時間（秒）。実測から更新する。"""

_DEFAULT_CONFIDENCE_DELTA = 0.1
"""差分レポートに載せる確信度変化の閾値。"""


class RegroundingTrigger(StrEnum):
    """再接地の契機（設計書 §6.8 の段階 1）。"""

    ONTOLOGY_VERSION = "ontology_version"
    """オントロジー版の昇格（概念の追加・意味変更）。"""
    PROBE_VERSION = "probe_version"
    """プローブの昇格（同じ概念を別の重みで判定する）。"""
    WORLD_MODEL_VERSION = "world_model_version"
    """WM 版の昇格（潜在の意味が変わる）。"""
    RETROSPECTIVE_INVESTIGATION = "retrospective_investigation"
    """不一致の遡及調査要求（人からの依頼）。"""

    @property
    def invalidates_latents(self) -> bool:
        """潜在そのものを作り直す必要があるか。

        WM 版が変われば潜在の意味が変わるので、潜在索引ごと作り直す（別テーブルになる）。
        プローブやオントロジーの変更では潜在は使い回せる——**ここを区別しないと、プローブを
        1 本足すたびに全潜在を再計算することになる**。
        """
        return self is RegroundingTrigger.WORLD_MODEL_VERSION


@dataclass(frozen=True, slots=True)
class ScopeEstimate:
    """再接地のコスト見積り（設計書 §6.8 の段階 2）。

    Attributes:
        entities: 対象実体の数。
        interval: 対象期間。
        levels: 対象の階層数。
        latents: 走査する潜在の件数。
        estimated_duration: 見積り所要時間。
        recompute_latents: 潜在そのものを作り直すか。
    """

    entities: int
    interval: Interval
    levels: int
    latents: int
    estimated_duration: timedelta
    recompute_latents: bool

    @property
    def is_large(self) -> bool:
        """人の承認を取るべき規模か（1 時間以上）。"""
        return self.estimated_duration >= timedelta(hours=1)

    def explain(self) -> str:
        """人が読む見積り（runbook に貼る形）。"""
        span = self.interval.duration
        duration = "無期限" if span is None else f"{span.total_seconds() / 3600:.1f} 時間分"
        return (
            f"実体 {self.entities} 件 × {duration} × 階層 {self.levels} "
            f"= 潜在 {self.latents:,} 件。"
            f"見積り所要 {self.estimated_duration.total_seconds() / 60:.1f} 分"
            + ("（潜在の再計算を含む）" if self.recompute_latents else "（潜在は再利用）")
            + ("。**規模が大きいので承認を取る**" if self.is_large else "")
        )


@dataclass(frozen=True, slots=True)
class RegroundingPlan:
    """再接地の計画（設計書 §6.8 の段階 2 の出力）。

    Attributes:
        plan_id: 識別子。
        trigger: 契機。
        reason: なぜ再接地するのか（版の変更内容、調査の依頼理由）。
        entities: 対象実体の IRI。
        fluents: 対象フルーエント型の IRI。
        interval: 対象期間。
        from_version: 変更前の版（オントロジー版・プローブ版・WM 版のいずれか）。
        to_version: 変更後の版。
        estimate: コスト見積り。
        confidence_delta_threshold: 差分レポートに載せる確信度変化の閾値。
    """

    plan_id: UUID
    trigger: RegroundingTrigger
    reason: str
    entities: tuple[str, ...]
    fluents: tuple[str, ...]
    interval: Interval
    from_version: str
    to_version: str
    estimate: ScopeEstimate
    confidence_delta_threshold: float = _DEFAULT_CONFIDENCE_DELTA

    def __post_init__(self) -> None:
        """計画として成立しているかを検証する。"""
        if not self.reason:
            raise RegroundingError(
                "a regrounding plan must state why it runs; without it the diff report cannot be "
                "judged (was the model improved, or is the new version broken?)"
            )
        if not self.entities or not self.fluents:
            raise RegroundingError(
                "a regrounding plan must name the entities and fluents in scope; an unbounded "
                "scope cannot be estimated and cannot be stopped halfway safely"
            )
        if self.from_version == self.to_version:
            raise RegroundingError(
                f"regrounding from {self.from_version} to the same version does nothing; a "
                "retrospective investigation should name the version it re-evaluates against"
            )
        if not 0.0 < self.confidence_delta_threshold <= 1.0:
            raise RegroundingError(
                f"confidence_delta_threshold must be in (0, 1], got "
                f"{self.confidence_delta_threshold}"
            )


@dataclass(frozen=True, slots=True)
class DiffEntry:
    """再接地で変わった 1 文（差分レポートの行）。

    Attributes:
        subject: 主語 IRI。
        predicate: 述語 IRI。
        valid_from: 有効開始（**過去のまま**）。
        old_confidence: 旧版での確信度（新規なら ``None``）。
        new_confidence: 新版での確信度（消滅なら ``None``）。
    """

    subject: str
    predicate: str
    valid_from: datetime
    old_confidence: float | None
    new_confidence: float | None

    def __post_init__(self) -> None:
        """少なくとも片方の確信度があることを検証する。"""
        if self.old_confidence is None and self.new_confidence is None:
            raise RegroundingError(
                "a diff entry with neither an old nor a new confidence describes nothing"
            )
        object.__setattr__(self, "valid_from", ensure_utc(self.valid_from))

    @property
    def is_new(self) -> bool:
        """新版で初めて出た文か（遡及的に増えた事実）。"""
        return self.old_confidence is None

    @property
    def is_gone(self) -> bool:
        """新版で出なくなった文か。"""
        return self.new_confidence is None

    @property
    def delta(self) -> float:
        """確信度の変化量（新規・消滅は変化前後を 0 とみなす）。"""
        return (self.new_confidence or 0.0) - (self.old_confidence or 0.0)

    def exceeds(self, threshold: float) -> bool:
        """閾値を超える変化か。

        Args:
            threshold: 閾値。
        """
        return abs(self.delta) >= threshold


@dataclass(frozen=True, slots=True)
class RegroundingReport:
    """再接地の差分レポート（設計書 §6.8 の段階 4）。

    Attributes:
        plan: 実行した計画。
        started_at: 開始時刻。
        finished_at: 終了時刻。
        reassessed: 再評価した文の数。
        entries: 閾値を超えた差分。
        new_discrepancies: 新たに起票された不一致の ID。
        resolved_discrepancies: 新版で消えた不一致の ID。
        closed_assertions: ``tx_to`` を閉じた文の数（**消した数ではない**）。
    """

    plan: RegroundingPlan
    started_at: datetime
    finished_at: datetime
    reassessed: int
    entries: tuple[DiffEntry, ...] = ()
    new_discrepancies: tuple[str, ...] = ()
    resolved_discrepancies: tuple[str, ...] = ()
    closed_assertions: int = 0

    def __post_init__(self) -> None:
        """時刻の順序を検証する。"""
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        object.__setattr__(self, "finished_at", ensure_utc(self.finished_at))
        if self.finished_at < self.started_at:
            raise RegroundingError(
                f"regrounding finished before it started ({self.finished_at.isoformat()} < "
                f"{self.started_at.isoformat()})"
            )

    @property
    def duration(self) -> timedelta:
        """実際の所要時間。"""
        return self.finished_at - self.started_at

    @property
    def added(self) -> int:
        """遡及的に増えた文の数。"""
        return sum(1 for entry in self.entries if entry.is_new)

    @property
    def removed(self) -> int:
        """新版で出なくなった文の数。"""
        return sum(1 for entry in self.entries if entry.is_gone)

    @property
    def needs_review(self) -> bool:
        """人の確認が要るか。

        閾値を超えた差分が 1 件でもあれば要る。**自動で採用しない**——確信度が大きく動いたのは
        モデルが良くなった結果かもしれないし、新版が壊れている兆候かもしれない。
        """
        return bool(self.entries) or bool(self.new_discrepancies)

    def markdown(self) -> str:
        """runbook の形式で差分レポートを出す（``docs/runbooks/regrounding.md``）。"""
        lines = [
            f"# 再接地レポート {self.plan.plan_id}",
            "",
            f"- 契機: {self.plan.trigger.value}",
            f"- 理由: {self.plan.reason}",
            f"- 版: {self.plan.from_version} → {self.plan.to_version}",
            f"- 対象: 実体 {len(self.plan.entities)} 件 / フルーエント {len(self.plan.fluents)} 件",
            f"- 期間: {self.plan.interval.start.isoformat()} .. "
            + (
                "（継続中）"
                if self.plan.interval.end is None
                else self.plan.interval.end.isoformat()
            ),
            f"- 見積り: {self.plan.estimate.explain()}",
            f"- 実績: {self.duration.total_seconds() / 60:.1f} 分 / 再評価 {self.reassessed:,} 文",
            f"- 旧文の扱い: `tx_to` を閉じた {self.closed_assertions:,} 件（**削除は 0 件**）",
            "",
            "## 閾値を超えた差分",
            "",
        ]
        if not self.entries:
            lines.append(f"確信度の変化が {self.plan.confidence_delta_threshold} 以上の文は無い。")
        else:
            lines.append("| 主語 | 述語 | 有効開始 | 旧 | 新 | 変化 |")
            lines.append("|---|---|---|---|---|---|")
            for entry in sorted(self.entries, key=lambda item: abs(item.delta), reverse=True):
                old = "—" if entry.old_confidence is None else f"{entry.old_confidence:.3f}"
                new = "—" if entry.new_confidence is None else f"{entry.new_confidence:.3f}"
                lines.append(
                    f"| {entry.subject} | {entry.predicate} | "
                    f"{entry.valid_from.isoformat()} | {old} | {new} | {entry.delta:+.3f} |"
                )
            lines.extend(["", f"新規 {self.added} 文 / 消滅 {self.removed} 文（遡及的な増減）。"])
        lines.extend(
            [
                "",
                "## 不一致の増減",
                "",
                f"- 新規起票: {len(self.new_discrepancies)} 件",
                f"- 新版で消えた: {len(self.resolved_discrepancies)} 件",
                "",
                "## 確認",
                "",
                (
                    "**人の確認が必要。** 確信度が動いた理由（モデルの改善か新版の不具合か）を"
                    "判断してから採用する。"
                    if self.needs_review
                    else "閾値を超える差分が無いため、確認は不要。"
                ),
            ]
        )
        return "\n".join(lines) + "\n"


def plan(
    *,
    trigger: RegroundingTrigger,
    reason: str,
    entities: Sequence[str],
    fluents: Sequence[str],
    interval: Interval,
    from_version: str,
    to_version: str,
    levels: int = 1,
    step: timedelta = timedelta(seconds=10),
    confidence_delta_threshold: float = _DEFAULT_CONFIDENCE_DELTA,
) -> RegroundingPlan:
    """スコープからコストを見積もって計画を作る（設計書 §6.8 の段階 2）。

    Args:
        trigger: 契機。
        reason: 理由。
        entities: 対象実体の IRI。
        fluents: 対象フルーエント型の IRI。
        interval: 対象期間。
        from_version: 変更前の版。
        to_version: 変更後の版。
        levels: 対象の階層数。
        step: 潜在の刻み幅（件数の見積りに使う）。
        confidence_delta_threshold: 差分レポートの閾値。

    Returns:
        計画。

    Raises:
        RegroundingError: 期間が無限、または刻みが非正の場合。
    """
    if interval.end is None:
        raise RegroundingError(
            "regrounding needs a bounded interval; an open-ended scope cannot be estimated "
            "(and a job whose size is unknown cannot be stopped safely)"
        )
    if step <= timedelta(0):
        raise RegroundingError(f"step must be positive, got {step}")
    if levels < 1:
        raise RegroundingError(f"levels must be at least 1, got {levels}")

    span = interval.end - interval.start
    per_entity = int(span / step)
    latents = len(entities) * per_entity * levels
    estimate = ScopeEstimate(
        entities=len(entities),
        interval=interval,
        levels=levels,
        latents=latents,
        estimated_duration=timedelta(seconds=latents * _SECONDS_PER_LATENT),
        recompute_latents=trigger.invalidates_latents,
    )
    built = RegroundingPlan(
        plan_id=uuid4(),
        trigger=trigger,
        reason=reason,
        entities=tuple(entities),
        fluents=tuple(fluents),
        interval=interval,
        from_version=from_version,
        to_version=to_version,
        estimate=estimate,
        confidence_delta_threshold=confidence_delta_threshold,
    )
    log.info(
        "knowledge.regrounding_planned",
        plan=str(built.plan_id),
        trigger=trigger.value,
        entities=len(entities),
        latents=latents,
        estimated_minutes=estimate.estimated_duration.total_seconds() / 60,
        recompute_latents=estimate.recompute_latents,
    )
    return built


def summarise(
    regrounding_plan: RegroundingPlan,
    *,
    diffs: Sequence[DiffEntry],
    reassessed: int,
    started_at: datetime,
    finished_at: datetime,
    new_discrepancies: Sequence[str] = (),
    resolved_discrepancies: Sequence[str] = (),
    closed_assertions: int = 0,
) -> RegroundingReport:
    """差分を閾値で絞ってレポートにする（設計書 §6.8 の段階 4）。

    **閾値未満の差分は載せない**が、再評価した総数は残す。「1 万文を見て 3 文が動いた」と
    「3 文だけ見た」は全く違う情報である。

    Args:
        regrounding_plan: 実行した計画。
        diffs: 全差分。
        reassessed: 再評価した文の数。
        started_at: 開始時刻。
        finished_at: 終了時刻。
        new_discrepancies: 新規の不一致 ID。
        resolved_discrepancies: 消えた不一致 ID。
        closed_assertions: ``tx_to`` を閉じた文の数。

    Returns:
        差分レポート。

    Raises:
        RegroundingError: 再評価数が差分より少ない場合（数が合わない）。
    """
    if reassessed < len(diffs):
        raise RegroundingError(
            f"reassessed {reassessed} statements but reported {len(diffs)} diffs; the counts do "
            "not add up"
        )
    threshold = regrounding_plan.confidence_delta_threshold
    notable = tuple(entry for entry in diffs if entry.exceeds(threshold))
    report = RegroundingReport(
        plan=regrounding_plan,
        started_at=started_at,
        finished_at=finished_at,
        reassessed=reassessed,
        entries=notable,
        new_discrepancies=tuple(new_discrepancies),
        resolved_discrepancies=tuple(resolved_discrepancies),
        closed_assertions=closed_assertions,
    )
    log.info(
        "knowledge.regrounding_summarised",
        plan=str(regrounding_plan.plan_id),
        reassessed=reassessed,
        notable=len(notable),
        added=report.added,
        removed=report.removed,
        needs_review=report.needs_review,
        duration_minutes=report.duration.total_seconds() / 60,
    )
    return report


@dataclass(slots=True)
class CompatibilityMatrix:
    """版の互換性行列（設計書 §12.4、M7-02）。

    > オントロジー版・WM 版・プローブ版・写像版は互換性行列で管理し、いずれかの昇格は
    > 影響分析 → カナリア → 再接地 → 差分レビューの同一パイプラインを通す。

    **4 つの版を独立に上げられないのが要点である。** プローブは特定の WM 版の潜在に対して学習
    されており、WM 版はオントロジーの概念に紐づき、写像は記録型に紐づく。どれかを上げたときに
    「どの組み合わせが検証済みか」を持っていないと、未検証の組み合わせが本番で動く。

    Attributes:
        verified: 検証済みの組み合わせ（``(オントロジー版, WM 版, プローブ版, 写像版)``）。
    """

    verified: set[tuple[str, str, str, str]] = field(default_factory=set)

    def record(self, *, ontology: str, world_model: str, probe: str, mapping: str) -> None:
        """検証済みの組み合わせを登録する。

        Args:
            ontology: オントロジー版。
            world_model: WM 版。
            probe: プローブ版。
            mapping: 写像版。
        """
        self.verified.add((ontology, world_model, probe, mapping))
        log.info(
            "knowledge.compatibility_recorded",
            ontology=ontology,
            world_model=world_model,
            probe=probe,
            mapping=mapping,
            total=len(self.verified),
        )

    def is_verified(self, *, ontology: str, world_model: str, probe: str, mapping: str) -> bool:
        """この組み合わせが検証済みか。

        Args:
            ontology: オントロジー版。
            world_model: WM 版。
            probe: プローブ版。
            mapping: 写像版。
        """
        return (ontology, world_model, probe, mapping) in self.verified

    def require_verified(
        self, *, ontology: str, world_model: str, probe: str, mapping: str
    ) -> None:
        """未検証の組み合わせを拒否する。

        Args:
            ontology: オントロジー版。
            world_model: WM 版。
            probe: プローブ版。
            mapping: 写像版。

        Raises:
            RegroundingError: 未検証の場合。
        """
        if not self.is_verified(
            ontology=ontology, world_model=world_model, probe=probe, mapping=mapping
        ):
            raise RegroundingError(
                f"the combination ontology={ontology} wm={world_model} probe={probe} "
                f"mapping={mapping} has not been verified; run the promotion pipeline "
                "(impact analysis → canary → regrounding → diff review) before serving it "
                "(design §12.4)"
            )

    def affected_by(self, *, world_model: str) -> tuple[tuple[str, str, str, str], ...]:
        """ある WM 版を含む組み合わせ（影響分析に使う）。

        Args:
            world_model: WM 版。
        """
        return tuple(
            combination for combination in sorted(self.verified) if combination[1] == world_model
        )
