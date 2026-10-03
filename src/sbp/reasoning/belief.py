"""信念更新エンジン（設計書 §8.1、reasoning-actions ルール）。

三源（物理観測・業務記録・計画）を 1 つの事後信念に統合する。これが SBP の中核で、
中核テーゼ 1「真実ではなく信念を管理する」の実装本体である。

**因子**（設計書 §8.1 の表）:

| 因子 | 由来 | 形 |
|---|---|---|
| 遷移 ``P(s_t \\| s_{t-1}, a, plan)`` | 事象計算 T + 滞在時間分布 + 計画遵守率 | 半マルコフ |
| 観測尤度 ``P(probe \\| s)`` | 較正済みプローブ（ソフトエビデンス） | 尤度比 |
| 記録尤度 ``P(record \\| s, source)`` | 信頼性モデル | 混同行列 |

計算は**対数オッズ空間**で行う（CLAUDE.md 数値規約）。事前の対数オッズに各証拠の対数尤度比を
足すだけになり、数値が安定し、**どの源がどれだけ事後を動かしたか**がそのまま分かる。
後者が重要で、設計書 §8.1 が要求する三源寄与率 ``contrib: {observed, record, plan}`` は
足した対数尤度比の絶対値の比として自然に出る。説明サービス（M3-07）がこれを使う。

**監査隔離**（不変条件 6）: 記録尤度の計算は :class:`~sbp.reasoning.audit.AuditMask` を
必須引数として取る。マスクなしで呼べる経路を作らない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

import numpy as np

from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import ensure_utc
from sbp.reasoning.audit import MaskedEvidence

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.core.records import PlanItem, Record
    from sbp.integration.reliability import ReliabilityModel
    from sbp.reasoning.audit import AuditMask

__all__ = [
    "BeliefFilter",
    "BeliefUpdate",
    "DwellTime",
    "SourceContribution",
    "TransitionPrior",
    "update_belief",
]

log = get_logger(__name__)

_LOG_ODDS_CLAMP: Final = 20.0
"""対数オッズの上限（確率で約 1 - 2e-9）。断定を避けるための飽和。"""


def _log_odds(probability: float) -> float:
    """確率を対数オッズに変換する（0 と 1 で飽和させる）。"""
    clamped = min(max(probability, 1e-9), 1.0 - 1e-9)
    return float(np.clip(math.log(clamped / (1.0 - clamped)), -_LOG_ODDS_CLAMP, _LOG_ODDS_CLAMP))


def _probability(log_odds: float) -> float:
    """対数オッズを確率に戻す。"""
    clamped = float(np.clip(log_odds, -_LOG_ODDS_CLAMP, _LOG_ODDS_CLAMP))
    return 1.0 / (1.0 + math.exp(-clamped))


@dataclass(frozen=True, slots=True)
class DwellTime:
    """フルーエントの滞在時間分布（半マルコフ、設計書 §8.1）。

    「どれだけ続いたか」で遷移確率が変わるのが半マルコフの要点である。設備が 10 秒前に起動した
    なら次の瞬間も稼働している確率は非常に高いが、8 時間連続稼働なら停止が近いかもしれない。
    指数分布を仮定する純マルコフでは、この「続いた長さ」の情報が捨てられる。

    Attributes:
        median_s: 滞在時間の中位数（秒）。
        shape: 分布の形状（ワイブルの形状パラメータ。1 なら指数分布＝記憶なし、
            1 より大きいと「続くほど終わりやすい」、小さいと「続くほど続きやすい」）。
    """

    median_s: float
    shape: float = 1.5

    def __post_init__(self) -> None:
        if self.median_s <= 0.0:
            raise ReasoningError(f"median dwell time must be positive, got {self.median_s}")
        if self.shape <= 0.0:
            raise ReasoningError(f"Weibull shape must be positive, got {self.shape}")

    def survival(self, elapsed: timedelta) -> float:
        """``elapsed`` だけ続いた状態が、さらに続いている確率（生存関数）。

        Args:
            elapsed: これまで続いた長さ。
        """
        seconds = max(elapsed.total_seconds(), 0.0)
        # ワイブル: S(t) = exp(-(t/λ)^k)、中位数から λ を決める
        scale = self.median_s / (math.log(2.0) ** (1.0 / self.shape))
        return float(math.exp(-((seconds / scale) ** self.shape)))


@dataclass(frozen=True, slots=True)
class TransitionPrior:
    """遷移事前分布（事象計算 T + 滞在時間 + 計画遵守率、設計書 §8.1）。

    事前は**マルコフ遷移の式**で計算する。

    ``P(s_t) = P(s_{t-1}) · P(継続) + (1 − P(s_{t-1})) · P(開始)``

    「継続確率を対数オッズに変換して足す」のは誤りである。長寿命の状態では継続確率が 1 に
    近づき、それを証拠として足すと事前が 1 に飽和して観測が効かなくなる。継続確率は
    「状態が崩れにくい」ことを表すのであって、「状態が真である証拠」ではない。

    計画は**混合**として入れる。``prior = (1 − α) · markov + α · 遵守率``。計画は事前分布を
    与えるが（設計書 §7.4）、実際に起きたこと（ダイナミクス）を上書きしてはいけない。
    既定の α = 0.2 は、設計書 §14 のトレース（計画と記録は稼働、観測は停止 → 事後 0.14）を
    再現する値である。

    Attributes:
        dwell: フルーエントごとの滞在時間分布。
        step: 更新の刻み幅。
        start_rate: 事象が無いときに偽の状態が真になる確率（1 刻みあたり）。事象計算では
            事象なしに状態は変わらないため小さい値にする。
        plan_weight: 計画が事前に占める重み α（0〜1）。
    """

    dwell: dict[str, DwellTime] = field(default_factory=dict)
    step: timedelta = timedelta(seconds=10)
    start_rate: float = 0.001
    plan_weight: float = 0.2

    def __post_init__(self) -> None:
        if not 0.0 <= self.plan_weight <= 1.0:
            raise ReasoningError(f"plan_weight must be in [0, 1], got {self.plan_weight}")
        if not 0.0 <= self.start_rate < 1.0:
            raise ReasoningError(f"start_rate must be in [0, 1), got {self.start_rate}")

    def prior_log_odds(
        self,
        *,
        fluent: str,
        previous: float | None,
        elapsed: timedelta,
        plan: PlanItem | None,
        at: datetime,
    ) -> tuple[float, float]:
        """事前の対数オッズと、そのうち計画が占める分を返す。

        計画の寄与を分けて返すのは、三源寄与率（設計書 §8.1）を出すためである。事前に
        混ぜ込んでしまうと「計画がどれだけ効いたか」が後から分からない。

        Args:
            fluent: 対象フルーエントのローカル名。
            previous: 直前の事後確率（``None`` なら計画または 0.5 から始める）。
            elapsed: 直前の状態が続いた長さ。
            plan: 当該時刻に有効な計画項目（無ければ ``None``）。
            at: 更新時刻。

        Returns:
            ``(事前の対数オッズ, 計画由来の対数オッズの差分)``。後者は「計画を混ぜたことで
            事前がどれだけ動いたか」であり、寄与率の計算に使う。
        """
        plan_active = plan is not None and plan.planned_interval.contains_instant(ensure_utc(at))

        if previous is None:
            # 初期状態。計画があればそれを事前に、無ければ無情報（0.5）
            if plan_active and plan is not None:
                prior = plan.adherence_prior
                return _log_odds(prior), _log_odds(prior)
            return 0.0, 0.0

        # マルコフ遷移（継続確率と開始確率から次の時点の確率を出す）
        dwell = self.dwell.get(fluent)
        persist = 1.0 if dwell is None else _continuation_probability(dwell, elapsed, self.step)
        markov = previous * persist + (1.0 - previous) * self.start_rate

        if not plan_active or plan is None:
            return _log_odds(markov), 0.0

        # 計画を混合で入れる（ダイナミクスを上書きしない）
        blended = (1.0 - self.plan_weight) * markov + self.plan_weight * plan.adherence_prior
        return _log_odds(blended), _log_odds(blended) - _log_odds(markov)


def _continuation_probability(dwell: DwellTime, elapsed: timedelta, step: timedelta) -> float:
    """``elapsed`` だけ続いた状態がさらに 1 刻み続く確率（生存関数の比）。"""
    current = dwell.survival(elapsed)
    if current <= 0.0:
        return 0.0
    return min(dwell.survival(elapsed + step) / current, 1.0)


@dataclass(frozen=True, slots=True)
class SourceContribution:
    """三源の寄与率（設計書 §8.1 の ``contrib``）。

    各源が事後をどれだけ動かしたかを、足した対数尤度比の絶対値の比で表す。「どの源が事後を
    支配したか」が説明サービス（M3-07）の出発点になる。

    Attributes:
        observed: 物理観測（プローブ）の寄与。
        record: 業務記録の寄与。
        plan: 計画の寄与。
    """

    observed: float
    record: float
    plan: float

    @property
    def dominant(self) -> str:
        """最も事後を動かした源。"""
        return max(
            (("observed", self.observed), ("record", self.record), ("plan", self.plan)),
            key=lambda kv: kv[1],
        )[0]

    def as_dict(self) -> dict[str, float]:
        """来歴に入れる形。"""
        return {"observed": self.observed, "record": self.record, "plan": self.plan}


@dataclass(frozen=True, slots=True)
class BeliefUpdate:
    """1 回の信念更新の結果。

    Attributes:
        fluent: 対象フルーエント。
        entity: 対象実体 IRI。
        at: 更新時刻。
        posterior: 事後確率。
        prior: 事前確率。
        contribution: 三源の寄与率。
        excluded_records: 監査隔離で除外した記録の数。
        audit_note: 監査隔離の来歴メモ。
    """

    fluent: str
    entity: str
    at: datetime
    posterior: float
    prior: float
    contribution: SourceContribution
    excluded_records: int
    audit_note: str

    def to_assertion(self, *, predicate: str, provenance: str, ontology_version: str) -> Assertion:
        """事後信念を ``inferred`` アサーションにする。

        不変条件 3 により様相は必ず ``inferred``（信念更新の出力は推定である）。

        Args:
            predicate: フルーエント型の IRI。
            provenance: 来歴バンドルの IRI。
            ontology_version: オントロジー版。
        """
        return Assertion(
            subject=self.entity,
            predicate=predicate,
            object=True,
            valid_from=self.at,
            tx_from=self.at,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=self.posterior),
            provenance=provenance,
            ontology_version=ontology_version,
        )


def update_belief(
    *,
    fluent: str,
    entity: str,
    at: datetime,
    previous: float | None,
    elapsed: timedelta,
    transition: TransitionPrior,
    probe_probabilities: Sequence[float] = (),
    records: Sequence[tuple[Record, bool]] = (),
    reliability: dict[tuple[str, str], ReliabilityModel] | None = None,
    plan: PlanItem | None = None,
    mask: AuditMask,
    weak_evidence_threshold: float = 0.3,
) -> BeliefUpdate:
    """三源を統合して事後信念を計算する（設計書 §8.1 の擬似コード）。

    手順:

    1. 遷移事前（T + 滞在時間 + 計画遵守率）
    2. プローブ尤度を掛ける（較正済みなので対数オッズをそのまま足せる）
    3. 記録尤度を掛ける（信頼性モデルの混同行列から対数尤度比）
    4. 正規化して事後を得る

    ``mask`` は**必須のキーワード引数**である。監査文脈で呼ぶときにマスクを忘れる経路を
    作らないため（不変条件 6）。監査対象が無い場合は :func:`~sbp.reasoning.audit.no_audit`
    を明示的に渡す。

    Args:
        fluent: 対象フルーエントのローカル名。
        entity: 対象実体 IRI。
        at: 更新時刻。
        previous: 直前の事後確率（``None`` なら初期状態）。
        elapsed: 直前の状態が続いた長さ。
        transition: 遷移事前分布。
        probe_probabilities: プローブの較正済み出力（複数プローブなら複数）。
        records: ``(記録, 記録が主張する真偽)`` の列。
        reliability: ``(情報源, 記録型)`` → 信頼性モデル。
        plan: 当該時刻に有効な計画項目。
        mask: 監査隔離マスク（必須）。
        weak_evidence_threshold: この確信度未満のプローブ出力は弱い証拠として扱い、
            寄与を半分にする（設計書 §5.7）。

    Returns:
        更新結果（事後・事前・三源寄与率・隔離の記録）。

    Raises:
        ReasoningError: 記録があるのに信頼性モデルが与えられていない場合。
            未知の情報源を既定で信頼しない（設計書 §7.3）。
    """
    prior_log_odds, plan_log_odds = transition.prior_log_odds(
        fluent=fluent, previous=previous, elapsed=elapsed, plan=plan, at=at
    )

    # ---- 2. 観測尤度（プローブ）----
    observed_log_odds = 0.0
    for probability in probe_probabilities:
        contribution = _log_odds(probability)
        if abs(probability - 0.5) < weak_evidence_threshold / 2.0:
            # 弱い証拠は寄与を半分にする（設計書 §5.7 の「弱く扱う」）
            contribution *= 0.5
        observed_log_odds += contribution

    # ---- 3. 記録尤度（監査隔離を通す）----
    masked: MaskedEvidence[tuple[Record, bool]] = _filter_record_claims(records, mask)
    record_log_odds = 0.0
    if masked.kept:
        if reliability is None:
            raise ReasoningError(
                "records were supplied without reliability models; records from unknown "
                "sources must not be trusted by default (design §7.3)"
            )
        for record, claims_true in masked.kept:
            model = reliability.get((record.source, record.record_type))
            if model is None:
                raise ReasoningError(
                    f"no reliability model for source={record.source} "
                    f"record_type={record.record_type}"
                )
            if model.confusion is None:
                # 真偽を主張しない記録型（計画など）は尤度に効かせない
                continue
            record_log_odds += model.confusion.log_likelihood_ratio(record_says=claims_true)

    posterior_log_odds = prior_log_odds + observed_log_odds + record_log_odds
    posterior = _probability(posterior_log_odds)

    contribution = SourceContribution(
        observed=abs(observed_log_odds),
        record=abs(record_log_odds),
        plan=abs(plan_log_odds),
    )

    update = BeliefUpdate(
        fluent=fluent,
        entity=entity,
        at=ensure_utc(at),
        posterior=posterior,
        prior=_probability(prior_log_odds),
        contribution=contribution,
        excluded_records=masked.exclusion_count,
        audit_note=mask.provenance_note(),
    )
    log.debug(
        "reasoning.belief_updated",
        entity=entity,
        fluent=fluent,
        prior=update.prior,
        posterior=posterior,
        dominant=contribution.dominant,
        contrib=contribution.as_dict(),
        excluded_records=masked.exclusion_count,
    )
    return update


def _filter_record_claims(
    records: Sequence[tuple[Record, bool]], mask: AuditMask
) -> MaskedEvidence[tuple[Record, bool]]:
    """記録の主張からマスク対象を除く。"""

    kept: list[tuple[Record, bool]] = []
    excluded: list[tuple[Record, bool]] = []
    for pair in records:
        (excluded if mask.excludes_record(pair[0]) else kept).append(pair)
    if excluded:
        log.info(
            "reasoning.audit_isolation_applied",
            excluded=len(excluded),
            kept=len(kept),
            reason=mask.reason,
        )
    return MaskedEvidence(kept=tuple(kept), excluded=tuple(excluded), mask=mask)


@dataclass(slots=True)
class BeliefFilter:
    """実体単位の前向きフィルタ（設計書 §8.1）。

    信念更新は 1 回で終わる計算ではなく**時系列を進む前向きフィルタ**である。設計書 §8.1 の
    擬似コードが毎刻み呼ばれる形になっているのはそのためで、§14 の「事後 0.14」も
    停止後に観測が続くことで到達する値である（1 ステップで 0.95 から 0.14 には落ちない）。

    状態として「直前の事後」と「その状態が続いた長さ」を持つ。後者が半マルコフの要点である。

    Attributes:
        fluent: 対象フルーエントのローカル名。
        entity: 対象実体 IRI。
        transition: 遷移事前分布。
        posterior: 現在の事後確率（``None`` なら未初期化）。
        elapsed: 現在の状態が続いた長さ。
    """

    fluent: str
    entity: str
    transition: TransitionPrior
    posterior: float | None = None
    elapsed: timedelta = timedelta(0)

    def step(
        self,
        *,
        at: datetime,
        probe_probabilities: Sequence[float] = (),
        records: Sequence[tuple[Record, bool]] = (),
        reliability: dict[tuple[str, str], ReliabilityModel] | None = None,
        plan: PlanItem | None = None,
        mask: AuditMask,
    ) -> BeliefUpdate:
        """1 刻み進める。

        ``mask`` は必須（不変条件 6）。監査対象が無ければ
        :func:`~sbp.reasoning.audit.no_audit` を明示的に渡す。

        Args:
            at: この刻みの時刻。
            probe_probabilities: プローブの較正済み出力。
            records: ``(記録, 記録が主張する真偽)`` の列。
            reliability: 信頼性モデル。
            plan: 当該時刻に有効な計画項目。
            mask: 監査隔離マスク。

        Returns:
            この刻みの更新結果。
        """
        update = update_belief(
            fluent=self.fluent,
            entity=self.entity,
            at=at,
            previous=self.posterior,
            elapsed=self.elapsed,
            transition=self.transition,
            probe_probabilities=probe_probabilities,
            records=records,
            reliability=reliability,
            plan=plan,
            mask=mask,
        )
        # 状態が反転したら滞在時間をリセットする（半マルコフの「続いた長さ」）
        flipped = self.posterior is not None and (self.posterior >= 0.5) != (
            update.posterior >= 0.5
        )
        self.elapsed = self.transition.step if flipped else self.elapsed + self.transition.step
        self.posterior = update.posterior
        return update

    def run(
        self,
        timestamps: Sequence[datetime],
        *,
        probe_series: Sequence[Sequence[float]] = (),
        records: Sequence[tuple[Record, bool]] = (),
        reliability: dict[tuple[str, str], ReliabilityModel] | None = None,
        plan: PlanItem | None = None,
        mask: AuditMask,
    ) -> list[BeliefUpdate]:
        """系列を通して回す。

        Args:
            timestamps: 各刻みの時刻。
            probe_series: 各刻みのプローブ出力（``timestamps`` と同じ長さ）。
            records: 全刻みに効かせる記録（業務記録は刻みより粗い粒度で来る）。
            reliability: 信頼性モデル。
            plan: 計画項目。
            mask: 監査隔離マスク。

        Returns:
            各刻みの更新結果。

        Raises:
            ReasoningError: ``probe_series`` の長さが ``timestamps`` と合わない場合。
        """
        if probe_series and len(probe_series) != len(timestamps):
            raise ReasoningError(
                f"probe_series has {len(probe_series)} entries but {len(timestamps)} "
                "timestamps were given"
            )
        out: list[BeliefUpdate] = []
        for index, at in enumerate(timestamps):
            probabilities = probe_series[index] if probe_series else ()
            out.append(
                self.step(
                    at=at,
                    probe_probabilities=probabilities,
                    records=records,
                    reliability=reliability,
                    plan=plan,
                    mask=mask,
                )
            )
        return out
