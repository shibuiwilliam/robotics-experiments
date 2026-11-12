"""説明サービス（設計書 §8.8）。

> 出力：説明グラフ ＝（記号側：適用規則・参照記録・計画）∪（潜在側：寄与プローブ・観測窓・
> 帰属）。三源のうちどれが事後を支配したかの寄与率を含む。

**説明は信念と同じ版で再構成できなければならない。** 「なぜ 9/12 にこう判断したのか」を今日の
モデルと今日の規則で説明すると、当時の判断とは別の話になる。そのため :class:`Explanation` は
``as_of``（取引時刻）と、その時点のモデル版・オントロジー版を必ず持つ。版を持たない説明は
監査に使えない（非機能要求の監査性）。

**LLM はここに入らない。** 文章化は任意で上に載せる層であり、説明グラフそのものは決定的に
組み立てる（不変条件 11: LLM を信念更新・照合・事象昇格のホットパスから呼ばない。説明の
**生成**はホットパスではないが、説明の**内容**が LLM に依存すると、同じ as-of で違う説明が
出てしまい監査が成立しない）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sbp.core.records import PlanItem, Record
    from sbp.reasoning.belief import BeliefUpdate, SourceContribution
    from sbp.reasoning.reconciliation.discrepancy import Discrepancy

__all__ = [
    "Explanation",
    "LatentSide",
    "SymbolicSide",
    "explain_belief",
    "explain_discrepancy",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SymbolicSide:
    """説明グラフの記号側（設計書 §8.8）。

    Attributes:
        rules: 適用した規則の参照（``ontology/process/*.lp`` の規則名、SHACL 形状）。
        records: 参照した業務記録の IRI。
        plans: 参照した計画項目の IRI。
        norms: 参照した規範の IRI。
        excluded_records: 監査隔離で**使わなかった**記録の IRI。
    """

    rules: tuple[str, ...] = ()
    records: tuple[str, ...] = ()
    plans: tuple[str, ...] = ()
    norms: tuple[str, ...] = ()
    excluded_records: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """記号側の根拠が何も無いか。"""
        return not (self.rules or self.records or self.plans or self.norms)


@dataclass(frozen=True, slots=True)
class LatentSide:
    """説明グラフの潜在側（設計書 §8.8）。

    Attributes:
        probes: 寄与したプローブの IRI。
        probe_outputs: プローブの較正済み出力（``probes`` と同じ順）。
        observation_window: 観測窓（``[start, end)`` の ISO 文字列）。
        wm_version: 使った WM の版 IRI。
        attribution: 潜在次元への帰属（次元名 → 寄与）。
    """

    probes: tuple[str, ...] = ()
    probe_outputs: tuple[float, ...] = ()
    observation_window: tuple[str, str] | None = None
    wm_version: str | None = None
    attribution: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """プローブと出力の対応を検証する。"""
        if self.probe_outputs and len(self.probe_outputs) != len(self.probes):
            raise ReasoningError(
                f"{len(self.probes)} probes but {len(self.probe_outputs)} outputs; the "
                "explanation must say which probe produced which value"
            )

    @property
    def is_empty(self) -> bool:
        """潜在側の根拠が何も無いか。"""
        return not self.probes


@dataclass(frozen=True, slots=True)
class Explanation:
    """説明グラフ（設計書 §8.8）。

    Attributes:
        subject: 説明対象（アサーション・不一致・期待の IRI）。
        as_of: この説明が再構成している取引時刻。
        symbolic: 記号側の根拠。
        latent: 潜在側の根拠。
        contribution: 三源の寄与率。
        dominant: 事後を支配した源（``observed`` / ``record`` / ``plan``）。
        posterior: 説明対象の事後確率（不一致の説明では深刻度）。
        ontology_version: 当時のオントロジー版。
        audit_note: 監査隔離の来歴メモ。
        narrative: 任意の文章化（LLM で後から載せる。空でも説明は成立する）。
    """

    subject: str
    as_of: datetime
    symbolic: SymbolicSide
    latent: LatentSide
    contribution: SourceContribution | None
    dominant: str | None
    posterior: float
    ontology_version: str
    audit_note: str = ""
    narrative: str = ""

    def __post_init__(self) -> None:
        """説明が根拠を持つことを検証する。"""
        if self.symbolic.is_empty and self.latent.is_empty:
            raise ReasoningError(
                f"explanation of {self.subject} cites no evidence on either side; an "
                "explanation without grounds is not an explanation"
            )

    @property
    def is_reproducible(self) -> bool:
        """当時の版を特定できるか（監査に使えるか）。"""
        return bool(self.ontology_version)

    def with_narrative(self, narrative: str) -> Explanation:
        """文章化を載せた新しい説明を返す。

        文章は**説明グラフを置き換えない**。LLM が生成するのはこのフィールドだけで、根拠の
        集合はすでに決まっている。

        Args:
            narrative: 人が読む説明文。
        """
        return Explanation(
            subject=self.subject,
            as_of=self.as_of,
            symbolic=self.symbolic,
            latent=self.latent,
            contribution=self.contribution,
            dominant=self.dominant,
            posterior=self.posterior,
            ontology_version=self.ontology_version,
            audit_note=self.audit_note,
            narrative=narrative,
        )


def explain_belief(
    update: BeliefUpdate,
    *,
    subject: str,
    probes: Sequence[str] = (),
    probe_outputs: Sequence[float] = (),
    records: Sequence[Record] = (),
    excluded_records: Sequence[Record] = (),
    plan: PlanItem | None = None,
    rules: Sequence[str] = (),
    wm_version: str | None = None,
    attribution: dict[str, float] | None = None,
    ontology_version: str,
) -> Explanation:
    """信念更新 1 回を説明する。

    ``update`` が持つ三源の寄与率をそのまま使う——説明のために寄与率を再計算すると、説明と
    信念が食い違いうる（設計書 §15 の「説明が後付けになる」失敗モード）。

    Args:
        update: 説明する信念更新。
        subject: 説明対象のアサーション IRI。
        probes: 寄与したプローブの IRI。
        probe_outputs: プローブの較正済み出力。
        records: 使った業務記録。
        excluded_records: 監査隔離で除外した記録。
        plan: 参照した計画項目。
        rules: 適用した規則の参照。
        wm_version: WM の版 IRI。
        attribution: 潜在次元への帰属。
        ontology_version: 当時のオントロジー版。

    Returns:
        説明グラフ。
    """
    window = (
        ensure_utc(update.at).isoformat(),
        ensure_utc(update.at).isoformat(),
    )
    explanation = Explanation(
        subject=subject,
        as_of=ensure_utc(update.at),
        symbolic=SymbolicSide(
            rules=tuple(rules),
            records=tuple(str(record.record_id) for record in records),
            plans=() if plan is None else (str(plan.plan_item_id),),
            excluded_records=tuple(str(record.record_id) for record in excluded_records),
        ),
        latent=LatentSide(
            probes=tuple(probes),
            probe_outputs=tuple(probe_outputs),
            observation_window=window,
            wm_version=wm_version,
            attribution=dict(attribution or {}),
        ),
        contribution=update.contribution,
        dominant=update.contribution.dominant,
        posterior=update.posterior,
        ontology_version=ontology_version,
        audit_note=update.audit_note,
    )
    log.info(
        "reasoning.explanation_built",
        subject=subject,
        dominant=explanation.dominant,
        as_of=explanation.as_of.isoformat(),
    )
    return explanation


def explain_discrepancy(
    discrepancy: Discrepancy,
    *,
    update: BeliefUpdate | None = None,
    probes: Sequence[str] = (),
    probe_outputs: Sequence[float] = (),
    rules: Sequence[str] = (),
    norms: Sequence[str] = (),
    wm_version: str | None = None,
) -> Explanation:
    """不一致 1 件を説明する。

    不一致の説明は「なぜこれが食い違いなのか」であり、そのためには**両側**が必要である。
    片側しか挙げられない説明は、現場から見ると「機械がそう言っている」でしかない。

    ``posterior`` には深刻度スコアを入れる（不一致は確率ではなく順位を持つ）。寄与率は信念
    更新由来なので、``update`` が無ければ ``None`` になる。

    Args:
        discrepancy: 説明する不一致。
        update: 根拠になった信念更新（あれば寄与率を載せる）。
        probes: 寄与したプローブの IRI。
        probe_outputs: プローブの較正済み出力。
        rules: 適用した検出規則（既定は不一致自身の ``detector``）。
        norms: 参照した規範の IRI。
        wm_version: WM の版 IRI。

    Returns:
        説明グラフ。
    """
    end = discrepancy.interval.end
    window = (
        discrepancy.interval.start.isoformat(),
        end.isoformat() if end is not None else "",
    )
    explanation = Explanation(
        subject=str(discrepancy.discrepancy_id),
        as_of=ensure_utc(discrepancy.detected_at),
        symbolic=SymbolicSide(
            rules=tuple(rules) or (discrepancy.detector,),
            records=discrepancy.evidence,
            norms=tuple(norms),
        ),
        latent=LatentSide(
            probes=tuple(probes),
            probe_outputs=tuple(probe_outputs),
            observation_window=window,
            wm_version=wm_version,
        ),
        contribution=None if update is None else update.contribution,
        dominant=None if update is None else update.contribution.dominant,
        posterior=discrepancy.severity.score,
        ontology_version=discrepancy.ontology_version,
        audit_note="" if update is None else update.audit_note,
    )
    log.info(
        "reasoning.discrepancy_explained",
        discrepancy=str(discrepancy.discrepancy_id),
        kind=int(discrepancy.kind),
        dominant=explanation.dominant,
    )
    return explanation
