"""プローブの学習・評価・昇格（M8-23、ADR-0024）。純粋関数で、I/O を持たない。

業務の主張イベント（弱ラベル）と潜在の時系列から、1 フルーエントの線形プローブを学習し、
**時間順の保留枠**で較正・評価し、昇格ゲートを通ったものだけを束ねる。入出力
（Kafka・潜在索引・置き場）は ``scripts/train_probe.py``。

**真値を読まない**（不変条件 10）。評価のラベルも弱ラベル由来（バッグ内の標本）である。
正バッグの全標本を真とみなすので、AUROC は実際より保守的に出る。ゲートは緩めない。

**ランダム分割にしない。** 時系列の近傍標本が訓練と評価に分かれると、評価が甘くなる。
各実体の時間軸を前から 訓練 / 較正 / 評価に切る。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np

from sbp.bridge.alignment import alignment_residual, symbolize
from sbp.bridge.alignment import equipment_maintenance_transition as _transition
from sbp.bridge.calibration import TemperatureScaler
from sbp.bridge.mil import TimeBag, fit_with_bags
from sbp.bridge.probes import (
    LinearProbe,
    ProbeKind,
    ProbeMetrics,
    ProbeSpec,
    auroc,
    expected_calibration_error,
    promote,
)
from sbp.bridge.serialization import ProbeBundle, bundle_probe
from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.errors import BridgeError, ProbePromotionError
from sbp.core.logging import get_logger
from sbp.core.time import Interval

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from numpy.typing import NDArray

    from sbp.core.config import BridgeSettings
    from sbp.core.events import EventEnvelope

__all__ = ["EntitySeries", "TrainingOutcome", "bags_from_claims", "train_probe"]

log = get_logger(__name__)

_EPOCHS = 400
_ROUNDS = 3
"""実体をまたいで学習するときの周回数（実体ごとに順に全バッチ更新を繰り返す）。"""


@dataclass(frozen=True, slots=True)
class EntitySeries:
    """1 実体の潜在の時系列。

    Attributes:
        entity: 実体の IRI。
        timestamps: 時刻順の標本時刻。
        latents: 潜在（``(標本数, 次元)``）。
    """

    entity: str
    timestamps: tuple[datetime, ...]
    latents: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class TrainingOutcome:
    """学習の結果。

    Attributes:
        spec: 登録メタデータ（昇格したら ``active``、できなければ ``candidate``）。
        bundle: 置き場に置く束。**昇格しなかったときは ``None``**。
        metrics: 評価指標（評価できたとき）。
        reasons: 昇格しなかった理由（昇格したら空）。
        bags: 使った弱ラベルの数。
    """

    spec: ProbeSpec
    bundle: ProbeBundle | None
    metrics: ProbeMetrics | None
    reasons: tuple[str, ...]
    bags: int


def bags_from_claims(
    events: Iterable[EventEnvelope],
    *,
    fluent: str,
    settings: BridgeSettings,
) -> tuple[list[TimeBag], int]:
    """主張イベントから時間バッグを作る。

    Args:
        events: 主張イベント（``BusinessEvent/<フルーエント>``。他は無視する）。
        fluent: 対象フルーエントの型 IRI。
        settings: 区間の半幅の下限。

    Returns:
        ``(バッグ, 使わなかった主張の数)``。確率が 0.5 ちょうど（情報が無い）や
            真偽でない信念は使わない。
    """
    wanted = f"{vocab.core.BusinessEvent}/{fluent}"
    floor = timedelta(seconds=settings.weak_label_min_half_width_s)
    bags: list[TimeBag] = []
    unused = 0
    for event in events:
        if event.type != wanted:
            continue
        belief = event.belief
        if not isinstance(belief, BooleanBelief) or belief.p == 0.5:
            unused += 1
            continue
        half = max(timedelta(seconds=event.valid_time.uncertainty_s), floor)
        bags.append(
            TimeBag(
                concept=fluent,
                entity=event.subject,
                interval=Interval(event.valid_time.start - half, event.valid_time.start + half),
                positive=belief.p > 0.5,
                weight=max(abs(2.0 * belief.p - 1.0), 1e-3),
                source_record=getattr(event.evidence[0], "record", None)
                if event.evidence
                else None,
            )
        )
    return bags, unused


def _core(bag: TimeBag, half_width: timedelta) -> TimeBag:
    """バッグの芯: 主張の時刻（区間の中点）の前後だけ。評価と較正のラベルに使う。

    訓練のバッグは情報源の時計のずれの幅まで広げる（MIL は曖昧さを前提にする）。しかし評価に
    同じ幅を使うと、状態が変わる境目の前後が逆のラベルになり、ECE が真の較正誤差ではなく
    ラベルの雑音を測ってしまう（G8 で実測: 幅 5 分・停止 24 分で ECE 0.39）。
    """
    end = bag.interval.end
    if end is None:
        return bag
    center = bag.interval.start + (end - bag.interval.start) / 2
    return replace(bag, interval=Interval(center - half_width, center + half_width))


def _labels(
    series: EntitySeries,
    bags: Sequence[TimeBag],
    indices: range,
    *,
    half_width: timedelta,
) -> tuple[NDArray[np.intp], NDArray[np.bool_]]:
    """区間内の標本に弱ラベルを付ける（芯だけ）。正負のバッグに同時に入る標本は捨てる。"""
    pos: set[int] = set()
    neg: set[int] = set()
    window = [series.timestamps[i] for i in indices]
    for bag in (_core(b, half_width) for b in bags):
        hit = {indices[j] for j in bag.sample_indices(window)}
        (pos if bag.positive else neg).update(hit)
    both = pos & neg
    keep = np.array(sorted((pos | neg) - both), dtype=np.intp)
    return keep, np.array([int(i) in pos for i in keep], dtype=np.bool_)


def _slices(count: int, settings: BridgeSettings) -> tuple[range, range, range]:
    """時間軸を前から 訓練 / 較正 / 評価に切る。"""
    train_end = int(count * settings.probe_train_fraction)
    calib_end = int(count * (settings.probe_train_fraction + settings.probe_calib_fraction))
    return range(0, train_end), range(train_end, calib_end), range(calib_end, count)


def train_probe(
    series: Sequence[EntitySeries],
    bags: Sequence[TimeBag],
    *,
    probe_id: str,
    grounds: str,
    wm_version: str,
    level: str,
    settings: BridgeSettings,
    active_alignment_residuals: tuple[float, ...] = (),
    seed: int,
) -> TrainingOutcome:
    """弱ラベルで学習し、保留枠で較正・評価し、昇格ゲートを通ったものだけ束ねる。

    Args:
        series: 実体ごとの潜在の時系列。
        bags: 弱ラベル（実体は ``TimeBag.entity`` で対応づける）。
        probe_id: 登録するプローブの IRI（版を含む）。
        grounds: 接地するフルーエントの IRI。
        wm_version: 潜在を作った WM 版の IRI。
        level: 潜在の階層。
        settings: 分割・昇格の閾値。
        active_alignment_residuals: 既存 active の整合残差（空なら中央値条件は適用されない）。
        seed: 乱数シード。

    Returns:
        結果。昇格できなければ ``bundle`` は ``None`` で、理由が ``reasons`` に入る。

    Raises:
        BridgeError: 潜在の系列が空、次元が揃わない場合。
    """
    if not series:
        raise BridgeError("no latent series to train on")
    dims = {s.latents.shape[1] for s in series}
    if len(dims) != 1:
        raise BridgeError(f"latent series have mismatched dimensions: {sorted(dims)}")
    spec = ProbeSpec(
        probe_id=probe_id,
        grounds=grounds,
        kind=ProbeKind.CLASSIFIER,
        wm_version=wm_version,
        latent_level=level,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )
    by_entity = {s.entity: [b for b in bags if b.entity == s.entity] for s in series}
    train_bags = _training_bags(series, by_entity, settings)
    used = [b for mine in train_bags.values() for b in mine]
    if not any(b.positive for b in used) or all(b.positive for b in used):
        return _declined(spec, bags, "the training window needs weak labels of both polarities")

    probe = LinearProbe(dim=dims.pop(), seed=seed)
    for _ in range(_ROUNDS):
        for s in series:
            mine = train_bags.get(s.entity)
            if mine:
                train = _slices(len(s.timestamps), settings)[0]
                try:
                    fit_with_bags(
                        probe,
                        s.latents[train.start : train.stop],
                        mine,
                        s.timestamps[train.start : train.stop],
                        epochs=_EPOCHS // _ROUNDS,
                    )
                except BridgeError as exc:  # どの標本にも当たらないバッグだけ: 学習できない
                    return _declined(spec, bags, str(exc))

    calib = _labelled(series, by_entity, settings, slot=1)
    held = _labelled(series, by_entity, settings, slot=2)
    problem = _insufficient(calib, "calibration", settings) or _insufficient(
        held, "evaluation", settings
    )
    if problem:
        return _declined(spec, bags, problem)
    _shift_intercept(probe, calib[0], calib[1])
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(calib[0]), calib[1]))
    metrics = _metrics(probe, held, series, settings)
    if metrics is None:
        return _declined(spec, bags, "no evaluation window is long enough to measure alignment")
    candidate = spec.model_copy(update={"metrics": metrics})
    try:
        active = promote(
            candidate, settings=settings, active_alignment_residuals=active_alignment_residuals
        )
    except ProbePromotionError as exc:
        log.warning("bridge.probe_not_promoted", probe_id=probe_id, reason=str(exc))
        return TrainingOutcome(candidate, None, metrics, (str(exc),), len(bags))
    return TrainingOutcome(active, bundle_probe(probe, active), metrics, (), len(bags))


def _training_bags(
    series: Sequence[EntitySeries],
    by_entity: dict[str, list[TimeBag]],
    settings: BridgeSettings,
) -> dict[str, list[TimeBag]]:
    """訓練に使うバッグ: 区間が較正枠の始まりまでに終わるもの（枠をまたぐものは使わない）。"""
    found: dict[str, list[TimeBag]] = {}
    for s in series:
        train, calib, _held = _slices(len(s.timestamps), settings)
        if len(train) == 0 or len(calib) == 0:
            continue
        cutoff = s.timestamps[calib.start]
        picked = [
            b
            for b in by_entity[s.entity]
            if b.interval.end is not None and b.interval.end <= cutoff
        ]
        if picked:
            found[s.entity] = picked
    return found


def _shift_intercept(
    probe: LinearProbe, latents: NDArray[np.float64], labels: NDArray[np.bool_]
) -> None:
    """較正枠で切片だけを合わせる（重みは動かさないので順位・AUROC は変わらない）。

    弱ラベル（MIL）の学習は、正バッグの「どれか 1 つ」しか押し上げないので、スコア全体が
    ずれた位置に出る。温度スケーリングは縮尺しか直せず、このずれは直せない（G8 で実測:
    AUROC 1.0 のまま ECE 0.39）。切片の最尤（ニュートン法）で較正枠の事前に合わせる。
    """
    logits = probe.logits(latents)
    targets = labels.astype(np.float64)
    shift = 0.0
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-(logits + shift)))
        gradient = float(np.mean(p - targets))
        curvature = float(np.mean(p * (1.0 - p))) + 1e-9
        step = gradient / curvature
        shift -= step
        if abs(step) < 1e-8:
            break
    probe.load_parameters(weights=probe.weights, bias=float(probe.bias) + shift)


def _bags_of_slot(
    series: EntitySeries, bags: Sequence[TimeBag], settings: BridgeSettings, slot: int
) -> list[TimeBag]:
    """区間の終端が属する枠のバッグだけ（較正と評価が同じ主張を共有しない）。"""
    _train, calib, held = _slices(len(series.timestamps), settings)
    if len(calib) == 0 or len(held) == 0:
        return []
    calib_start, held_start = series.timestamps[calib.start], series.timestamps[held.start]
    out: list[TimeBag] = []
    for bag in bags:
        end = bag.interval.end
        if end is None:
            continue
        if (slot == 1 and calib_start < end <= held_start) or (slot == 2 and end > held_start):
            out.append(bag)
    return out


def _labelled(
    series: Sequence[EntitySeries],
    by_entity: dict[str, list[TimeBag]],
    settings: BridgeSettings,
    *,
    slot: int,
) -> tuple[NDArray[np.float64], NDArray[np.bool_], dict[str, tuple[NDArray[np.intp], range]]]:
    """枠の弱ラベル付き標本（潜在・ラベル・実体ごとの添字）。"""
    rows: list[NDArray[np.float64]] = []
    labels: list[NDArray[np.bool_]] = []
    where: dict[str, tuple[NDArray[np.intp], range]] = {}
    for s in series:
        window = _slices(len(s.timestamps), settings)[slot]
        keep, flags = _labels(
            s,
            _bags_of_slot(s, by_entity[s.entity], settings, slot),
            window,
            half_width=timedelta(seconds=settings.weak_label_min_half_width_s),
        )
        if len(keep):
            rows.append(s.latents[keep])
            labels.append(flags)
            where[s.entity] = (keep, window)
    if not rows:
        return np.empty((0, series[0].latents.shape[1])), np.empty(0, dtype=np.bool_), where
    return np.vstack(rows), np.concatenate(labels), where


def _insufficient(
    data: tuple[NDArray[np.float64], NDArray[np.bool_], dict[str, tuple[NDArray[np.intp], range]]],
    name: str,
    settings: BridgeSettings,
) -> str | None:
    """両クラスが十分にあるか。無ければ理由を返す。"""
    labels = data[1]
    positives, negatives = int(labels.sum()), int((~labels).sum())
    need = settings.probe_min_labelled
    if positives < need or negatives < need:
        return (
            f"the {name} window has {positives} positive and {negatives} negative labelled "
            f"samples; each needs at least {need}"
        )
    return None


def _metrics(
    probe: LinearProbe,
    held: tuple[NDArray[np.float64], NDArray[np.bool_], dict[str, tuple[NDArray[np.intp], range]]],
    series: Sequence[EntitySeries],
    settings: BridgeSettings,
) -> ProbeMetrics | None:
    """評価枠の指標。整合残差は評価枠全体の記号化に対して測る（測れなければ ``None``）。"""
    latents, labels, _where = held
    output = probe.predict(latents)
    residuals: list[float] = []
    for s in series:
        window = _slices(len(s.timestamps), settings)[2]
        if len(window) >= 2:
            states = symbolize(
                {"fluent": probe.predict(s.latents[window.start : window.stop]).probabilities}
            )
            residuals.append(alignment_residual(states, _transition()).residual)
    if not residuals:
        return None
    return ProbeMetrics(
        auroc=auroc(labels, output.raw_scores),
        ece=expected_calibration_error(labels, output.probabilities),
        alignment_residual=float(np.mean(residuals)),
        n_samples=len(labels),
    )


def _declined(spec: ProbeSpec, bags: Sequence[TimeBag], reason: str) -> TrainingOutcome:
    """データ不足などで学習・評価できなかった結果（束は作らない）。"""
    log.warning("bridge.probe_training_declined", probe_id=spec.probe_id, reason=reason)
    return TrainingOutcome(spec, None, None, (reason,), len(bags))
