"""SynthWorld — 合成データ生成（設計書 §13.2、infra-testing ルール）。

**真値（オラクル）を持つ唯一の場所**である。不変条件 10 により、``SynthWorld.oracle`` は
``sbp.testing`` の外から読めない（import-linter の契約で機械検査している）。これは
「真値を見て推定する」という自己欺瞞を構造的に防ぐための境界である。

生成するもの:

1. **真の状態系列** — 事象計算規則（``ontology/process/*.lp`` と同じ構造）に従う
   フルーエントの成立・不成立。これが真値。
2. **観測** — 真の状態に依存する多変量時系列（振動・電流・温度）。ノイズを含む。
3. **合成潜在** — 真の状態を線形に埋め込んだベクトル + ノイズ。M4 で実 WM の ``encode``
   出力に差し替えるまでの代替（TASK.md の M2 → M4 の設計）。

設計書 §14 のワークドエグザンプルを既定シナリオにする（09:41 にポンプが停止し、
業務側は稼働中と記録している）。

乱数は必ず ``seed`` で決定的にする（CLAUDE.md 数値規約）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import numpy as np

from sbp.core.time import Interval, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

__all__ = [
    "LATENT_DIM",
    "Fluent",
    "GroundTruth",
    "ObservationWindow",
    "SynthConfig",
    "SynthWorld",
]

LATENT_DIM: Final = 64
"""合成潜在の次元。``infra/sql/versions/0001`` の ``latent_index`` と一致させる。"""

_FAST_LEVEL: Final = "fast"
_MID_LEVEL: Final = "mid"
_SLOW_LEVEL: Final = "slow"


class Fluent(StrEnum):
    """合成世界のフルーエント。``ontology/domain/equipment-maintenance.ttl`` のローカル名に対応。

    オントロジー側の語彙と 1 対 1 で対応させる（``make ontology-check`` が TTL と ``.lp`` の
    対応を見るのと同じ理由で、合成世界も語彙からずれてはいけない）。
    """

    RUNNING = "Running"
    UNDER_MAINTENANCE = "UnderMaintenance"
    FAULTED = "Faulted"
    VIBRATION_ANOMALY = "VibrationAnomaly"
    ACCESSIBLE = "Accessible"
    INSPECTED = "Inspected"
    PRESENT = "Present"


# 観測可能量の並び（観測ベクトルの列順）。ent:VibrationRms / MotorCurrent / BearingTemperature。
_CHANNELS: Final[tuple[str, ...]] = ("vibration_rms", "motor_current", "bearing_temperature")

# 各フルーエントが観測チャンネルに与える影響（真の状態 → 観測の生成モデル）。
# 行がフルーエント、列がチャンネル。これが「物理」であり、プローブが逆向きに推定する対象。
_EMISSION: Final[dict[Fluent, tuple[float, float, float]]] = {
    #                      vibration, current, temperature
    Fluent.RUNNING: (1.0, 12.0, 45.0),
    Fluent.UNDER_MAINTENANCE: (0.1, 0.0, 25.0),
    Fluent.FAULTED: (0.3, 0.5, 30.0),
    Fluent.VIBRATION_ANOMALY: (4.5, 0.0, 8.0),
    Fluent.ACCESSIBLE: (0.0, 0.0, 0.0),
    Fluent.INSPECTED: (0.0, 0.0, 0.0),
    Fluent.PRESENT: (0.0, 0.0, 0.0),
}

_BASELINE: Final[tuple[float, float, float]] = (0.2, 0.3, 22.0)
"""全フルーエントが偽のときの観測（停止・常温）。"""


@dataclass(frozen=True, slots=True)
class SynthConfig:
    """合成世界の設定。

    Attributes:
        start: 系列の開始時刻。
        duration: 系列の長さ。
        step: 標本間隔。
        observation_noise: 観測ノイズの標準偏差（チャンネルごとの比率）。
        latent_noise: 合成潜在のノイズの標準偏差。
        stop_at: ポンプが停止する時刻（設計書 §14 の 09:41:30 相当）。
        anomaly_at: 振動異常が始まる時刻。停止の前に始まるのが設計書 §14 の筋書き。
        maintenance_window: 保全作業の区間（``None`` なら保全なし）。
        cycles: 起動停止を繰り返す回数。1 なら「起動 → 停止」の 1 回だけ（設計書 §14 の
            筋書き）。2 以上にすると変化点が複数になり、事象昇格（M2-06）の検証と、
            時系列を分割しても両クラスが入る評価が可能になる。
    """

    start: datetime
    duration: timedelta = timedelta(hours=1)
    step: timedelta = timedelta(seconds=10)
    observation_noise: float = 0.05
    latent_noise: float = 0.1
    stop_at: timedelta | None = timedelta(minutes=41, seconds=30)
    anomaly_at: timedelta | None = timedelta(minutes=30)
    maintenance_window: tuple[timedelta, timedelta] | None = None
    cycles: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", ensure_utc(self.start))
        if self.step <= timedelta(0):
            raise ValueError(f"step must be positive, got {self.step}")
        if self.duration <= self.step:
            raise ValueError("duration must be longer than one step")
        if self.cycles < 1:
            raise ValueError(f"cycles must be at least 1, got {self.cycles}")


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """真値（オラクル）。``sbp.testing`` の外から読まない（不変条件 10）。

    Attributes:
        timestamps: 標本時刻。
        fluents: フルーエント → 各時刻での成立（真偽）。
        intervals: フルーエント → 成立区間の列。事象昇格の検証に使う。
        stop_event_at: 停止が起きた時刻（``None`` なら停止しない）。
        stop_cause: 停止の原因（``StopCmd`` / ``Fault``）。事象昇格の逆写像が当てる対象。
    """

    timestamps: tuple[datetime, ...]
    fluents: dict[Fluent, NDArray[np.bool_]]
    intervals: dict[Fluent, tuple[Interval, ...]]
    stop_event_at: datetime | None
    stop_cause: str | None

    def holds_at(self, fluent: Fluent, instant: datetime) -> bool:
        """真値として、指定時刻にフルーエントが成立しているか。

        Args:
            fluent: 対象フルーエント。
            instant: 時刻。
        """
        target = ensure_utc(instant)
        return any(iv.contains_instant(target) for iv in self.intervals.get(fluent, ()))

    def labels_for(self, fluent: Fluent) -> NDArray[np.bool_]:
        """プローブ評価用の真ラベル列。

        Args:
            fluent: 対象フルーエント。
        """
        return self.fluents[fluent]


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """観測の窓（WM の ``encode`` に渡す単位）。

    Attributes:
        start: 窓の開始時刻。
        end: 窓の終了時刻（右開）。
        timestamps: 窓内の標本時刻。
        channels: チャンネル名の並び。
        values: 観測値（``(標本数, チャンネル数)``）。
    """

    start: datetime
    end: datetime
    timestamps: tuple[datetime, ...]
    channels: tuple[str, ...]
    values: NDArray[np.float64]

    @property
    def interval(self) -> Interval:
        """窓の区間（右開）。"""
        return Interval(self.start, self.end)

    def channel(self, name: str) -> NDArray[np.float64]:
        """チャンネル 1 本を取り出す。

        Args:
            name: チャンネル名。
        """
        return self.values[:, self.channels.index(name)]


def _run_ranges(cfg: SynthConfig, count: int, stop_index: int | None) -> list[range]:
    """Running の成立区間（添字の範囲）を組む。

    ``cycles=1`` なら「開始 → ``stop_at`` で停止」の 1 回（設計書 §14 の筋書き）。
    ``cycles>1`` なら系列を等分して起動・停止を繰り返す（変化点が複数になり、事象昇格の
    検証と、時系列を分割しても両クラスが入る評価ができる）。

    Args:
        cfg: 生成設定。
        count: 標本数。
        stop_index: 停止する標本の添字（``None`` なら停止しない）。
    """
    if cfg.cycles == 1:
        end = count if stop_index is None else min(stop_index, count)
        return [range(0, end)]
    cycle_len = count // cfg.cycles
    # 各サイクルの前 60% を稼働、残りを停止にする
    return [
        range(c * cycle_len, min(c * cycle_len + int(cycle_len * 0.6), count))
        for c in range(cfg.cycles)
    ]


@dataclass(slots=True)
class SynthWorld:
    """合成世界。真の状態・観測・合成潜在を決定的に生成する。

    Args:
        config: 生成設定。
        seed: 乱数シード。同じシードなら完全に同じ系列になる。
    """

    config: SynthConfig
    seed: int = 0
    _truth: GroundTruth | None = field(default=None, init=False, repr=False)
    _observations: NDArray[np.float64] | None = field(default=None, init=False, repr=False)
    _latents: dict[str, NDArray[np.float64]] = field(default_factory=dict, init=False, repr=False)

    # ---- 真値 ----

    @property
    def oracle(self) -> GroundTruth:
        """真値（オラクル）。

        **``sbp.testing`` の外から参照しない**（不変条件 10）。import-linter の
        「真値（オラクル）は sbp.testing の外から読まない」契約で機械検査している。
        """
        if self._truth is None:
            self._truth = self._build_truth()
        return self._truth

    @property
    def timestamps(self) -> tuple[datetime, ...]:
        """標本時刻。"""
        return self.oracle.timestamps

    def _build_truth(self) -> GroundTruth:
        """事象計算に従う真の状態系列を組む。"""
        cfg = self.config
        count = int(cfg.duration / cfg.step)
        stamps = tuple(cfg.start + cfg.step * i for i in range(count))

        running = np.zeros(count, dtype=np.bool_)
        maintenance = np.zeros(count, dtype=np.bool_)
        faulted = np.zeros(count, dtype=np.bool_)
        anomaly = np.zeros(count, dtype=np.bool_)

        stop_index = None if cfg.stop_at is None else int(cfg.stop_at / cfg.step)
        anomaly_index = None if cfg.anomaly_at is None else int(cfg.anomaly_at / cfg.step)

        maint_range: range | None = None
        if cfg.maintenance_window is not None:
            m_start, m_end = cfg.maintenance_window
            maint_range = range(int(m_start / cfg.step), min(int(m_end / cfg.step), count))
            for i in maint_range:
                maintenance[i] = True

        run_ranges = _run_ranges(cfg, count, stop_index)
        for r in run_ranges:
            for i in r:
                if not maintenance[i]:
                    running[i] = True

        # 振動異常は停止より前に始まり、停止後も続く（Fault の前兆という筋書き）。
        if anomaly_index is not None:
            for i in range(anomaly_index, count):
                anomaly[i] = True

        # 停止が異常を伴うなら Fault、伴わないなら StopCmd（事象昇格の逆写像が当てる対象）。
        stop_cause: str | None = None
        stop_at: datetime | None = None
        first_stop = run_ranges[0].stop if run_ranges and run_ranges[0].stop < count else None
        if first_stop is not None:
            stop_at = stamps[first_stop]
            anomalous = bool(anomaly[first_stop])
            stop_cause = "Fault" if anomalous else "StopCmd"
            if anomalous:
                # Faulted は次の起動まで続く（再起動で解消する）
                next_run = next((r.start for r in run_ranges[1:] if r.start > first_stop), count)
                for i in range(first_stop, next_run):
                    faulted[i] = True

        fluents: dict[Fluent, NDArray[np.bool_]] = {
            Fluent.RUNNING: running,
            Fluent.UNDER_MAINTENANCE: maintenance,
            Fluent.FAULTED: faulted,
            Fluent.VIBRATION_ANOMALY: anomaly,
            # Accessible は保全中または停止中に成立する（人が近づける状態）
            Fluent.ACCESSIBLE: ~running,
            Fluent.INSPECTED: np.zeros(count, dtype=np.bool_),
            Fluent.PRESENT: np.zeros(count, dtype=np.bool_),
        }
        intervals = {f: self._to_intervals(series, stamps) for f, series in fluents.items()}
        return GroundTruth(
            timestamps=stamps,
            fluents=fluents,
            intervals=intervals,
            stop_event_at=stop_at,
            stop_cause=stop_cause,
        )

    def _to_intervals(
        self, series: NDArray[np.bool_], stamps: tuple[datetime, ...]
    ) -> tuple[Interval, ...]:
        """真偽列を成立区間の列に変換する（右開）。"""
        out: list[Interval] = []
        start_index: int | None = None
        for i, value in enumerate(series):
            if value and start_index is None:
                start_index = i
            elif not value and start_index is not None:
                out.append(Interval(stamps[start_index], stamps[i]))
                start_index = None
        if start_index is not None:
            # 系列末尾まで成立。最後の標本の次の刻みを終端にする（右開を保つ）。
            out.append(Interval(stamps[start_index], stamps[-1] + self.config.step))
        return tuple(out)

    # ---- 観測 ----

    @property
    def observations(self) -> NDArray[np.float64]:
        """観測値（``(標本数, チャンネル数)``）。真の状態 + ノイズで生成する。"""
        if self._observations is None:
            self._observations = self._build_observations()
        return self._observations

    @property
    def channels(self) -> tuple[str, ...]:
        """観測チャンネルの並び。"""
        return _CHANNELS

    def _build_observations(self) -> NDArray[np.float64]:
        truth = self.oracle
        count = len(truth.timestamps)
        rng = np.random.default_rng(self.seed)
        values = np.tile(np.asarray(_BASELINE, dtype=np.float64), (count, 1))
        for fluent, emission in _EMISSION.items():
            active = truth.fluents[fluent]
            values += np.outer(active.astype(np.float64), np.asarray(emission, dtype=np.float64))
        scale = np.abs(values) * self.config.observation_noise + 1e-3
        return values + rng.normal(0.0, scale)

    def window(self, start: datetime, end: datetime) -> ObservationWindow:
        """観測窓を切り出す（右開区間）。

        Args:
            start: 窓の開始時刻。
            end: 窓の終了時刻（含まない）。

        Raises:
            ValueError: 窓に標本が 1 つも入らない場合。
        """
        interval = Interval(start, end)
        stamps = self.oracle.timestamps
        indices = [i for i, t in enumerate(stamps) if interval.contains_instant(t)]
        if not indices:
            raise ValueError(f"no samples inside {interval}")
        return ObservationWindow(
            start=interval.start,
            end=interval.start if interval.end is None else interval.end,
            timestamps=tuple(stamps[i] for i in indices),
            channels=_CHANNELS,
            values=self.observations[indices, :],
        )

    # ---- 合成潜在 ----

    def latents(self, level: str = _MID_LEVEL) -> NDArray[np.float64]:
        """合成潜在（``(標本数, LATENT_DIM)``）。

        真の状態を線形に埋め込み、ノイズを足す。**線形プローブで分離できる**ように作るのは
        意図的で、設計書 §5.2 の「まず線形プローブ（潜在の品質検査を兼ねる）」が成立する
        基準系を与えるためである。実 WM（M4）に差し替えたとき線形で足りなければ、それは
        潜在の質の問題として現れる。

        階層（設計書 §4.4）:

        - ``fast`` — 観測そのものに近い速い成分（ms〜s）。
        - ``mid`` — 設備状態（分）。フルーエントが最も素直に出る。
        - ``slow`` — 劣化（時間〜日）。累積量として表れる。

        Args:
            level: 潜在の階層。

        Raises:
            ValueError: 未知の階層名。
        """
        if level not in (_FAST_LEVEL, _MID_LEVEL, _SLOW_LEVEL):
            raise ValueError(f"unknown latent level {level!r}; expected one of fast / mid / slow")
        if level not in self._latents:
            self._latents[level] = self._build_latents(level)
        return self._latents[level]

    def _build_latents(self, level: str) -> NDArray[np.float64]:
        truth = self.oracle
        count = len(truth.timestamps)
        # 階層ごとに別のシードを使う（同じ系列にならないようにする）。
        rng = np.random.default_rng(self.seed + hash(level) % 1000)

        # フルーエントを直交に近い方向へ埋め込む（線形分離可能にする）。
        basis_rng = np.random.default_rng(self.seed)
        basis = basis_rng.normal(0.0, 1.0, size=(len(Fluent), LATENT_DIM))
        basis /= np.linalg.norm(basis, axis=1, keepdims=True)

        latent = np.zeros((count, LATENT_DIM), dtype=np.float64)
        for index, fluent in enumerate(Fluent):
            active = truth.fluents[fluent].astype(np.float64)
            if level == _SLOW_LEVEL:
                # 遅い階層では累積（劣化の蓄積）として表す。
                active = np.cumsum(active) / max(count, 1)
            latent += np.outer(active, basis[index])

        if level == _FAST_LEVEL:
            # 速い階層には観測の変動をそのまま載せる。
            obs = self.observations
            normalized = (obs - obs.mean(axis=0)) / (obs.std(axis=0) + 1e-9)
            latent[:, : len(_CHANNELS)] += normalized

        noisy = latent + rng.normal(0.0, self.config.latent_noise, size=latent.shape)
        return np.asarray(noisy, dtype=np.float64)

    # ---- 弱ラベルの材料 ----

    def true_intervals(self, fluent: Fluent) -> tuple[Interval, ...]:
        """真値としての成立区間（検証用。``sbp.testing`` 内からのみ使う）。

        Args:
            fluent: 対象フルーエント。
        """
        return self.oracle.intervals.get(fluent, ())

    def sample_indices_in(self, interval: Interval) -> Sequence[int]:
        """区間に入る標本の添字（時間バッグの構成に使う）。

        Args:
            interval: 対象区間。
        """
        return [i for i, t in enumerate(self.oracle.timestamps) if interval.contains_instant(t)]
