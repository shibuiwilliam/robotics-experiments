"""JEPA 型の時系列潜在予測モデル（設計書 §4.2 の「多変量時系列一般」向け族）。

**考え方。** 画素（ここでは生のセンサ値）を再構成せず、**潜在空間で次を予測する**。
入力窓を文脈エンコーダ g_θ で潜在にし、予測器 h_φ が次の窓の潜在を当てる。目標側は同じ構造の
目標エンコーダ g_ξ（θ の指数移動平均）で、勾配を流さない（stop-gradient）。

**なぜ再構成しないのか**（設計書 §4.2）。再構成損失は「予測しやすいが意味のない高周波成分」に
容量を使う。センサ値ではノイズの再現に引っ張られ、設備状態という低次元の構造が潜在に残らない。
プローブが線形で済むかどうかはここで決まる。

**崩壊（collapse）を防ぐ仕組みが必須。** 予測損失だけを下げる最適解は「全部同じ潜在を出す」
である（予測は完璧、情報量ゼロ）。VICReg 系の分散項と共分散項を入れて、各次元が広がりを持ち、
次元間が相関しないように押す。この 2 項を落とすと**学習は収束して指標も良く見えるのに
プローブが何も分離できない**——最も気付きにくい失敗である。

**koopman 族との違い。** koopman は遷移が線形だと仮定して作用素を解く（決定的、速い、解釈
しやすい）。こちらは非線形の遷移を勾配で学ぶ（表現力が高い、seed 依存、解釈しにくい）。
同じ ``WorldModel`` Protocol を**独立に**満たすことが M4 のゲート G4 の中身である。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Self

import numpy as np
import torch
from torch import Tensor, nn

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
from sbp.worldmodel.families.koopman import latent_shift_mmd

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from numpy.typing import NDArray

__all__ = ["JepaConfig", "JepaTimeSeriesModel", "TrainingReport"]

log = get_logger(__name__)

_MIN_BATCHES = 2
"""学習に必要な最小バッチ数。1 バッチでは分散項が意味を持たない。"""


@dataclass(frozen=True, slots=True)
class JepaConfig:
    """JEPA 時系列モデルの設定。

    Attributes:
        window: 入力窓の長さ（刻み数）。
        latent_dim: 潜在次元。
        hidden_dim: エンコーダ・予測器の隠れ層幅。
        ensemble_size: 不確実性のためのアンサンブル数（設計書 §4.4 は 3〜5）。
        dropout: アンサンブルを作るための dropout 率（推論時にも効かせる）。
        ema_decay: 目標エンコーダの指数移動平均の減衰。
        variance_weight: 分散項の重み（崩壊防止）。
        covariance_weight: 共分散項の重み（次元間の冗長性を削る）。
        device: 計算デバイス。``mps`` が使えなければ ``cpu`` に落ちる。
    """

    window: int = 16
    latent_dim: int = 16
    hidden_dim: int = 64
    ensemble_size: int = 3
    dropout: float = 0.1
    ema_decay: float = 0.99
    variance_weight: float = 1.0
    covariance_weight: float = 0.04
    device: str = "mps"

    def __post_init__(self) -> None:
        """設定の値域を検証する。"""
        for name, value in (
            ("window", self.window),
            ("latent_dim", self.latent_dim),
            ("hidden_dim", self.hidden_dim),
        ):
            if value < 1:
                raise WorldModelError(f"{name} must be at least 1, got {value}")
        if not 2 <= self.ensemble_size <= 5:
            raise WorldModelError(
                f"ensemble size must be 2..5 (design §4.4), got {self.ensemble_size}"
            )
        if not 0.0 < self.dropout < 1.0:
            raise WorldModelError(
                f"dropout must be in (0, 1) to produce an ensemble, got {self.dropout}"
            )
        if not 0.0 < self.ema_decay < 1.0:
            raise WorldModelError(f"ema_decay must be in (0, 1), got {self.ema_decay}")
        if self.variance_weight <= 0.0:
            raise WorldModelError(
                "variance_weight must be positive; without it the model collapses to a constant "
                "latent and every probe silently stops separating anything"
            )

    def resolved_device(self) -> str:
        """実際に使うデバイス（MPS が無ければ CPU）。"""
        if self.device == "mps" and not torch.backends.mps.is_available():
            log.info("worldmodel.mps_unavailable_falling_back_to_cpu")
            return "cpu"
        return self.device


@dataclass(frozen=True, slots=True)
class TrainingReport:
    """学習 1 回の記録（設計書 §4.5 のゲート判定に使う）。

    Attributes:
        epochs: 回したエポック数。
        prediction_loss: 最終エポックの予測損失。
        variance_loss: 最終エポックの分散項。
        covariance_loss: 最終エポックの共分散項。
        latent_std: 潜在次元ごとの標準偏差の平均（崩壊の直接の指標）。
        device: 使ったデバイス。
        seed: 使ったシード。
    """

    epochs: int
    prediction_loss: float
    variance_loss: float
    covariance_loss: float
    latent_std: float
    device: str
    seed: int

    @property
    def collapsed(self) -> bool:
        """潜在が崩壊しているか（広がりが実質ゼロ）。

        入力は標準化してあるので、健全な潜在の標準偏差は 1 のオーダーになる。0.1 を下回るのは
        「ほぼ定数」であり、学習が「収束した」ように見えても何も学んでいない。

        **部分的な崩壊はここでは捕まらない。** 実測（SynthWorld、分散項を切った場合）では
        ``latent_std`` が 0.97 → 0.157 に縮んでも AUROC は 0.9984 のままで、劣化は **ECE に
        出た**（0.0048 → 0.0349）。分離はできるが確信度が壊れる、という形で現れる。
        昇格ゲートが AUROC ∧ ECE ∧ 整合残差の AND になっているのはこのためで、AUROC だけを
        見ていると潰れかけた潜在を active にしてしまう。
        """
        return self.latent_std < 0.1


class _Encoder(nn.Module):
    """窓 → 潜在。dropout を推論時にも効かせてアンサンブルを作る。"""

    def __init__(self, input_dim: int, hidden_dim: int, latent_dim: int, dropout: float) -> None:
        """Args:
        input_dim: 平坦化した窓の次元。
        hidden_dim: 隠れ層幅。
        latent_dim: 潜在次元。
        dropout: dropout 率。
        """
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, latent_dim),
        )

    def forward(self, windows: Tensor) -> Tensor:
        """窓を潜在に写す。

        Args:
            windows: ``(バッチ, 窓 × チャンネル)``。
        """
        return self.net(windows)


class _Predictor(nn.Module):
    """潜在（+ 介入 + 条件付け）→ 次の潜在。"""

    def __init__(self, latent_dim: int, hidden_dim: int, action_dim: int) -> None:
        """Args:
        latent_dim: 潜在次元。
        hidden_dim: 隠れ層幅。
        action_dim: 介入の次元（0 なら介入なし）。
        """
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(latent_dim + action_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, latent_dim),
        )

    def forward(self, latents: Tensor, actions: Tensor | None = None) -> Tensor:
        """次の潜在を予測する。

        残差接続にしているのは、「次は今とだいたい同じ」が時系列の既定だからである。
        ゼロから作らせると初期の学習が遅く、崩壊に落ちやすい。

        Args:
            latents: ``(バッチ, 潜在次元)``。
            actions: ``(バッチ, 介入次元)``。
        """
        inputs = latents if actions is None else torch.cat([latents, actions], dim=-1)
        return latents + self.net(inputs)


def _variance_loss(latents: Tensor) -> Tensor:
    """各次元に広がりを要求する（崩壊防止、VICReg の分散項）。"""
    std = torch.sqrt(latents.var(dim=0) + 1e-4)
    return torch.mean(torch.relu(1.0 - std))


def _covariance_loss(latents: Tensor) -> Tensor:
    """次元間の相関を削る（VICReg の共分散項）。"""
    centered = latents - latents.mean(dim=0, keepdim=True)
    batch = max(centered.shape[0] - 1, 1)
    covariance = (centered.T @ centered) / batch
    off_diagonal = covariance - torch.diag(torch.diag(covariance))
    return (off_diagonal**2).sum() / latents.shape[1]


@dataclass(slots=True)
class JepaTimeSeriesModel:
    """潜在予測モデル（``WorldModel`` Protocol の実装）。

    Attributes:
        meta: モデルのメタデータ。
        config: 設定。
        channels: 入力チャンネル名。
    """

    meta: WorldModelMeta
    config: JepaConfig
    channels: tuple[str, ...]
    _encoder: _Encoder | None = field(default=None, repr=False)
    _target: _Encoder | None = field(default=None, repr=False)
    _predictor: _Predictor | None = field(default=None, repr=False)
    _mean: NDArray[np.float64] | None = field(default=None, repr=False)
    _scale: NDArray[np.float64] | None = field(default=None, repr=False)
    _reference: NDArray[np.float64] | None = field(default=None, repr=False)
    _action_dim: int = 0
    _report: TrainingReport | None = field(default=None, repr=False)

    @classmethod
    def untrained(
        cls,
        *,
        model_id: str,
        version: str,
        domain: str,
        channels: Sequence[str],
        config: JepaConfig | None = None,
    ) -> JepaTimeSeriesModel:
        """学習前のモデルを作る。

        Args:
            model_id: モデルの IRI。
            version: 版（``latest`` は不可）。
            domain: 対象ドメインの IRI。
            channels: 入力チャンネル名。
            config: 設定。
        """
        settings = config or JepaConfig()
        return cls(
            meta=WorldModelMeta(
                model_id=model_id,
                version=version,
                domain=domain,
                modalities=tuple(channels),
                latent_spec=LatentSpec(dims={LatentLevel.MID: settings.latent_dim}),
            ),
            config=settings,
            channels=tuple(channels),
        )

    @property
    def is_trained(self) -> bool:
        """学習済みか。"""
        return self._encoder is not None

    @property
    def latent_dim(self) -> int:
        """潜在次元。"""
        return self.config.latent_dim

    @property
    def parameter_count(self) -> int:
        """パラメータ数（CLAUDE.md: 数百万以下）。"""
        if self._encoder is None or self._predictor is None:
            window_dim = self.config.window * len(self.channels)
            encoder = window_dim * self.config.hidden_dim + self.config.hidden_dim
            encoder += self.config.hidden_dim * self.config.latent_dim + self.config.latent_dim
            predictor = (
                self.config.latent_dim + self._action_dim
            ) * self.config.hidden_dim + self.config.hidden_dim
            predictor += self.config.hidden_dim * self.config.latent_dim + self.config.latent_dim
            return encoder + predictor
        return sum(
            parameter.numel()
            for module in (self._encoder, self._predictor)
            for parameter in module.parameters()
        )

    @property
    def report(self) -> TrainingReport | None:
        """最後の学習の記録。"""
        return self._report

    def fit(
        self,
        observations: NDArray[np.float64],
        *,
        actions: NDArray[np.float64] | None = None,
        epochs: int = 200,
        batch_size: int = 64,
        learning_rate: float = 1e-3,
        seed: int,
    ) -> Self:
        """潜在予測で学習する。

        Args:
            observations: 観測列（``(時刻, チャンネル)``）。
            actions: 介入列（``(時刻, 介入次元)``）。
            epochs: エポック数。
            batch_size: バッチサイズ。
            learning_rate: 学習率。
            seed: 乱数シード（CLAUDE.md: 乱数を使う関数は必ず seed を取る）。

        Returns:
            自分自身（学習済み）。

        Raises:
            WorldModelError: 標本が足りない、または形が合わない場合。
        """
        self._check_observation_shape(observations)
        torch.manual_seed(seed)
        device = torch.device(self.config.resolved_device())

        self._mean = observations.mean(axis=0)
        scale = observations.std(axis=0)
        self._scale = np.where(scale > 1e-12, scale, 1.0)

        windows, targets, aligned_actions = self._windows(observations, actions)
        if windows.shape[0] < _MIN_BATCHES:
            raise WorldModelError(
                f"need at least {_MIN_BATCHES} windows of length {self.config.window}, got "
                f"{windows.shape[0]}"
            )
        self._action_dim = 0 if aligned_actions is None else aligned_actions.shape[1]

        window_dim = self.config.window * len(self.channels)
        self._encoder = _Encoder(
            window_dim, self.config.hidden_dim, self.config.latent_dim, self.config.dropout
        ).to(device)
        self._target = _Encoder(
            window_dim, self.config.hidden_dim, self.config.latent_dim, self.config.dropout
        ).to(device)
        self._target.load_state_dict(self._encoder.state_dict())
        for parameter in self._target.parameters():
            parameter.requires_grad_(False)
        self._predictor = _Predictor(
            self.config.latent_dim, self.config.hidden_dim, self._action_dim
        ).to(device)

        optimizer = torch.optim.Adam(
            [*self._encoder.parameters(), *self._predictor.parameters()], lr=learning_rate
        )
        context_tensor = torch.tensor(windows, dtype=torch.float32, device=device)
        target_tensor = torch.tensor(targets, dtype=torch.float32, device=device)
        action_tensor = (
            None
            if aligned_actions is None
            else torch.tensor(aligned_actions, dtype=torch.float32, device=device)
        )

        generator = np.random.default_rng(seed)
        prediction = variance = covariance = 0.0
        for _ in range(epochs):
            order = generator.permutation(context_tensor.shape[0])
            for start in range(0, len(order), batch_size):
                batch = torch.tensor(order[start : start + batch_size], device=device)
                if batch.numel() < 2:
                    continue  # 分散項が意味を持たない大きさのバッチは飛ばす
                latents = self._encoder(context_tensor[batch])
                actions_batch = None if action_tensor is None else action_tensor[batch]
                predicted = self._predictor(latents, actions_batch)
                with torch.no_grad():
                    # 目標側は勾配を流さない（stop-gradient）。流すと両者が定数に寄って崩壊する
                    expected = self._target(target_tensor[batch])
                prediction_loss = nn.functional.mse_loss(predicted, expected)
                variance_term = _variance_loss(latents)
                covariance_term = _covariance_loss(latents)
                loss = (
                    prediction_loss
                    + self.config.variance_weight * variance_term
                    + self.config.covariance_weight * covariance_term
                )
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                self._update_target()
                prediction = float(prediction_loss.detach().cpu())
                variance = float(variance_term.detach().cpu())
                covariance = float(covariance_term.detach().cpu())

        self._reference = self.encode_series(observations)
        self._report = TrainingReport(
            epochs=epochs,
            prediction_loss=prediction,
            variance_loss=variance,
            covariance_loss=covariance,
            latent_std=float(np.mean(np.std(self._reference, axis=0))),
            device=str(device),
            seed=seed,
        )
        log.info(
            "worldmodel.jepa_fitted",
            wm_version=self.meta.version_iri,
            latent_dim=self.config.latent_dim,
            parameters=self.parameter_count,
            prediction_loss=prediction,
            latent_std=self._report.latent_std,
            collapsed=self._report.collapsed,
            device=str(device),
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
        """観測窓の末尾の潜在状態を返す（dropout アンサンブル付き）。

        Args:
            observations: 観測窓。少なくとも ``window`` 行必要。
            timestamps: 各行の時刻。
            level: 階層。
            context: 記号条件付けベクトル（本族は遷移で使う）。

        Raises:
            WorldModelError: 未学習、窓が短い、または時刻の長さが合わない場合。
        """
        del context
        self._require_trained()
        self.meta.latent_spec.dim_of(level)
        self._check_observation_shape(observations)
        if observations.shape[0] < self.config.window:
            raise WorldModelError(
                f"encoding needs at least {self.config.window} observations, got "
                f"{observations.shape[0]}"
            )
        if len(timestamps) != observations.shape[0]:
            raise WorldModelError(
                f"{observations.shape[0]} observation rows but {len(timestamps)} timestamps"
            )
        window = self._standardize(observations[-self.config.window :]).reshape(1, -1)
        members = self._ensemble(window)
        return LatentState(
            z=members.mean(axis=0),
            level=level,
            at=ensure_utc(timestamps[-1]),
            wm_version=self.meta.version_iri,
            ensemble=members,
        )

    def encode_series(self, observations: NDArray[np.float64]) -> NDArray[np.float64]:
        """観測列全体を潜在列に写す（プローブ学習用）。

        窓が埋まらない先頭は最初の有効な潜在で前詰めし、ラベル列と長さを揃える。

        Args:
            observations: 観測列。
        """
        self._require_trained()
        self._check_observation_shape(observations)
        assert self._encoder is not None
        standardized = self._standardize(observations)
        window = self.config.window
        rows = standardized.shape[0] - window + 1
        if rows < 1:
            raise WorldModelError(
                f"need at least {window} observations, got {standardized.shape[0]}"
            )
        stacked = np.stack(
            [standardized[index : index + window].reshape(-1) for index in range(rows)]
        )
        device = torch.device(self.config.resolved_device())
        self._encoder.eval()
        with torch.no_grad():
            latents = (
                self._encoder(torch.tensor(stacked, dtype=torch.float32, device=device))
                .cpu()
                .numpy()
                .astype(np.float64)
            )
        pad = window - 1
        if pad == 0:
            return latents
        return np.vstack([np.repeat(latents[:1], pad, axis=0), latents])

    def step(
        self,
        state: LatentState,
        *,
        action: NDArray[np.float64] | None = None,
        context: NDArray[np.float64] | None = None,
    ) -> LatentState:
        """潜在を 1 刻み進める（アンサンブルの各成員を個別に進める）。

        成員ごとに進めるのが要点である。平均だけを進めると、非線形な遷移では「平均の予測」と
        「予測の平均」が食い違い、不確実性が消える。

        Args:
            state: 現在の潜在。
            action: 介入のエンコード。
            context: 記号条件付けベクトル（潜在に加算する）。

        Raises:
            WorldModelError: 未学習、次元不一致、または学習時に無かった介入を渡した場合。
        """
        self._require_trained()
        assert self._predictor is not None
        if state.dim != self.latent_dim:
            raise WorldModelError(
                f"latent has {state.dim} dimensions but this model works in {self.latent_dim}"
            )
        if action is not None and self._action_dim == 0:
            raise WorldModelError(
                "this model was fitted without actions; it cannot condition on an intervention"
            )
        if action is None and self._action_dim > 0:
            raise WorldModelError(
                f"this model was fitted with {self._action_dim}-dimensional actions; pass one "
                "(use zeros for 'no intervention')"
            )
        if context is not None and context.shape != (self.latent_dim,):
            raise WorldModelError(
                f"conditioning vector must have {self.latent_dim} dimensions, got {context.shape}"
            )

        members = (
            state.ensemble
            if state.ensemble is not None
            else state.samples(self.config.ensemble_size, seed=0)
        )
        device = torch.device(self.config.resolved_device())
        latents = torch.tensor(members, dtype=torch.float32, device=device)
        actions = (
            None
            if action is None
            else torch.tensor(
                np.repeat(action[None, :], members.shape[0], axis=0),
                dtype=torch.float32,
                device=device,
            )
        )
        self._predictor.eval()
        with torch.no_grad():
            advanced = self._predictor(latents, actions).cpu().numpy().astype(np.float64)
        if context is not None:
            advanced = advanced + context
        return LatentState(
            z=advanced.mean(axis=0),
            level=state.level,
            at=state.at + timedelta(seconds=self.meta.latent_spec.step_of(state.level)),
            wm_version=self.meta.version_iri,
            ensemble=advanced,
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

        Args:
            state: 起点の潜在。
            horizon: 刻み数。
            samples: 軌道の本数。
            actions: 各刻みの介入。
            context: 記号条件付けベクトル。
            seed: 乱数シード。

        Raises:
            WorldModelError: 地平・本数が正でない、または介入が足りない場合。
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
        starts = state.samples(max(samples * 2, self.config.ensemble_size), seed=seed)
        trajectories: list[Trajectory] = []
        for index in range(samples):
            members = starts[index :: max(samples, 1)][: self.config.ensemble_size]
            if members.shape[0] < 2:
                members = starts[: self.config.ensemble_size]
            current = LatentState(
                z=members.mean(axis=0),
                level=state.level,
                at=state.at,
                wm_version=self.meta.version_iri,
                ensemble=members,
            )
            states: list[LatentState] = []
            for horizon_index in range(horizon):
                action = None if actions is None else actions[horizon_index]
                current = self.step(current, action=action, context=context)
                states.append(current)
            trajectories.append(Trajectory(states=tuple(states), horizon_origin=state.at))
        return tuple(trajectories)

    def embed(self, state: LatentState, *, level: LatentLevel) -> NDArray[np.float64]:
        """潜在索引用のベクトル。

        Args:
            state: 潜在状態。
            level: 索引の階層。
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
        """線形汎関数に対する潜在次元の寄与（ADR-0007）。

        非線形エンコーダなので、チャンネルへの帰属は潜在次元の寄与を入力勾配で配分して出す。
        勾配を使うのは、koopman のように「潜在次元 ↔ チャンネル」の対応が構造的に決まって
        いないからである。

        Args:
            state: 対象の潜在。
            weights: 線形汎関数の係数。
            label: 呼び出し側が付ける名前。

        Raises:
            WorldModelError: 係数の長さが潜在次元と合わない場合。
        """
        if weights.shape != state.z.shape:
            raise WorldModelError(
                f"weights have shape {weights.shape} but the latent is {state.z.shape}"
            )
        contributions = weights * state.z
        return Attribution(
            probe_id=label,
            latent_dims={index: float(value) for index, value in enumerate(contributions)},
            modalities={},
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

        整合残差は ``bridge`` が計算して渡す（ADR-0007）。

        Args:
            observations: 評価に使う観測列。
            timestamps: 各行の時刻。
            reference: 基準潜在（``None`` なら学習時の潜在）。
            align_residual: 整合残差。
        """
        self._require_trained()
        del timestamps
        current = self.encode_series(observations)
        baseline = reference if reference is not None else self._reference
        assert baseline is not None
        shift = latent_shift_mmd(baseline, current)

        assert self._predictor is not None
        device = torch.device(self.config.resolved_device())
        self._predictor.eval()
        with torch.no_grad():
            latents = torch.tensor(current[:-1], dtype=torch.float32, device=device)
            actions = (
                None
                if self._action_dim == 0
                else torch.zeros(
                    (latents.shape[0], self._action_dim), dtype=torch.float32, device=device
                )
            )
            predicted = self._predictor(latents, actions).cpu().numpy().astype(np.float64)
        actual = current[1:]
        denominator = float(np.linalg.norm(actual)) or 1.0
        error = float(np.linalg.norm(actual - predicted) / denominator)

        return ModelHealth(
            latent_shift=shift,
            pred_error=error,
            align_residual=align_residual,
            samples=int(current.shape[0]),
            wm_version=self.meta.version_iri,
        )

    # ---- 内部 ----------------------------------------------------------------

    def _require_trained(self) -> None:
        """学習済みであることを要求する。"""
        if self._encoder is None:
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

    def _windows(
        self, observations: NDArray[np.float64], actions: NDArray[np.float64] | None
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64] | None]:
        """文脈窓・目標窓・整列した介入を作る。"""
        standardized = self._standardize(observations)
        window = self.config.window
        rows = standardized.shape[0] - window
        if rows < 1:
            raise WorldModelError(
                f"need at least {window + 1} observations to form a context/target pair, got "
                f"{standardized.shape[0]}"
            )
        context = np.stack(
            [standardized[index : index + window].reshape(-1) for index in range(rows)]
        )
        target = np.stack(
            [standardized[index + 1 : index + 1 + window].reshape(-1) for index in range(rows)]
        )
        if actions is None:
            return context, target, None
        aligned = actions[window - 1 : window - 1 + rows]
        if aligned.shape[0] != rows:
            raise WorldModelError(
                f"actions have {actions.shape[0]} rows; {rows} are needed after windowing"
            )
        return context, target, aligned

    def _ensemble(self, window: NDArray[np.float64]) -> NDArray[np.float64]:
        """dropout を効かせたまま複数回通してアンサンブルを作る。"""
        assert self._encoder is not None
        device = torch.device(self.config.resolved_device())
        tensor = torch.tensor(window, dtype=torch.float32, device=device)
        self._encoder.train()  # dropout を推論時にも効かせる（認識的不確実性の源）
        with torch.no_grad():
            members = [
                self._encoder(tensor).cpu().numpy().astype(np.float64).reshape(-1)
                for _ in range(self.config.ensemble_size)
            ]
        self._encoder.eval()
        return np.stack(members)

    def _update_target(self) -> None:
        """目標エンコーダを指数移動平均で更新する。"""
        assert self._encoder is not None and self._target is not None
        decay = self.config.ema_decay
        with torch.no_grad():
            for online, target in zip(
                self._encoder.parameters(), self._target.parameters(), strict=True
            ):
                target.mul_(decay).add_(online, alpha=1.0 - decay)
