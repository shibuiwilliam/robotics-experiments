"""`WorldModel` Protocol と潜在状態（設計書 §4.3、§4.4）。

**この層は記号を決めない。** 潜在状態 z を推定し、条件付き遷移 f を回し、分布として返すところ
までが責務で、「それが稼働中かどうか」はプローブ（``sbp.bridge``）が決める。境界をここで引いて
おかないと、モデル族ごとに違う記号判定が埋め込まれ、モデルを差し替えるたびに意味が変わる。

**なぜ Protocol なのか。** M4 の主張は「合成潜在を実モデルに差し替えても接地層の IF が変わら
ない」ことである。基底クラスの継承にすると実装の共有が入り、「この族だけ少し違う」が許されて
しまう。Protocol は構造的な一致だけを要求するので、2 つの族が**独立に**同じ契約を満たすことを
証明できる。

**不確実性を必ず持つ。** :class:`LatentState` は共分散かアンサンブルのどちらかを持つ
（両方 ``None`` は作れない）。点推定の潜在からは較正済みの確信度が作れず、不変条件 7
（``belief`` には較正済みの値だけ）を下流で満たせなくなる。

**``probe`` と ``explain`` は Protocol に無い（ADR-0007）。** 設計書 §4.3 はこの 2 つを
``WorldModel`` に置いているが、プローブ ID を引数に取る以上 ``bridge`` への依存が生まれ、層依存と
「WM は記号を決めない」という責務分担の両方に反する。代わりに :meth:`WorldModel.attribute` が
**線形汎関数の係数**（線形プローブの重みがそのまま入る）に対する潜在次元の寄与を返す——WM は
それが何の概念なのかを知らない。プローブの適用は ``bridge`` が :class:`LatentState` を受け取って
行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np

from sbp.core.errors import WorldModelError
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

__all__ = [
    "Attribution",
    "LatentLevel",
    "LatentSpec",
    "LatentState",
    "ModelHealth",
    "Trajectory",
    "WorldModel",
    "WorldModelMeta",
]


class LatentLevel(StrEnum):
    """階層潜在の時間尺度（設計書 §4.4）。

    階層を分けるのは「同じモデルで ms と日を扱えない」からである。各階層に別のプローブ群が
    付き、部分−全体の集約制約で整合させる。
    """

    FAST = "fast"
    """ms〜s。センサ挙動。"""
    MID = "mid"
    """分。設備状態。"""
    SLOW = "slow"
    """時間〜日。劣化・在庫水位。"""

    @property
    def typical_step_s(self) -> float:
        """この階層が想定する刻み幅（秒）。遷移の既定の時間解像度。"""
        return {"fast": 1.0, "mid": 10.0, "slow": 3600.0}[self.value]


@dataclass(frozen=True, slots=True)
class LatentSpec:
    """潜在空間の仕様（``meta.latentSpec``）。

    Attributes:
        dims: 階層ごとの次元数。
        timescales: 階層ごとの刻み幅（秒）。
    """

    dims: dict[LatentLevel, int]
    timescales: dict[LatentLevel, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """次元が正であることを検証する。"""
        if not self.dims:
            raise WorldModelError("a latent spec must declare at least one level")
        for level, dim in self.dims.items():
            if dim <= 0:
                raise WorldModelError(f"latent dimension for {level} must be positive, got {dim}")

    @property
    def levels(self) -> tuple[LatentLevel, ...]:
        """宣言されている階層。"""
        return tuple(self.dims)

    def dim_of(self, level: LatentLevel) -> int:
        """階層の次元数。

        Args:
            level: 階層。

        Raises:
            WorldModelError: 宣言されていない階層。
        """
        try:
            return self.dims[level]
        except KeyError as exc:
            raise WorldModelError(
                f"this model has no {level} latent; declared levels: "
                f"{[item.value for item in self.levels]}"
            ) from exc

    def step_of(self, level: LatentLevel) -> float:
        """階層の刻み幅（秒）。宣言が無ければ階層の既定値。

        Args:
            level: 階層。
        """
        return self.timescales.get(level, level.typical_step_s)


@dataclass(frozen=True, slots=True)
class WorldModelMeta:
    """モデルのメタデータ（設計書 §4.3 の ``meta``）。

    Attributes:
        model_id: モデルの IRI。
        version: 版（semver）。``latest`` は許さない（レジストリ規約）。
        domain: 対象ドメインの IRI。
        modalities: 入力モダリティの名前。
        latent_spec: 潜在空間の仕様。
        training_provenance: 学習の来歴 IRI（データ・設定・コミット）。
    """

    model_id: str
    version: str
    domain: str
    modalities: tuple[str, ...]
    latent_spec: LatentSpec
    training_provenance: str = ""

    def __post_init__(self) -> None:
        """版が具体的に指定されていることを検証する。"""
        if not self.version or self.version == "latest":
            raise WorldModelError(
                "a world model version must be concrete; 'latest' is forbidden because a "
                "belief recorded today must stay explainable by the model that produced it"
            )
        if not self.modalities:
            raise WorldModelError(f"model {self.model_id} declares no input modalities")

    @property
    def version_iri(self) -> str:
        """``WorldModelVersion`` の IRI（アサーションの来歴に入れる）。"""
        return f"{self.model_id}/{self.version}"


@dataclass(frozen=True, slots=True)
class LatentState:
    """潜在状態とその不確実性（設計書 §4.3、§4.4）。

    共分散（ガウス近似）かアンサンブル（3〜5 標本）のどちらかを必ず持つ。点推定だけの潜在は
    下流で較正済みの確信度を作れない。

    Attributes:
        z: 潜在ベクトル（アンサンブルを持つ場合はその平均）。
        level: 階層。
        at: この潜在が表す時刻。
        wm_version: 生成したモデルの版 IRI（来歴に入れる）。
        covariance: 潜在ガウスの共分散（``(d, d)``）。
        ensemble: アンサンブル標本（``(n, d)``）。
    """

    z: NDArray[np.float64]
    level: LatentLevel
    at: datetime
    wm_version: str
    covariance: NDArray[np.float64] | None = None
    ensemble: NDArray[np.float64] | None = None

    def __post_init__(self) -> None:
        """形と不確実性の有無を検証する。"""
        object.__setattr__(self, "at", ensure_utc(self.at))
        if self.z.ndim != 1:
            raise WorldModelError(f"latent z must be 1-D, got shape {self.z.shape}")
        if self.covariance is None and self.ensemble is None:
            raise WorldModelError(
                "a latent state must carry its uncertainty (covariance or ensemble); a point "
                "estimate cannot produce a calibrated belief downstream (invariant 7)"
            )
        if self.covariance is not None:
            expected = (self.z.size, self.z.size)
            if self.covariance.shape != expected:
                raise WorldModelError(f"covariance must be {expected}, got {self.covariance.shape}")
        if self.ensemble is not None:
            if self.ensemble.ndim != 2 or self.ensemble.shape[1] != self.z.size:
                raise WorldModelError(
                    f"ensemble must be (n, {self.z.size}), got {self.ensemble.shape}"
                )
            if self.ensemble.shape[0] < 2:
                raise WorldModelError(
                    f"an ensemble needs at least 2 members to express spread, got "
                    f"{self.ensemble.shape[0]}"
                )
        if not self.wm_version:
            raise WorldModelError(
                "a latent state must name the model version that produced it; without it the "
                "belief it supports cannot be re-explained (design §8.8)"
            )

    @property
    def dim(self) -> int:
        """潜在の次元数。"""
        return int(self.z.size)

    @property
    def spread(self) -> NDArray[np.float64]:
        """次元ごとの標準偏差。共分散があれば対角の平方根、無ければアンサンブルの標準偏差。"""
        if self.covariance is not None:
            return np.sqrt(np.clip(np.diag(self.covariance), 0.0, None))
        assert self.ensemble is not None  # __post_init__ が保証する
        return np.std(self.ensemble, axis=0, ddof=1)

    @property
    def total_uncertainty(self) -> float:
        """不確実性の総量（標準偏差のノルム）。ドリフト監視と能動学習で使う。"""
        return float(np.linalg.norm(self.spread))

    def samples(self, count: int, *, seed: int) -> NDArray[np.float64]:
        """潜在から標本を引く（ロールアウトの初期値に使う）。

        アンサンブルを持つならそれを循環して使う（再標本化で分布を作り直さない——
        アンサンブルは既に「モデルが出した分布」であり、ガウスで近似し直すと裾が消える）。

        Args:
            count: 標本数。
            seed: 乱数シード（CLAUDE.md: 乱数を使う関数は必ず seed を取る）。

        Raises:
            WorldModelError: ``count`` が正でない場合。
        """
        if count <= 0:
            raise WorldModelError(f"sample count must be positive, got {count}")
        if self.ensemble is not None:
            indices = np.arange(count) % self.ensemble.shape[0]
            return self.ensemble[indices].copy()
        assert self.covariance is not None  # __post_init__ が保証する
        generator = np.random.default_rng(seed)
        return generator.multivariate_normal(self.z, self.covariance, size=count)


@dataclass(frozen=True, slots=True)
class Trajectory:
    """ロールアウト 1 本（設計書 §4.3 の ``Trajectory``）。

    Attributes:
        states: 各地平の潜在状態（昇順）。
        horizon_origin: 予測の起点時刻。
    """

    states: tuple[LatentState, ...]
    horizon_origin: datetime

    def __post_init__(self) -> None:
        """時刻が昇順であることを検証する。"""
        object.__setattr__(self, "horizon_origin", ensure_utc(self.horizon_origin))
        if not self.states:
            raise WorldModelError("a trajectory must contain at least one state")
        for earlier, later in zip(self.states, self.states[1:], strict=False):
            if later.at <= earlier.at:
                raise WorldModelError(
                    f"trajectory states must advance in time: {earlier.at.isoformat()} then "
                    f"{later.at.isoformat()}"
                )

    @property
    def horizon(self) -> float:
        """起点から最終地平までの秒数。"""
        return (self.states[-1].at - self.horizon_origin).total_seconds()

    def lead_times(self) -> tuple[float, ...]:
        """各状態の先行時間（秒）。予測精度を起点別に追跡するために使う。"""
        return tuple((state.at - self.horizon_origin).total_seconds() for state in self.states)


@dataclass(frozen=True, slots=True)
class Attribution:
    """プローブ出力の帰属（設計書 §4.3 の ``explain``）。

    Attributes:
        probe_id: 対象プローブの IRI。
        latent_dims: 潜在次元ごとの寄与。
        modalities: 入力モダリティごとの寄与。
        time_window: 効いた観測窓（``(start, end)`` の ISO 文字列）。
    """

    probe_id: str
    latent_dims: dict[int, float]
    modalities: dict[str, float] = field(default_factory=dict)
    time_window: tuple[str, str] | None = None

    @property
    def dominant_dim(self) -> int | None:
        """最も効いた潜在次元。"""
        if not self.latent_dims:
            return None
        return max(self.latent_dims.items(), key=lambda item: abs(item[1]))[0]


@dataclass(frozen=True, slots=True)
class ModelHealth:
    """モデルの健全性（設計書 §4.8 のドリフト監視）。

    三つを**同時に**見るのが要点である。予測誤差だけを見ていると「よく当たるが記号と対応が
    取れない潜在」を見逃し、整合残差だけを見ていると「記号には合うが現実を予測しない潜在」を
    見逃す。

    Attributes:
        latent_shift: 潜在分布のシフト（MMD または PSI）。
        pred_error: 予測誤差。
        align_residual: 整合残差 ‖A(f(z,a)) − T(A(z),a)‖。
        samples: 評価に使った標本数。
        wm_version: 評価対象のモデル版。
    """

    latent_shift: float
    pred_error: float
    align_residual: float
    samples: int
    wm_version: str

    def is_healthy(self, *, shift_warn: float, error_warn: float, residual_warn: float) -> bool:
        """閾値（Settings 由来）の全てを下回っているか。

        Args:
            shift_warn: 潜在シフトの警告閾値。
            error_warn: 予測誤差の警告閾値。
            residual_warn: 整合残差の警告閾値。
        """
        return (
            self.latent_shift <= shift_warn
            and self.pred_error <= error_warn
            and self.align_residual <= residual_warn
        )

    def breaches(
        self, *, shift_warn: float, error_warn: float, residual_warn: float
    ) -> tuple[str, ...]:
        """閾値を超えた指標の名前（イベントに載せる）。

        Args:
            shift_warn: 潜在シフトの警告閾値。
            error_warn: 予測誤差の警告閾値。
            residual_warn: 整合残差の警告閾値。
        """
        breached: list[str] = []
        if self.latent_shift > shift_warn:
            breached.append("latent_shift")
        if self.pred_error > error_warn:
            breached.append("pred_error")
        if self.align_residual > residual_warn:
            breached.append("align_residual")
        return tuple(breached)


@runtime_checkable
class WorldModel(Protocol):
    """全モデル族が満たす契約（設計書 §4.3）。

    ``context`` は記号条件付けベクトル（``sbp.worldmodel.context``）。``None`` を渡せるのは
    「条件付けを使わないモデル」のためで、監査文脈でマスクを省略するための抜け道ではない
    ——条件付けベクトルの**生成**側が ``AuditMask`` を必須にしている（不変条件 6）。
    """

    @property
    def meta(self) -> WorldModelMeta:
        """モデルのメタデータ。"""
        ...

    def encode(
        self,
        observations: NDArray[np.float64],
        timestamps: Sequence[datetime],
        *,
        level: LatentLevel = LatentLevel.MID,
        context: NDArray[np.float64] | None = None,
    ) -> LatentState:
        """観測窓から潜在状態を推定する。

        Args:
            observations: 観測窓（``(時刻, チャンネル)``）。
            timestamps: 各行の時刻。
            level: 推定する階層。
            context: 記号条件付けベクトル。
        """
        ...

    def step(
        self,
        state: LatentState,
        *,
        action: NDArray[np.float64] | None = None,
        context: NDArray[np.float64] | None = None,
    ) -> LatentState:
        """潜在を 1 刻み進める。

        Args:
            state: 現在の潜在。
            action: 介入のエンコード。
            context: 記号条件付けベクトル。
        """
        ...

    def rollout(
        self,
        state: LatentState,
        *,
        horizon: int,
        samples: int = 1,
        actions: Sequence[NDArray[np.float64]] | None = None,
        context: NDArray[np.float64] | None = None,
        seed: int,
    ) -> tuple[Trajectory, ...]:
        """任意地平のロールアウトを分布として返す。

        Args:
            state: 起点の潜在。
            horizon: 刻み数。
            samples: 軌道の本数。
            actions: 各刻みの介入（``None`` なら介入なし）。
            context: 記号条件付けベクトル。
            seed: 乱数シード。
        """
        ...

    def attribute(
        self,
        state: LatentState,
        weights: NDArray[np.float64],
        *,
        label: str = "",
    ) -> Attribution:
        """線形汎関数に対する潜在次元・モダリティの寄与を返す（ADR-0007）。

        ``weights`` が何の概念を表すかは WM の関心ではない。線形プローブの重みをそのまま
        渡せるようにしてあるが、WM 側はそれを単なる係数として扱う。

        Args:
            state: 対象の潜在状態。
            weights: 線形汎関数の係数（``state.dim`` と同じ長さ）。
            label: 呼び出し側が付ける名前（プローブ IRI など。来歴用）。
        """
        ...

    def embed(self, state: LatentState, *, level: LatentLevel) -> NDArray[np.float64]:
        """潜在索引用のベクトルを返す。

        Args:
            state: 潜在状態。
            level: 索引の階層。
        """
        ...

    def health(
        self,
        observations: NDArray[np.float64],
        timestamps: Sequence[datetime],
        *,
        reference: NDArray[np.float64] | None = None,
    ) -> ModelHealth:
        """潜在シフト・予測誤差・整合残差を返す。

        Args:
            observations: 評価に使う観測窓。
            timestamps: 各行の時刻。
            reference: 基準となる潜在分布（学習時の潜在）。
        """
        ...
