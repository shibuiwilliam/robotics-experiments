"""学習パイプラインの単体テスト（設計書 §4.5、§4.6）。

重点:

1. **λ_align は後期に効く** — 初期から強いと潜在が記号に過剰適合する。
2. **各項を個別に記録する** — 合計だけでは「何が下がったのか」が分からない。
3. **省略した項は 0 の曲線として見える** — 「入れたつもりで効いていない」と区別できる。
4. **段階ゲートは AND** — 予測誤差だけを見ると崩壊した潜在が通る。
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest
import torch
from torch import nn

from sbp.core.errors import WorldModelError
from sbp.worldmodel import (
    LossWeights,
    TermValues,
    exclusivity_penalty,
    smoothness_penalty,
    stage_gate_passed,
    train_latent_predictor,
)

LATENT = 8
WINDOW = 8
CHANNELS = 2


def _series(length: int = 400) -> np.ndarray:
    grid = np.linspace(0.0, 20.0, length)
    return np.column_stack([np.sin(grid), np.cos(grid)])


def _windows(series: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows = series.shape[0] - WINDOW - 1
    contexts = np.stack([series[index : index + WINDOW].reshape(-1) for index in range(rows)])
    targets = np.stack(
        [series[index + 1 : index + 1 + WINDOW].reshape(-1) for index in range(rows)]
    )
    return contexts, targets


def _modules() -> tuple[nn.Module, nn.Module]:
    torch.manual_seed(0)
    encoder = nn.Sequential(nn.Linear(WINDOW * CHANNELS, 32), nn.GELU(), nn.Linear(32, LATENT))
    predictor = nn.Sequential(nn.Linear(LATENT, 32), nn.GELU(), nn.Linear(32, LATENT))
    return encoder, predictor


def _variance_term(latents: torch.Tensor) -> torch.Tensor:
    """崩壊防止項（学習ループ側が持たないので呼び出し側が渡す形を確認する）。"""
    std = torch.sqrt(latents.var(dim=0) + 1e-4)
    return torch.mean(torch.relu(1.0 - std))


class TestLossWeights:
    def test_align_is_zero_before_the_schedule_starts(self) -> None:
        """初期から整合損失を強めると、記号側の遷移 T の誤りまで潜在が学ぶ。"""
        weights = LossWeights(align=0.5, align_start_fraction=0.5)
        assert weights.align_at(0.0) == 0.0
        assert weights.align_at(0.49) == 0.0

    def test_align_ramps_linearly_to_its_final_value(self) -> None:
        """段階的に切り替えると損失が不連続になり、その時点で潜在が大きく動く。"""
        weights = LossWeights(align=0.4, align_start_fraction=0.5)
        assert weights.align_at(0.75) == pytest.approx(0.2)
        assert weights.align_at(1.0) == pytest.approx(0.4)

    def test_progress_outside_the_unit_interval_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="progress must be in"):
            LossWeights().align_at(1.5)

    def test_negative_weights_are_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="must be non-negative"):
            LossWeights(physics=-0.1)

    def test_a_start_fraction_of_one_is_rejected(self) -> None:
        """1 にすると整合損失が一度も効かない（宣言と実際が食い違う）。"""
        with pytest.raises(WorldModelError, match="align_start_fraction"):
            LossWeights(align_start_fraction=1.0)


class TestTermValues:
    def test_the_total_uses_the_scheduled_align_weight(self) -> None:
        """合計には「そのステップで実際に使った」λ_align が入る。"""
        values = TermValues(
            prediction=1.0, physics=2.0, probe=3.0, logic=4.0, align=5.0, align_weight=0.1
        )
        weights = LossWeights(physics=0.5, probe=1.0, logic=0.25, align=0.9)
        assert values.total(weights) == pytest.approx(1.0 + 1.0 + 3.0 + 1.0 + 0.5)

    def test_every_term_is_reported_separately(self) -> None:
        reported = TermValues(prediction=1.0).as_dict()
        assert set(reported) == {
            "prediction",
            "physics",
            "probe",
            "logic",
            "align",
            "align_weight",
        }


class TestPhysicsAndLogicTerms:
    def test_smoothness_punishes_jumpy_latents(self) -> None:
        smooth = torch.tensor([[0.0, 0.0], [0.1, 0.1], [0.2, 0.2]])
        jumpy = torch.tensor([[0.0, 0.0], [5.0, -5.0], [0.0, 0.0]])
        assert smoothness_penalty(smooth) < smoothness_penalty(jumpy)

    def test_smoothness_needs_two_steps(self) -> None:
        with pytest.raises(WorldModelError, match="at least 2 consecutive"):
            smoothness_penalty(torch.zeros((1, 2)))

    def test_exclusivity_only_punishes_the_overlap(self) -> None:
        """排他の衝突は拒否ではなく不一致候補。ハード制約にすると食い違いが消える。"""
        compatible = exclusivity_penalty(torch.tensor([0.9, 0.1]), torch.tensor([0.05, 0.8]))
        colliding = exclusivity_penalty(torch.tensor([0.9, 0.9]), torch.tensor([0.9, 0.9]))
        assert compatible == pytest.approx(0.0)
        assert colliding > 0.5

    def test_exclusivity_requires_matching_shapes(self) -> None:
        with pytest.raises(WorldModelError, match="matching shapes"):
            exclusivity_penalty(torch.zeros(3), torch.zeros(4))


class TestTrainingLoop:
    def test_it_records_every_epoch(self) -> None:
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        provenance = train_latent_predictor(
            encoder=encoder,
            predictor=predictor,
            contexts=contexts,
            targets=targets,
            epochs=6,
            seed=7,
        )
        assert len(provenance.history) == 6
        assert [record.epoch for record in provenance.history] == [1, 2, 3, 4, 5, 6]
        assert provenance.seed == 7
        assert provenance.samples == contexts.shape[0]

    def test_the_align_weight_is_zero_early_and_positive_late(self) -> None:
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        provenance = train_latent_predictor(
            encoder=encoder,
            predictor=predictor,
            contexts=contexts,
            targets=targets,
            epochs=10,
            weights=LossWeights(align=0.5, align_start_fraction=0.5),
            align_term=lambda latents: (latents**2).mean(),
            seed=7,
        )
        schedule = provenance.align_schedule()
        assert schedule[0] == 0.0
        assert schedule[-1] == pytest.approx(0.5)
        assert all(later >= earlier for earlier, later in pairwise(schedule))

    def test_an_omitted_term_shows_as_a_zero_curve(self) -> None:
        """「入れたつもりで効いていない」状態と区別できるようにする。"""
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        provenance = train_latent_predictor(
            encoder=encoder,
            predictor=predictor,
            contexts=contexts,
            targets=targets,
            epochs=4,
            physics_term=smoothness_penalty,
            contiguous_batches=True,
            seed=7,
        )
        assert set(provenance.term_curve("probe")) == {0.0}
        assert any(value > 0.0 for value in provenance.term_curve("physics"))

    def test_the_prediction_loss_decreases(self) -> None:
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        provenance = train_latent_predictor(
            encoder=encoder,
            predictor=predictor,
            contexts=contexts,
            targets=targets,
            epochs=15,
            seed=7,
        )
        curve = provenance.term_curve("prediction")
        assert curve[-1] < curve[0]

    def test_an_unknown_term_name_fails_loudly(self) -> None:
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        provenance = train_latent_predictor(
            encoder=encoder,
            predictor=predictor,
            contexts=contexts,
            targets=targets,
            epochs=2,
            seed=7,
        )
        with pytest.raises(WorldModelError, match="unknown loss term"):
            provenance.term_curve("not_a_term")

    def test_a_non_scalar_term_is_rejected(self) -> None:
        """スカラーでない項を黙って縮約すると、何を最小化しているのか分からなくなる。"""
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        with pytest.raises(WorldModelError, match="must return a scalar"):
            train_latent_predictor(
                encoder=encoder,
                predictor=predictor,
                contexts=contexts,
                targets=targets,
                epochs=1,
                physics_term=lambda latents: latents.mean(dim=0),
                seed=7,
            )

    def test_mismatched_shapes_are_rejected(self) -> None:
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        with pytest.raises(WorldModelError, match="must have the same shape"):
            train_latent_predictor(
                encoder=encoder,
                predictor=predictor,
                contexts=contexts,
                targets=targets[:-1],
                epochs=1,
                seed=7,
            )

    def test_it_is_seed_stable(self) -> None:
        contexts, targets = _windows(_series())
        results = []
        for _ in range(2):
            encoder, predictor = _modules()
            provenance = train_latent_predictor(
                encoder=encoder,
                predictor=predictor,
                contexts=contexts,
                targets=targets,
                epochs=4,
                seed=11,
            )
            results.append(provenance.term_curve("prediction"))
        assert results[0] == pytest.approx(results[1])


class TestStageGate:
    def test_a_time_ordered_term_on_shuffled_batches_collapses_the_latent(self) -> None:
        """実装中に見つけた落とし穴。

        ``smoothness_penalty`` は連続する潜在の差を罰する。バッチが並べ替えられていると
        「無関係な標本どうしを近づける」ことになり、潜在を能動的に潰す。``contiguous_batches``
        を忘れた呼び出しがこれに当たる。ゲートがこれを捕まえられることを押さえておく。
        """
        contexts, targets = _windows(_series())
        shuffled_encoder, shuffled_predictor = _modules()
        shuffled = train_latent_predictor(
            encoder=shuffled_encoder,
            predictor=shuffled_predictor,
            contexts=contexts,
            targets=targets,
            epochs=20,
            physics_term=smoothness_penalty,
            weights=LossWeights(physics=0.5),
            seed=7,
        )
        ordered_encoder, ordered_predictor = _modules()
        ordered = train_latent_predictor(
            encoder=ordered_encoder,
            predictor=ordered_predictor,
            contexts=contexts,
            targets=targets,
            epochs=20,
            physics_term=smoothness_penalty,
            weights=LossWeights(physics=0.5),
            contiguous_batches=True,
            seed=7,
        )
        assert shuffled.final.latent_std < ordered.final.latent_std
        assert shuffled.collapsed
        assert not stage_gate_passed(shuffled, max_prediction_loss=1.0)

    def test_a_healthy_run_passes_the_gate(self) -> None:
        """予測誤差 ∧ 潜在の広がりの両方を満たせば段階 1 を通る。"""
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        provenance = train_latent_predictor(
            encoder=encoder,
            predictor=predictor,
            contexts=contexts,
            targets=targets,
            epochs=20,
            logic_term=_variance_term,
            weights=LossWeights(logic=1.0),
            seed=7,
        )
        assert not provenance.collapsed
        assert stage_gate_passed(provenance, max_prediction_loss=1.0)

    def test_the_gate_is_an_and_not_an_or(self) -> None:
        """崩壊した潜在は予測が完璧になる。予測誤差だけのゲートは通ってしまう。"""
        encoder, predictor = _modules()
        contexts, targets = _windows(_series())
        provenance = train_latent_predictor(
            encoder=encoder,
            predictor=predictor,
            contexts=contexts,
            targets=targets,
            epochs=20,
            logic_term=_variance_term,
            weights=LossWeights(logic=1.0),
            seed=7,
        )
        # 予測誤差は満たすが、潜在の広がりの下限を非現実的に上げると落ちる
        assert stage_gate_passed(provenance, max_prediction_loss=1.0, min_latent_std=0.1)
        assert not stage_gate_passed(provenance, max_prediction_loss=1.0, min_latent_std=99.0)
