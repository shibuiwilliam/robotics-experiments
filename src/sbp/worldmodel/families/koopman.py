"""Koopman 作用素モデル（設計書 §4.2 の「設備・プロセス」向け族）。

**考え方。** 非線形な力学 x_{t+1} = F(x_t) を、観測量（observable）の空間に持ち上げると
**線形**になる——という Koopman の定理に沿う。持ち上げ写像 g を時間遅れ埋め込み（Hankel）で
作り、潜在空間での遷移を行列 K として最小二乗で解く。

**なぜこのドメインでこの族か**（設計書 §4.2）。回転機・プロセス設備では「固有関数がフルーエント
に対応づけやすく、整合残差が解釈しやすい」。線形遷移なので ``A(f(z,a))`` と ``T(A(z),a)`` の
食い違い（整合残差）がどの潜在方向から来ているかを追える。非線形な遷移では残差が出ても
「モデルのどこが記号と合っていないか」が分からない。

**torch を使わない理由。** Koopman 作用素の同定は勾配学習ではなく**最小二乗（SVD）**である。
numpy の `lstsq` で解けるものに最適化ループを被せると、再現性（seed 依存）と計算時間を無駄に
増やすだけで精度は上がらない。勾配学習が必要な族は ``jepa_ts``（M4-03）で torch を使う。

**注意点**（設計書 §4.2）: 非定常運転では作用素の切替（モード）が必要になる。本実装は単一
作用素であり、モード切替が必要だと分かった時点で ADR を起こす。``health()`` の予測誤差が
運転条件ごとに偏るかどうかがその兆候になる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Self

import numpy as np

from sbp.core.errors import WorldModelError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc
from sbp.worldmodel.base import (
    Attribution,
    LatentLevel,
    LatentSpec,
    LatentState,
    ModelHealth,
    Trajectory,
    WorldModelMeta,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from numpy.typing import NDArray

__all__ = ["KoopmanConfig", "KoopmanModel"]

log = get_logger(__name__)

_MIN_SAMPLES = 16
"""作用素を解くのに必要な最小標本数。これを下回ると過適合した K が出る。"""


@dataclass(frozen=True, slots=True)
class KoopmanConfig:
    """Koopman モデルの設定。

    Attributes:
        delays: 時間遅れ埋め込みの段数。潜在次元 = チャンネル数 × ``delays``。
        rank: 作用素の切り詰めランク（``None`` なら切り詰めない）。ノイズの多い系では
            下げると予測が安定する。
        ridge: 最小二乗のリッジ正則化。特異に近い系で K が爆発するのを防ぐ。
        ensemble_size: 不確実性のためのアンサンブル数（設計書 §4.4 は 3〜5）。
    """

    delays: int = 8
    rank: int | None = None
    ridge: float = 1e-6
    ensemble_size: int = 3

    def __post_init__(self) -> None:
        """設定の値域を検証する。"""
        if self.delays < 1:
            raise WorldModelError(f"delays must be at least 1, got {self.delays}")
        if self.rank is not None and self.rank < 1:
            raise WorldModelError(f"rank must be positive when given, got {self.rank}")
        if self.ridge < 0.0:
            raise WorldModelError(f"ridge must be non-negative, got {self.ridge}")
        if not 2 <= self.ensemble_size <= 5:
            raise WorldModelError(
                f"ensemble size must be 2..5 (design §4.4), got {self.ensemble_size}"
            )


@dataclass(slots=True)
class KoopmanModel:
    """線形潜在遷移モデル（``WorldModel`` Protocol の実装）。

    Attributes:
        meta: モデルのメタデータ。
        config: 設定。
        channels: 入力チャンネル名（観測列の順序と一致させる）。
    """

    meta: WorldModelMeta
    config: KoopmanConfig
    channels: tuple[str, ...]
    _mean: NDArray[np.float64] | None = field(default=None, repr=False)
    _scale: NDArray[np.float64] | None = field(default=None, repr=False)
    _operator: NDArray[np.float64] | None = field(default=None, repr=False)
    _control: NDArray[np.float64] | None = field(default=None, repr=False)
    _noise: NDArray[np.float64] | None = field(default=None, repr=False)
    _reference: NDArray[np.float64] | None = field(default=None, repr=False)
    _pred_error: float = 0.0

    @classmethod
    def untrained(
        cls,
        *,
        model_id: str,
        version: str,
        domain: str,
        channels: Sequence[str],
        config: KoopmanConfig | None = None,
    ) -> KoopmanModel:
        """学習前のモデルを作る。

        Args:
            model_id: モデルの IRI。
            version: 版（``latest`` は不可）。
            domain: 対象ドメインの IRI。
            channels: 入力チャンネル名。
            config: 設定（既定は :class:`KoopmanConfig`）。
        """
        settings = config or KoopmanConfig()
        latent_dim = len(channels) * settings.delays
        return cls(
            meta=WorldModelMeta(
                model_id=model_id,
                version=version,
                domain=domain,
                modalities=tuple(channels),
                latent_spec=LatentSpec(dims={LatentLevel.MID: latent_dim}),
            ),
            config=settings,
            channels=tuple(channels),
        )

    @property
    def is_trained(self) -> bool:
        """作用素が同定済みか。"""
        return self._operator is not None

    @property
    def latent_dim(self) -> int:
        """潜在次元（チャンネル数 × 遅れ段数）。"""
        return len(self.channels) * self.config.delays

    @property
    def parameter_count(self) -> int:
        """パラメータ数（CLAUDE.md: 数百万以下に収める）。"""
        dim = self.latent_dim
        control = 0 if self._control is None else self._control.size
        return dim * dim + control + 2 * len(self.channels)

    def fit(
        self,
        observations: NDArray[np.float64],
        *,
        actions: NDArray[np.float64] | None = None,
        seed: int,
    ) -> Self:
        """観測列から作用素を同定する（DMD with control）。

        標準化 → 時間遅れ埋め込み → 1 刻み先への最小二乗、の順。残差から潜在ノイズ共分散を
        推定し、不確実性の源にする（点推定の潜在を返さないため）。

        Args:
            observations: 観測列（``(時刻, チャンネル)``）。
            actions: 介入列（``(時刻, 介入次元)``）。``None`` なら介入なしで同定する。
            seed: 乱数シード（この実装では標本抽出に使わないが、契約として受け取る）。

        Returns:
            自分自身（学習済み）。

        Raises:
            WorldModelError: 標本が少ない、または形が合わない場合。
        """
        del seed  # 最小二乗は決定的。契約として受け取るだけ（CLAUDE.md 乱数規約）
        self._check_observation_shape(observations)
        if observations.shape[0] < _MIN_SAMPLES:
            raise WorldModelError(
                f"fitting a Koopman operator needs at least {_MIN_SAMPLES} samples, got "
                f"{observations.shape[0]}"
            )
        self._mean = observations.mean(axis=0)
        scale = observations.std(axis=0)
        # 定数チャンネル（std=0）はそのまま通す。0 で割ると潜在が NaN になる
        self._scale = np.where(scale > 1e-12, scale, 1.0)

        lifted = self._lift_series(observations)
        if lifted.shape[0] < 2:
            raise WorldModelError(
                f"delay embedding of {self.config.delays} steps leaves "
                f"{lifted.shape[0]} snapshot(s); use a longer window or fewer delays"
            )
        current, following = lifted[:-1], lifted[1:]

        if actions is not None:
            aligned = actions[self.config.delays - 1 : -1]
            if aligned.shape[0] != current.shape[0]:
                raise WorldModelError(
                    f"actions have {actions.shape[0]} rows; after delay alignment "
                    f"{aligned.shape[0]} remain but {current.shape[0]} are needed"
                )
            design = np.hstack([current, aligned])
        else:
            design = current

        solution = self._ridge_solve(design, following)
        dim = current.shape[1]
        self._operator = self._truncate(solution[:dim, :].T)
        self._control = solution[dim:, :].T if actions is not None else None

        predicted = current @ self._operator.T
        if self._control is not None and actions is not None:
            predicted = predicted + actions[self.config.delays - 1 : -1] @ self._control.T
        residual = following - predicted
        self._noise = np.atleast_2d(np.cov(residual, rowvar=False)) + self.config.ridge * np.eye(
            dim
        )
        self._reference = lifted
        denominator = float(np.linalg.norm(following)) or 1.0
        self._pred_error = float(np.linalg.norm(residual) / denominator)

        log.info(
            "worldmodel.koopman_fitted",
            wm_version=self.meta.version_iri,
            latent_dim=dim,
            snapshots=current.shape[0],
            pred_error=self._pred_error,
            parameters=self.parameter_count,
        )
        return self

    def encode(
        self,
        observations: NDArray[np.float64],
        timestamps: Sequence[datetime],
        *,
        level: LatentLevel = LatentLevel.MID,
        context: NDArray[np.float64] | None = None,
    ) -> LatentState:
        """観測窓の末尾の潜在状態を返す。

        Args:
            observations: 観測窓（``(時刻, チャンネル)``）。少なくとも ``delays`` 行必要。
            timestamps: 各行の時刻。
            level: 階層（本族は ``mid`` のみ）。
            context: 記号条件付けベクトル（本族は遷移にのみ使うため無視する）。

        Raises:
            WorldModelError: 未学習、窓が短い、または階層が未宣言の場合。
        """
        del context  # 本族は encode では条件付けを使わない（step で使う）
        self._require_trained()
        self.meta.latent_spec.dim_of(level)
        self._check_observation_shape(observations)
        if observations.shape[0] < self.config.delays:
            raise WorldModelError(
                f"encoding needs at least {self.config.delays} observations (delay embedding), "
                f"got {observations.shape[0]}"
            )
        if len(timestamps) != observations.shape[0]:
            raise WorldModelError(
                f"{observations.shape[0]} observation rows but {len(timestamps)} timestamps"
            )
        lifted = self._lift_series(observations)
        assert self._noise is not None
        return LatentState(
            z=lifted[-1],
            level=level,
            at=ensure_utc(timestamps[-1]),
            wm_version=self.meta.version_iri,
            covariance=self._noise.copy(),
        )

    def encode_series(
        self,
        observations: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        """観測列全体を潜在列に写す（プローブ学習用）。

        先頭の ``delays - 1`` 行は埋め込みが埋まらないため、最初の有効な潜在で前詰めする。
        捨てずに前詰めするのは、プローブ学習のラベル列と長さを揃えるためである（ずれた長さで
        学習すると、ラベルと潜在の時刻対応が静かに 1 刻みずれる）。

        Args:
            observations: 観測列（``(時刻, チャンネル)``）。
        """
        self._require_trained()
        self._check_observation_shape(observations)
        lifted = self._lift_series(observations)
        pad = self.config.delays - 1
        if pad == 0:
            return lifted
        return np.vstack([np.repeat(lifted[:1], pad, axis=0), lifted])

    def step(
        self,
        state: LatentState,
        *,
        action: NDArray[np.float64] | None = None,
        context: NDArray[np.float64] | None = None,
    ) -> LatentState:
        """潜在を 1 刻み進める（線形遷移 + 共分散の伝播）。

        共分散は ``K Σ Kᵀ + Q`` で伝播する。ノイズ項 Q を足すのが要点で、足さないと地平が
        伸びるほど不確実性が縮み、「遠い未来を確信している」予測になる。

        Args:
            state: 現在の潜在。
            action: 介入のエンコード。学習時に介入を与えていなければ受け付けない。
            context: 記号条件付けベクトル。潜在の末尾に加算して遷移を条件付ける。

        Raises:
            WorldModelError: 未学習、次元不一致、または学習時に無かった介入を渡した場合。
        """
        self._require_trained()
        assert self._operator is not None and self._noise is not None
        if state.dim != self.latent_dim:
            raise WorldModelError(
                f"latent has {state.dim} dimensions but this model works in {self.latent_dim}"
            )
        z = self._operator @ state.z
        if action is not None:
            if self._control is None:
                raise WorldModelError(
                    "this model was fitted without actions; it cannot condition on an "
                    "intervention (refit with actions to enable what-if)"
                )
            z = z + self._control @ action
        if context is not None:
            z = z + self._conditioning_shift(context)

        covariance = self._operator @ _covariance_of(state) @ self._operator.T + self._noise
        return LatentState(
            z=z,
            level=state.level,
            at=state.at + _step_delta(self.meta, state.level),
            wm_version=self.meta.version_iri,
            covariance=covariance,
        )

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
        """地平 ``horizon`` のロールアウトを ``samples`` 本返す。

        各本は初期潜在の標本から始める。平均軌道 1 本だけを返さないのは、下流（予測サービス、
        What-if）が分布を必要とするからである。

        Args:
            state: 起点の潜在。
            horizon: 刻み数。
            samples: 軌道の本数。
            actions: 各刻みの介入。
            context: 記号条件付けベクトル。
            seed: 乱数シード。

        Raises:
            WorldModelError: 地平・本数が正でない、または介入の長さが足りない場合。
        """
        self._require_trained()
        if horizon < 1:
            raise WorldModelError(f"horizon must be at least 1, got {horizon}")
        if samples < 1:
            raise WorldModelError(f"samples must be at least 1, got {samples}")
        if actions is not None and len(actions) < horizon:
            raise WorldModelError(
                f"rollout of {horizon} steps needs {horizon} actions, got {len(actions)}"
            )

        starts = state.samples(samples, seed=seed)
        trajectories: list[Trajectory] = []
        for index in range(samples):
            current = LatentState(
                z=starts[index],
                level=state.level,
                at=state.at,
                wm_version=self.meta.version_iri,
                covariance=_covariance_of(state),
            )
            states: list[LatentState] = []
            for horizon_index in range(horizon):
                action = None if actions is None else actions[horizon_index]
                current = self.step(current, action=action, context=context)
                states.append(current)
            trajectories.append(Trajectory(states=tuple(states), horizon_origin=state.at))
        return tuple(trajectories)

    def embed(self, state: LatentState, *, level: LatentLevel) -> NDArray[np.float64]:
        """潜在索引用のベクトル（本族は潜在そのもの）。

        Args:
            state: 潜在状態。
            level: 索引の階層。

        Raises:
            WorldModelError: 階層が宣言されていない場合。
        """
        self.meta.latent_spec.dim_of(level)
        return state.z.copy()

    def attribute(
        self,
        state: LatentState,
        weights: NDArray[np.float64],
        *,
        label: str = "",
    ) -> Attribution:
        """線形汎関数に対する潜在次元・チャンネルの寄与（ADR-0007）。

        Args:
            state: 対象の潜在。
            weights: 線形汎関数の係数。
            label: 呼び出し側が付ける名前（プローブ IRI など）。

        Raises:
            WorldModelError: 係数の長さが潜在次元と合わない場合。
        """
        if weights.shape != state.z.shape:
            raise WorldModelError(
                f"weights have shape {weights.shape} but the latent is {state.z.shape}"
            )
        contributions = weights * state.z
        per_channel: dict[str, float] = {}
        for index, value in enumerate(contributions):
            # 潜在の並びは (遅れ, チャンネル) の平坦化。チャンネルごとに畳んで返す
            channel = self.channels[index % len(self.channels)]
            per_channel[channel] = per_channel.get(channel, 0.0) + float(value)
        return Attribution(
            probe_id=label,
            latent_dims={index: float(value) for index, value in enumerate(contributions)},
            modalities=per_channel,
            time_window=(state.at.isoformat(), state.at.isoformat()),
        )

    def health(
        self,
        observations: NDArray[np.float64],
        timestamps: Sequence[datetime],
        *,
        reference: NDArray[np.float64] | None = None,
        align_residual: float = 0.0,
    ) -> ModelHealth:
        """潜在シフト・予測誤差・整合残差を返す（設計書 §4.8）。

        **整合残差は引数で受け取る。** 計算には記号側の遷移 T とプローブ出力が必要で、どちらも
        ``bridge`` にある（ADR-0007）。WM が自分で計算すると層依存に反する。渡されなければ
        0 のままで、「評価していない」ことを呼び出し側が意識する形にしている。

        Args:
            observations: 評価に使う観測列。
            timestamps: 各行の時刻。
            reference: 基準潜在（``None`` なら学習時の潜在を使う）。
            align_residual: ``bridge.alignment`` が計算した整合残差。

        Raises:
            WorldModelError: 未学習の場合。
        """
        self._require_trained()
        del timestamps  # 本族の健全性指標は時刻に依らない（分布と 1 刻み誤差のみ）
        current = self._lift_series(observations)
        baseline = reference if reference is not None else self._reference
        assert baseline is not None
        shift = _mmd(baseline, current)

        assert self._operator is not None
        predicted = current[:-1] @ self._operator.T
        actual = current[1:]
        denominator = float(np.linalg.norm(actual)) or 1.0
        error = float(np.linalg.norm(actual - predicted) / denominator)

        health = ModelHealth(
            latent_shift=shift,
            pred_error=error,
            align_residual=align_residual,
            samples=int(current.shape[0]),
            wm_version=self.meta.version_iri,
        )
        log.info(
            "worldmodel.health_evaluated",
            wm_version=self.meta.version_iri,
            latent_shift=shift,
            pred_error=error,
            align_residual=align_residual,
        )
        return health

    # ---- 内部 ----------------------------------------------------------------

    def _require_trained(self) -> None:
        """学習済みであることを要求する。"""
        if self._operator is None:
            raise WorldModelError(
                f"model {self.meta.version_iri} is not fitted; call fit() before use"
            )

    def _check_observation_shape(self, observations: NDArray[np.float64]) -> None:
        """観測の形を検証する。"""
        if observations.ndim != 2:
            raise WorldModelError(
                f"observations must be 2-D (time, channel), got shape {observations.shape}"
            )
        if observations.shape[1] != len(self.channels):
            raise WorldModelError(
                f"this model expects {len(self.channels)} channels {self.channels}, got "
                f"{observations.shape[1]}"
            )

    def _standardize(self, observations: NDArray[np.float64]) -> NDArray[np.float64]:
        """チャンネルごとに標準化する。"""
        assert self._mean is not None and self._scale is not None
        return (observations - self._mean) / self._scale

    def _lift_series(self, observations: NDArray[np.float64]) -> NDArray[np.float64]:
        """時間遅れ埋め込み（Hankel）で観測を潜在に持ち上げる。"""
        standardized = self._standardize(observations)
        delays = self.config.delays
        rows = standardized.shape[0] - delays + 1
        if rows < 1:
            raise WorldModelError(
                f"need at least {delays} observations for the delay embedding, got "
                f"{standardized.shape[0]}"
            )
        windows = [standardized[index : index + rows] for index in range(delays)]
        return np.hstack(windows)

    def _ridge_solve(
        self, design: NDArray[np.float64], target: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        """リッジ付き最小二乗。"""
        gram = design.T @ design + self.config.ridge * np.eye(design.shape[1])
        return np.linalg.solve(gram, design.T @ target)

    def _truncate(self, operator: NDArray[np.float64]) -> NDArray[np.float64]:
        """作用素を指定ランクに切り詰める（ノイズ方向を落とす）。"""
        if self.config.rank is None:
            return operator
        left, singular, right = np.linalg.svd(operator)
        rank = min(self.config.rank, singular.size)
        return left[:, :rank] @ np.diag(singular[:rank]) @ right[:rank, :]

    def _conditioning_shift(self, context: NDArray[np.float64]) -> NDArray[np.float64]:
        """記号条件付けベクトルを潜在次元に合わせる。

        条件付けは「遷移をずらす」加算項として入れる。次元が合わなければ落とす——黙って
        切り詰めると、条件付けの一部だけが効く状態になって原因が追えない。
        """
        if context.shape != (self.latent_dim,):
            raise WorldModelError(
                f"conditioning vector must have {self.latent_dim} dimensions, got {context.shape}"
            )
        return context


def _covariance_of(state: LatentState) -> NDArray[np.float64]:
    """潜在状態の共分散（アンサンブルしか無ければ標本共分散）。"""
    if state.covariance is not None:
        return state.covariance
    assert state.ensemble is not None
    return np.atleast_2d(np.cov(state.ensemble, rowvar=False))


def _step_delta(meta: WorldModelMeta, level: LatentLevel) -> timedelta:
    """階層の刻み幅。"""
    return timedelta(seconds=meta.latent_spec.step_of(level))


def _mmd(reference: NDArray[np.float64], current: NDArray[np.float64]) -> float:
    """2 標本の最大平均差異（RBF カーネル、設計書 §4.8 の潜在シフト）。

    バンド幅は 2 標本をまとめた距離の中央値（median heuristic）。固定値にすると潜在の
    スケールが変わった時点で指標の意味が変わる。
    """
    if reference.size == 0 or current.size == 0:
        return 0.0
    pooled = np.vstack([reference, current])
    # 計算量を抑えるため、標本が多いときは等間隔に間引く
    limit = 256
    if pooled.shape[0] > limit:
        stride = pooled.shape[0] // limit
        reference = reference[::stride]
        current = current[::stride]
        pooled = np.vstack([reference, current])
    distances = np.linalg.norm(pooled[:, None, :] - pooled[None, :, :], axis=-1)
    bandwidth = float(np.median(distances[distances > 0.0])) if np.any(distances > 0.0) else 1.0

    def kernel(a: NDArray[np.float64], b: NDArray[np.float64]) -> float:
        squared = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=-1) ** 2
        return float(np.mean(np.exp(-squared / (2.0 * bandwidth**2))))

    return float(
        max(
            0.0,
            kernel(reference, reference)
            + kernel(current, current)
            - 2.0 * kernel(reference, current),
        )
    )
