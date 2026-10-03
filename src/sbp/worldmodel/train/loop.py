"""学習ループと各項の記録（設計書 §4.5、§4.6）。

設計書 §4.5 の 7 段階（自己教師事前学習 → 記号条件付け → 弱教師プローブ → 論理制約注入 →
整合学習 → 較正 → 能動学習）のうち、このモジュールが受け持つのは**段階 1・4・5 を同じループで
回す部分**である。段階 3（プローブ学習）と 6（較正）は ``bridge`` の仕事で、段階 7（能動学習）は
``reasoning`` のスケジューラが駆動する。

**各段階のゲートを記録する。** 設計書 §4.5 の表は段階ごとにゲート（予測誤差・潜在崩壊指標・
AUROC・制約違反率・残差分布・ECE）を定めている。このループは自分が測れるもの——予測誤差、
潜在の広がり、各損失項——を :class:`EpochRecord` に残し、測れないもの（AUROC、ECE）は
呼び出し側が評価する。

**時間順を前提とする損失項に注意する。** 既定のバッチは毎エポック並べ替えられる（確率的勾配
降下の前提）。連続する潜在の差を見る項（物理則の慣性など）は、並べ替えたバッチでは無意味な量を
最小化し、潜在を潰す方向に働く。そのための ``contiguous_batches`` 引数であり、既定を偽にして
いるのは「気付かずに時間順を仮定する」ほうが危険だからである。

**学習の来歴を残す。** ``seed``、デバイス、重み、エポック数、各項の推移。これが無いと
「このモデル版はどうやって作ったか」が再現できず、モデル版 IRI が意味を持たなくなる
（設計書 §4.3 の ``trainingProvenance``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import torch

from sbp.core.errors import WorldModelError
from sbp.core.logging import get_logger
from sbp.worldmodel.train.losses import LossWeights, TermValues

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray
    from torch import Tensor

__all__ = ["EpochRecord", "TrainingProvenance", "train_latent_predictor"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EpochRecord:
    """1 エポックの記録。

    Attributes:
        epoch: エポック番号（1 始まり）。
        terms: 各損失項の値。
        latent_std: 潜在次元ごとの標準偏差の平均（崩壊の指標）。
        progress: 学習の進捗（0〜1）。λ_align のスケジュールに対応する。
    """

    epoch: int
    terms: TermValues
    latent_std: float
    progress: float

    @property
    def collapsing(self) -> bool:
        """潜在が潰れかけているか（設計書 §4.5 段階 1 のゲート）。"""
        return self.latent_std < 0.1


@dataclass(frozen=True, slots=True)
class TrainingProvenance:
    """学習の来歴（``meta.trainingProvenance`` に入れる）。

    Attributes:
        seed: 乱数シード。
        device: 計算デバイス。
        epochs: エポック数。
        weights: 使った損失の重み。
        history: 各エポックの記録。
        samples: 学習に使った標本数。
    """

    seed: int
    device: str
    epochs: int
    weights: LossWeights
    history: tuple[EpochRecord, ...]
    samples: int

    @property
    def final(self) -> EpochRecord:
        """最終エポックの記録。"""
        if not self.history:
            raise WorldModelError("this training run has no epoch records")
        return self.history[-1]

    @property
    def collapsed(self) -> bool:
        """最終的に潜在が崩壊したか。"""
        return self.final.collapsing

    def term_curve(self, name: str) -> tuple[float, ...]:
        """ある項の推移（個別にログする規約の読み出し側）。

        Args:
            name: ``prediction`` / ``physics`` / ``probe`` / ``logic`` / ``align``。

        Raises:
            WorldModelError: 未知の項名。
        """
        try:
            return tuple(record.terms.as_dict()[name] for record in self.history)
        except KeyError as exc:
            raise WorldModelError(f"unknown loss term {name!r}") from exc

    def align_schedule(self) -> tuple[float, ...]:
        """λ_align の推移（後期に強まっていることの確認用）。"""
        return tuple(record.terms.align_weight for record in self.history)


def train_latent_predictor(
    *,
    encoder: torch.nn.Module,
    predictor: torch.nn.Module,
    contexts: NDArray[np.float64],
    targets: NDArray[np.float64],
    epochs: int,
    batch_size: int = 64,
    learning_rate: float = 1e-3,
    weights: LossWeights | None = None,
    physics_term: Callable[[Tensor], Tensor] | None = None,
    probe_term: Callable[[Tensor], Tensor] | None = None,
    logic_term: Callable[[Tensor], Tensor] | None = None,
    align_term: Callable[[Tensor], Tensor] | None = None,
    target_encoder: torch.nn.Module | None = None,
    ema_decay: float = 0.99,
    contiguous_batches: bool = False,
    device: str = "cpu",
    seed: int,
) -> TrainingProvenance:
    """潜在予測を合成損失で学習する（設計書 §4.5 段階 1・4・5）。

    ``*_term`` は省略できる（その項が 0 になる）。省略を許すのは、設計書 §4.5 が段階を分けて
    いるためである——段階 1 では弱ラベルも整合もまだ無い。**省略したことは記録に残る**
    （該当項が常に 0 の曲線として見える）ので、「入れたつもりで効いていない」状態と区別できる。

    Args:
        encoder: 窓 → 潜在。
        predictor: 潜在 → 次の潜在。
        contexts: 文脈窓（``(標本, 平坦化した窓)``）。
        targets: 目標窓（同じ形）。
        epochs: エポック数。
        batch_size: バッチサイズ。
        learning_rate: 学習率。
        weights: 損失の重み（既定は :class:`LossWeights`）。
        physics_term: L_phys。
        probe_term: L_probe（``bridge`` が与える）。
        logic_term: L_logic。
        align_term: L_align（``bridge`` が与える）。
        target_encoder: 目標エンコーダ。``None`` なら ``encoder`` 自身を stop-gradient で使う。
        ema_decay: 目標エンコーダの指数移動平均。
        contiguous_batches: バッチを時間順の連続スライスにする。**時間順を前提とする損失項**
            （:func:`~sbp.worldmodel.train.losses.smoothness_penalty` など）を使うときは真に
            すること。偽（既定）のときバッチは毎エポック並べ替えられ、連続性を仮定した項は
            無意味な量を最小化する——``smoothness_penalty`` の場合は「並べ替えた標本どうしを
            近づける」ことになり、**潜在を能動的に潰す**（実測: latent_std 0.845 → 0.079）。
        device: 計算デバイス。
        seed: 乱数シード。

    Returns:
        学習の来歴。

    Raises:
        WorldModelError: 形が合わない、または標本・エポックが足りない場合。
    """
    if contexts.shape != targets.shape:
        raise WorldModelError(
            f"contexts {contexts.shape} and targets {targets.shape} must have the same shape"
        )
    if contexts.shape[0] < 2:
        raise WorldModelError(
            f"need at least 2 samples to compute a spread, got {contexts.shape[0]}"
        )
    if epochs < 1:
        raise WorldModelError(f"epochs must be at least 1, got {epochs}")

    torch.manual_seed(seed)
    settings = weights or LossWeights()
    torch_device = torch.device(device)
    encoder.to(torch_device)
    predictor.to(torch_device)
    if target_encoder is not None:
        target_encoder.to(torch_device)
        for parameter in target_encoder.parameters():
            parameter.requires_grad_(False)

    optimizer = torch.optim.Adam([*encoder.parameters(), *predictor.parameters()], lr=learning_rate)
    context_tensor = torch.tensor(contexts, dtype=torch.float32, device=torch_device)
    target_tensor = torch.tensor(targets, dtype=torch.float32, device=torch_device)
    generator = np.random.default_rng(seed)

    history: list[EpochRecord] = []
    for epoch in range(1, epochs + 1):
        # 進捗は「終わったエポック数 / 全体」。1 エポック目から λ_align が効かないようにする
        progress = (epoch - 1) / max(epochs - 1, 1)
        align_weight = settings.align_at(progress)
        order = (
            np.arange(context_tensor.shape[0])
            if contiguous_batches
            else generator.permutation(context_tensor.shape[0])
        )
        last: TermValues | None = None
        latent_snapshot: Tensor | None = None

        for start in range(0, len(order), batch_size):
            batch = torch.tensor(order[start : start + batch_size], device=torch_device)
            if batch.numel() < 2:
                continue
            latents = encoder(context_tensor[batch])
            terms = _batch_terms(
                latents=latents,
                expected=_expected_latents(encoder, target_encoder, target_tensor[batch]),
                predictor=predictor,
                physics_term=physics_term,
                probe_term=probe_term,
                logic_term=logic_term,
                align_term=align_term,
                device=torch_device,
            )
            loss = (
                terms["prediction"]
                + settings.physics * terms["physics"]
                + settings.probe * terms["probe"]
                + settings.logic * terms["logic"]
                + align_weight * terms["align"]
            )
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            if target_encoder is not None:
                _update_ema(encoder, target_encoder, ema_decay)

            last = TermValues(
                prediction=float(terms["prediction"].detach().cpu()),
                physics=float(terms["physics"].detach().cpu()),
                probe=float(terms["probe"].detach().cpu()),
                logic=float(terms["logic"].detach().cpu()),
                align=float(terms["align"].detach().cpu()),
                align_weight=align_weight,
            )
            latent_snapshot = latents.detach()

        if last is None or latent_snapshot is None:
            raise WorldModelError(
                f"epoch {epoch} produced no batch of at least 2 samples; lower batch_size or "
                "supply more data"
            )
        latent_std = float(latent_snapshot.std(dim=0).mean().cpu())
        history.append(
            EpochRecord(epoch=epoch, terms=last, latent_std=latent_std, progress=progress)
        )

    provenance = TrainingProvenance(
        seed=seed,
        device=str(torch_device),
        epochs=epochs,
        weights=settings,
        history=tuple(history),
        samples=int(contexts.shape[0]),
    )
    log.info(
        "worldmodel.training_complete",
        epochs=epochs,
        device=str(torch_device),
        seed=seed,
        collapsed=provenance.collapsed,
        **provenance.final.terms.as_dict(),
    )
    return provenance


def _expected_latents(
    encoder: torch.nn.Module, target_encoder: torch.nn.Module | None, windows: Tensor
) -> Tensor:
    """目標側の潜在（勾配を流さない）。

    目標エンコーダが無ければオンライン側を stop-gradient で使う。どちらでも**勾配を流さない**
    ことが要点で、流すと両者が定数に寄って崩壊する。
    """
    with torch.no_grad():
        source = target_encoder if target_encoder is not None else encoder
        return source(windows)


def _batch_terms(
    *,
    latents: Tensor,
    expected: Tensor,
    predictor: torch.nn.Module,
    physics_term: Callable[[Tensor], Tensor] | None,
    probe_term: Callable[[Tensor], Tensor] | None,
    logic_term: Callable[[Tensor], Tensor] | None,
    align_term: Callable[[Tensor], Tensor] | None,
    device: torch.device,
) -> dict[str, Tensor]:
    """1 バッチ分の各項を評価する（合成は呼び出し側）。"""
    return {
        "prediction": torch.nn.functional.mse_loss(predictor(latents), expected),
        "physics": _term(physics_term, latents, device),
        "probe": _term(probe_term, latents, device),
        "logic": _term(logic_term, latents, device),
        "align": _term(align_term, latents, device),
    }


def _term(term: Callable[[Tensor], Tensor] | None, latents: Tensor, device: torch.device) -> Tensor:
    """損失項を評価する（省略されていれば 0）。"""
    if term is None:
        return torch.zeros((), device=device)
    value = term(latents)
    if value.ndim != 0:
        raise WorldModelError(f"a loss term must return a scalar, got shape {tuple(value.shape)}")
    return value


def _update_ema(online: torch.nn.Module, target: torch.nn.Module, decay: float) -> None:
    """目標エンコーダを指数移動平均で更新する。"""
    with torch.no_grad():
        for source, destination in zip(online.parameters(), target.parameters(), strict=True):
            destination.mul_(decay).add_(source, alpha=1.0 - decay)


def stage_gate_passed(
    provenance: TrainingProvenance,
    *,
    max_prediction_loss: float,
    min_latent_std: float = 0.1,
) -> bool:
    """設計書 §4.5 段階 1 のゲート（予測誤差 ∧ 潜在崩壊指標）。

    AND で判定するのが要点である。予測誤差だけを見ると、崩壊した潜在（予測は完璧）が通る。

    Args:
        provenance: 学習の来歴。
        max_prediction_loss: 予測損失の上限。
        min_latent_std: 潜在の広がりの下限。
    """
    final = provenance.final
    return final.terms.prediction <= max_prediction_loss and final.latent_std >= min_latent_std
