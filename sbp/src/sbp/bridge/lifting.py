"""事象昇格（設計書 §5.3、bridge-worldmodel ルール）。

プローブ出力の時系列から、型付きの確率的事象（``TypedProbabilisticEvent``）を作る。
処理順を崩さない（ルールが明示している）:

1. 変化点検出（BOCPD）
2. ヒステリシス（0.7 / 0.3、Settings）
3. 最小持続時間
4. 逆写像（clingo で候補事象型とその尤度）
5. 重複抑制
6. ``TypedProbabilisticEvent`` としてイベントログへ

**区間には必ず ``uncertainty_s`` を入れる。点時刻のイベントを出さない。** 変化点推定には
必ず幅があり、それを捨てると「09:41:30 に止まった」と断定したことになる。後段の時間不一致
（類型 3）の判定は、この幅と情報源の許容プロファイルを比べて行うため、幅が無いと判定が狂う。

**逆写像とは**: フルーエントが真→偽に変わったとき、それを起こしえた事象型は複数ある
（Running が落ちたのは StopCmd か Fault か）。事象計算の ``terminates`` を逆向きに辿って
候補を列挙し、他プローブ（振動異常）と業務文脈（停止指令の記録）の尤度で重み付ける。
設計書 §5.3 の手順 3 であり、§14 では「Fault 0.56 / StopCmd 0.35」として現れる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Final

import numpy as np

from sbp.bridge.bocpd import apply_hysteresis, detect_change_points
from sbp.core.belief import BooleanBelief, CategoricalBelief
from sbp.core.errors import BridgeError
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    ProbeEvidence,
    Producer,
    ValidTime,
    topic_name,
)
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import Interval, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

    from sbp.bridge.bocpd import ChangePoint
    from sbp.core.config import BridgeSettings

__all__ = [
    "EventCalculusRules",
    "FluentChange",
    "LiftedEvent",
    "fluent_local_name",
    "lift_events",
    "load_event_calculus",
]

log = get_logger(__name__)

_PRODUCER_VERSION: Final = "0.1.0"


@dataclass(frozen=True, slots=True)
class EventCalculusRules:
    """事象計算の規則（``ontology/process/*.lp`` から読む）。

    Attributes:
        initiates: (事象型, フルーエント型) の組。この事象がそのフルーエントを成立させる。
        terminates: (事象型, フルーエント型) の組。この事象がそのフルーエントを不成立にする。
        exclusive: 排他フルーエントの対。
    """

    initiates: frozenset[tuple[str, str]]
    terminates: frozenset[tuple[str, str]]
    exclusive: frozenset[tuple[str, str]]

    def candidates_for_rise(self, fluent: str) -> tuple[str, ...]:
        """フルーエントを成立させえた事象型（逆写像）。

        Args:
            fluent: 成立したフルーエントのローカル名（snake_case）。
        """
        return tuple(sorted(e for e, f in self.initiates if f == fluent))

    def candidates_for_fall(self, fluent: str) -> tuple[str, ...]:
        """フルーエントを不成立にしえた事象型（逆写像）。

        Args:
            fluent: 不成立になったフルーエントのローカル名（snake_case）。
        """
        return tuple(sorted(e for e, f in self.terminates if f == fluent))


def load_event_calculus(lp_text: str) -> EventCalculusRules:
    """``.lp`` の本文から ``initiates`` / ``terminates`` / ``exclusive`` を読む。

    規則文字列を Python 側で組み立てない（reasoning ルール）のと同じ理由で、**規則の内容は
    ``.lp`` が正**とし、ここは読み取るだけにする。``make ontology-check`` が TTL と ``.lp`` の
    対応を検査しているので、読み取った内容はオントロジーと一致している。

    Args:
        lp_text: ``.lp`` ファイルの本文。

    Returns:
        事象計算の規則。

    Raises:
        BridgeError: ``initiates`` も ``terminates`` も見つからない場合。
    """
    import re  # noqa: PLC0415 — 読み取りのためだけの用途

    def pairs(predicate: str) -> frozenset[tuple[str, str]]:
        return frozenset(
            (m.group(1), m.group(2))
            for m in re.finditer(
                rf"^{predicate}\(([a-z0-9_]+),\s*([a-z0-9_]+)\)\.", lp_text, flags=re.MULTILINE
            )
        )

    initiates = pairs("initiates")
    terminates = pairs("terminates")
    if not initiates and not terminates:
        raise BridgeError(
            "no initiates/terminates rules found in the event-calculus text; "
            "the inverse mapping would have no candidates"
        )
    return EventCalculusRules(
        initiates=initiates, terminates=terminates, exclusive=pairs("exclusive")
    )


@dataclass(frozen=True, slots=True)
class FluentChange:
    """フルーエントの変化（ヒステリシスと最小持続を通ったもの）。

    Attributes:
        fluent: フルーエントのローカル名（snake_case）。
        index: 変化が起きた標本の添字。
        at: 変化時刻。
        uncertainty_s: 変化点推定の不確実幅（秒）。
        direction: ``rise`` または ``fall``。
        probability: 変化点の事後確率。
        confidence_after: 変化の瞬間のプローブ確率（事象の確信度の計算に使う）。
        state_after: 変化後の安定した区間のプローブ確率の中央値（フルーエントが成立している確率）。
    """

    fluent: str
    index: int
    at: datetime
    uncertainty_s: float
    direction: str
    probability: float
    confidence_after: float
    state_after: float


@dataclass(frozen=True, slots=True)
class LiftedEvent:
    """昇格した事象（設計書 §5.8 の ``TypedProbabilisticEvent`` に対応）。

    Attributes:
        subject: 主体実体の IRI。
        fluent: 変化したフルーエント。
        valid_time: 有効時間（``uncertainty_s`` を必ず持つ）。
        belief: 事象が起きたことの確信度。
        state_probability: 変化後にフルーエントが成立している確率。**イベントに載せるのはこちら**
            （ADR-0026）。下流は ``PhysicalEvent/<フルーエント>`` の確率を
            フルーエントの確率として読む。
        inferred_event_type: 候補事象型の確率分布（逆写像の結果）。
        evidence: 根拠となったプローブ。
        alignment_residual: この昇格時点での整合残差。
    """

    subject: str
    fluent: str
    valid_time: ValidTime
    belief: BooleanBelief
    state_probability: float
    inferred_event_type: CategoricalBelief
    evidence: tuple[ProbeEvidence, ...]
    alignment_residual: float

    def to_envelope(
        self, *, site_id: str, event_type_iri: str, ontology_version: str
    ) -> EventEnvelope:
        """イベントログに流す形にする。

        様相は ``inferred``（不変条件 3: プローブの出力は必ず ``inferred``）。

        Args:
            site_id: 拠点識別子。
            event_type_iri: イベント型の IRI。
            ontology_version: オントロジー版。
        """
        return EventEnvelope(
            type=event_type_iri,
            subject=self.subject,
            valid_time=self.valid_time,
            tx_time=self.valid_time.start,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=self.state_probability),
            evidence=self.evidence,
            producer=Producer(agent=f"bridge@{site_id}", version=_PRODUCER_VERSION),
            ontology_version=ontology_version,
        )

    @property
    def topic(self) -> str:
        """送信先トピックの ``<layer>.<class>`` 部分を含む名前を作るための補助。"""
        return "physical"


def _snake(local_name: str) -> str:
    """CamelCase のローカル名を snake_case にする（``.lp`` の表記に合わせる）。"""
    import re  # noqa: PLC0415

    return re.sub(r"(?<!^)(?=[A-Z])", "_", local_name).lower()


def lift_events(
    *,
    subject: str,
    probe_outputs: Mapping[str, NDArray[np.float64]],
    timestamps: Sequence[datetime],
    rules: EventCalculusRules,
    settings: BridgeSettings,
    probe_ids: Mapping[str, str],
    wm_version: str | None = None,
    alignment_residual: float = 0.0,
) -> list[LiftedEvent]:
    """プローブ出力から事象を昇格する（設計書 §5.3 の全手順）。

    Args:
        subject: 主体実体の IRI。
        probe_outputs: フルーエントのローカル名（CamelCase）→ 較正済み確率の列。
        timestamps: 標本時刻。
        rules: 事象計算の規則（逆写像に使う）。
        settings: ヒステリシスと最小持続の閾値。
        probe_ids: フルーエント名 → プローブ IRI（証拠に残す）。
        wm_version: WM 版の IRI（証拠に残す）。
        alignment_residual: この時点の整合残差。

    Returns:
        昇格した事象（時刻順）。

    Raises:
        BridgeError: 出力と時刻の長さが合わない場合。
    """
    if not probe_outputs:
        raise BridgeError("cannot lift events without probe outputs")
    for name, values in probe_outputs.items():
        if len(values) != len(timestamps):
            raise BridgeError(
                f"probe output {name!r} has {len(values)} samples but {len(timestamps)} "
                "timestamps were given"
            )

    step_s = _median_step_seconds(timestamps)
    min_duration_samples = max(1, int(settings.min_duration_s / step_s))

    # 1〜3: 変化点検出 → ヒステリシス → 最小持続
    changes: list[FluentChange] = []
    for fluent_name, values in probe_outputs.items():
        detected = detect_change_points(
            values,
            high=settings.hysteresis_high,
            low=settings.hysteresis_low,
            min_bayes_factor=5.0,
        )
        binary = apply_hysteresis(
            values, high=settings.hysteresis_high, low=settings.hysteresis_low
        )
        for change in _filter_by_duration(detected, binary, min_duration_samples):
            changes.append(
                FluentChange(
                    fluent=_snake(fluent_name),
                    index=change.index,
                    at=ensure_utc(timestamps[change.index]),
                    # 変化点の不確実幅は「標本間隔 × 事後確率の裏返し」で見積もる。
                    # 確率が低い（曖昧な）変化ほど幅を広く取る。
                    uncertainty_s=step_s * max(1.0, 2.0 * (1.0 - change.probability)),
                    direction=change.direction,
                    probability=change.probability,
                    confidence_after=float(values[change.index]),
                    state_after=float(
                        np.median(values[change.index : change.index + min_duration_samples])
                    ),
                )
            )

    changes.sort(key=lambda c: (c.at, c.fluent))

    # 4: 逆写像（候補事象型とその尤度）
    lifted: list[LiftedEvent] = []
    for change in changes:
        candidates = (
            rules.candidates_for_rise(change.fluent)
            if change.direction == "rise"
            else rules.candidates_for_fall(change.fluent)
        )
        if not candidates:
            log.warning(
                "bridge.no_candidate_event_type",
                fluent=change.fluent,
                direction=change.direction,
                entity=subject,
            )
            continue
        distribution = _score_candidates(
            candidates, change=change, probe_outputs=probe_outputs, rules=rules
        )
        evidence = tuple(
            ProbeEvidence(
                probe_id=probe_ids[name],
                wm_version=wm_version,
                raw_score=float(values[change.index]),
            )
            for name, values in probe_outputs.items()
            if name in probe_ids
        )
        lifted.append(
            LiftedEvent(
                subject=subject,
                fluent=change.fluent,
                valid_time=ValidTime(start=change.at, uncertainty_s=change.uncertainty_s),
                belief=BooleanBelief(p=_event_confidence(change)),
                state_probability=float(np.clip(change.state_after, 0.0, 1.0)),
                inferred_event_type=CategoricalBelief(pmf=distribution),
                evidence=evidence,
                alignment_residual=alignment_residual,
            )
        )

    # 5: 重複抑制
    deduped = _suppress_duplicates(lifted, window=timedelta(seconds=settings.min_duration_s))
    log.info(
        "bridge.events_lifted",
        entity=subject,
        changes=len(changes),
        lifted=len(lifted),
        after_dedup=len(deduped),
    )
    return deduped


def _median_step_seconds(timestamps: Sequence[datetime]) -> float:
    """標本間隔の中央値（秒）。"""
    if len(timestamps) < 2:
        raise BridgeError("need at least 2 timestamps to infer the sampling step")
    deltas = [(ensure_utc(b) - ensure_utc(a)).total_seconds() for a, b in pairwise(timestamps)]
    return float(np.median(np.asarray(deltas, dtype=np.float64)))


def _filter_by_duration(
    changes: Sequence[ChangePoint], binary: NDArray[np.bool_], min_samples: int
) -> list[ChangePoint]:
    """最小持続時間を満たさない変化を落とす（設計書 §5.3 の手順 2）。

    変化した後の状態が ``min_samples`` 続かないものは、ちらつきとみなして捨てる。
    """
    out: list[ChangePoint] = []
    for change in changes:
        after = binary[change.index : change.index + min_samples]
        if len(after) == 0:
            continue
        # 変化後の状態が最小持続の間ずっと保たれているか
        if bool(after.all()) or not bool(after.any()):
            out.append(change)
    return out


def _score_candidates(
    candidates: Sequence[str],
    *,
    change: FluentChange,
    probe_outputs: Mapping[str, NDArray[np.float64]],
    rules: EventCalculusRules,
) -> dict[str, float]:
    """候補事象型に尤度を割り当てる（設計書 §5.3 の手順 3）。

    判別の材料は**他のフルーエントの状態**である。たとえば Running が落ちたとき、同時に
    VibrationAnomaly が成立していれば ``Fault``、していなければ ``StopCmd`` が尤もらしい。
    これは事象計算の ``initiates`` を使って「その事象型が他にどのフルーエントを動かすか」を
    見ることで一般化できる。

    業務文脈（停止指令の記録があるか）も本来は材料だが、それは ``reasoning`` が持つ情報で
    あり、この層は記録を参照しない（監査隔離の方向性と層の責務分離）。したがってここでは
    物理側の証拠だけで重み付け、業務文脈との統合は信念更新に委ねる。
    """
    scores: dict[str, float] = {}
    for candidate in candidates:
        # この候補が同時に成立させるフルーエント（Fault なら faulted）
        co_initiated = {f for e, f in rules.initiates if e == candidate}
        support = 1.0
        for fluent in co_initiated:
            camel = fluent_local_name(fluent)
            if camel in probe_outputs:
                # 同時に動くべきフルーエントが実際に高いなら、この候補を支持する
                support *= 0.5 + float(probe_outputs[camel][change.index])
        scores[candidate] = support

    total = sum(scores.values())
    if total <= 0.0:  # pragma: no cover — support は常に正
        uniform = 1.0 / len(candidates)
        return dict.fromkeys(candidates, uniform)
    normalized = {c: v / total for c, v in scores.items()}

    # 「候補のどれでもない」余地を残す（設計書 §5.8 の "other"）。断定しないため。
    other = 0.09
    scaled = {c: v * (1.0 - other) for c, v in normalized.items()}
    scaled["other"] = other
    return scaled


def fluent_local_name(snake: str) -> str:
    """snake_case のフルーエント名をオントロジーのローカル名（CamelCase）に戻す。

    昇格イベントの型 ``PhysicalEvent/ent:<ローカル名>`` に使う。下流（信念更新・照合）は末尾が
    オントロジーの FluentType のローカル名に当たるものだけをフルーエントとして読む（ADR-0026）。
    """
    return "".join(part.capitalize() for part in snake.split("_"))


def _event_confidence(change: FluentChange) -> float:
    """事象が起きたことの確信度。

    変化点の事後確率と、変化後のプローブ確率の両方を使う。変化点としては確からしくても
    変化後の状態が曖昧なら、事象の確信度は上げられない。
    """
    after = change.confidence_after if change.direction == "rise" else 1.0 - change.confidence_after
    return float(np.clip(0.5 * change.probability + 0.5 * after, 0.0, 1.0))


def _suppress_duplicates(events: Sequence[LiftedEvent], *, window: timedelta) -> list[LiftedEvent]:
    """重複抑制（設計書 §5.3 の手順 4）。

    同一実体・同一フルーエント・区間重なりの事象を 1 つに束ねる。残すのは確信度が高い方で、
    証拠は両方を合わせる（捨てない）。

    Args:
        events: 昇格した事象。
        window: この幅以内に起きた同種の事象を重複とみなす。
    """
    out: list[LiftedEvent] = []
    for event in events:
        duplicate_index: int | None = None
        for index, kept in enumerate(out):
            if kept.subject != event.subject or kept.fluent != event.fluent:
                continue
            gap = abs((event.valid_time.start - kept.valid_time.start).total_seconds())
            if gap <= window.total_seconds():
                duplicate_index = index
                break
        if duplicate_index is None:
            out.append(event)
            continue
        kept = out[duplicate_index]
        if event.belief.p > kept.belief.p:
            # 証拠はプローブ単位で重複排除して合わせる（捨てない）。
            by_probe = {e.probe_id: e for e in kept.evidence}
            by_probe.update({e.probe_id: e for e in event.evidence})
            merged_evidence = tuple(by_probe[k] for k in sorted(by_probe))
            out[duplicate_index] = LiftedEvent(
                subject=event.subject,
                fluent=event.fluent,
                valid_time=event.valid_time,
                belief=event.belief,
                state_probability=event.state_probability,
                inferred_event_type=event.inferred_event_type,
                evidence=merged_evidence,
                alignment_residual=event.alignment_residual,
            )
    return out


def event_topic(site_id: str) -> str:
    """昇格した事象の送信先トピック。"""
    return topic_name(site_id, EventLayer.BRIDGE, "physical")


def interval_of(event: LiftedEvent) -> Interval:
    """事象の有効時間を区間として返す。"""
    return event.valid_time.interval
