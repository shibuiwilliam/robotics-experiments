"""JEPA 時系列族の単体テスト（設計書 §4.2、§4.5）。

重点:

1. **崩壊防止の仕組みが外せない** — 分散項の重みを 0 にできない（最も気付きにくい失敗）。
2. **アンサンブルの各成員を個別に進める** — 平均だけ進めると不確実性が消える。
3. **koopman と同じ Protocol を独立に満たす** — モデル族の差し替えが成立する（ゲート G4）。
4. **seed で再現する** — 勾配学習なので seed 無しでは比較ができない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.bridge import LinearProbe, TemperatureScaler
from sbp.core.errors import WorldModelError
from sbp.testing import Fluent, SynthConfig, SynthWorld
from sbp.worldmodel import (
    JepaConfig,
    JepaTimeSeriesModel,
    LatentLevel,
    WorldModel,
)

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
EPOCHS = 40
"""テスト用のエポック数。品質の主張は統合テスト（G4）で行う。"""


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(start=T0, duration=timedelta(hours=2), cycles=2), seed=2026
    )


@pytest.fixture(scope="module")
def trained(world: SynthWorld) -> JepaTimeSeriesModel:
    model = JepaTimeSeriesModel.untrained(
        model_id="https://sbp.example/model/jepa-ts",
        version="1.0.0",
        domain="https://sbp.example/ent#Pump",
        channels=world.channels,
        config=JepaConfig(window=16, latent_dim=16, hidden_dim=64),
    )
    cut = int(len(world.timestamps) * 0.6)
    return model.fit(world.observations[:cut], epochs=EPOCHS, seed=2026)


class TestConfigGuards:
    def test_the_variance_term_cannot_be_switched_off(self) -> None:
        """これを 0 にできると、学習が収束して指標も良く見えるのに潜在が定数になる。"""
        with pytest.raises(WorldModelError, match="variance_weight must be positive"):
            JepaConfig(variance_weight=0.0)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"window": 0}, "window must be at least 1"),
            ({"latent_dim": 0}, "latent_dim must be at least 1"),
            ({"ensemble_size": 1}, "ensemble size must be 2..5"),
            ({"dropout": 0.0}, "dropout must be in"),
            ({"dropout": 1.0}, "dropout must be in"),
            ({"ema_decay": 1.0}, "ema_decay must be in"),
        ],
    )
    def test_invalid_settings_are_rejected(self, kwargs: dict[str, object], message: str) -> None:
        with pytest.raises(WorldModelError, match=message):
            JepaConfig(**kwargs)  # type: ignore[arg-type]

    def test_it_falls_back_to_cpu_without_mps(self) -> None:
        """CLAUDE.md: CUDA 前提のコードを書かない。MPS が無ければ CPU で動く。"""
        assert JepaConfig(device="cpu").resolved_device() == "cpu"
        assert JepaConfig(device="mps").resolved_device() in {"mps", "cpu"}


class TestProtocolConformance:
    def test_it_satisfies_the_protocol(self, trained: JepaTimeSeriesModel) -> None:
        assert isinstance(trained, WorldModel)

    def test_it_stays_within_the_parameter_budget(self, trained: JepaTimeSeriesModel) -> None:
        """CLAUDE.md: MacBook で学習・推論できる数百万パラメータ級まで。"""
        assert trained.parameter_count < 1_000_000

    def test_an_untrained_model_refuses_to_encode(self, world: SynthWorld) -> None:
        model = JepaTimeSeriesModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=world.channels
        )
        assert not model.is_trained
        with pytest.raises(WorldModelError, match="is not fitted"):
            model.encode(world.observations[:20], list(world.timestamps[:20]))


class TestTrainingDoesNotCollapse:
    def test_the_latent_keeps_its_spread(self, trained: JepaTimeSeriesModel) -> None:
        report = trained.report
        assert report is not None
        assert not report.collapsed, f"潜在が崩壊している: latent_std={report.latent_std:.4f}"
        assert report.latent_std > 0.5

    def test_dropping_the_variance_term_shrinks_the_latent(self, world: SynthWorld) -> None:
        """分散項が実際に効いていること（宣言だけで効いていない正則化を許さない）。"""
        cut = int(len(world.timestamps) * 0.6)
        weak = JepaTimeSeriesModel.untrained(
            model_id="m",
            version="1.0.0",
            domain="d",
            channels=world.channels,
            config=JepaConfig(
                window=16, latent_dim=16, variance_weight=1e-9, covariance_weight=0.0
            ),
        ).fit(world.observations[:cut], epochs=EPOCHS, seed=2026)
        strong = JepaTimeSeriesModel.untrained(
            model_id="m",
            version="1.0.0",
            domain="d",
            channels=world.channels,
            config=JepaConfig(window=16, latent_dim=16),
        ).fit(world.observations[:cut], epochs=EPOCHS, seed=2026)
        assert weak.report is not None and strong.report is not None
        assert strong.report.latent_std > weak.report.latent_std * 2.0

    def test_the_report_records_each_loss_term(self, trained: JepaTimeSeriesModel) -> None:
        """設計書 §4.6: 各項を個別にログする（合計だけでは原因が分からない）。"""
        report = trained.report
        assert report is not None
        assert report.prediction_loss >= 0.0
        assert report.variance_loss >= 0.0
        assert report.covariance_loss >= 0.0
        assert report.seed == 2026
        assert report.epochs == EPOCHS


class TestEncoding:
    def test_the_latent_carries_an_ensemble(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        """dropout を推論時にも効かせて認識的不確実性を出す。"""
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        assert state.ensemble is not None
        assert state.ensemble.shape[0] == trained.config.ensemble_size
        assert state.total_uncertainty > 0.0

    def test_a_short_window_is_rejected(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        with pytest.raises(WorldModelError, match="at least 16 observations"):
            trained.encode(world.observations[:4], list(world.timestamps[:4]))

    def test_the_series_encoding_matches_the_input_length(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        latents = trained.encode_series(world.observations)
        assert latents.shape == (world.observations.shape[0], trained.latent_dim)

    def test_a_channel_mismatch_is_rejected(self, trained: JepaTimeSeriesModel) -> None:
        with pytest.raises(WorldModelError, match="expects 3 channels"):
            trained.encode_series(np.zeros((40, 2)))


class TestTransition:
    def test_each_ensemble_member_advances(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        """平均だけを進めると、非線形な遷移では不確実性が消える。"""
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        after = trained.step(state)
        assert after.ensemble is not None
        assert after.ensemble.shape == (trained.config.ensemble_size, trained.latent_dim)
        assert after.total_uncertainty > 0.0
        assert after.at > state.at

    def test_an_action_without_training_is_refused(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        with pytest.raises(WorldModelError, match="fitted without actions"):
            trained.step(state, action=np.ones(1))

    def test_a_wrong_sized_context_is_refused(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        with pytest.raises(WorldModelError, match="conditioning vector must have"):
            trained.step(state, context=np.ones(3))

    def test_an_action_trained_model_requires_an_action(self, world: SynthWorld) -> None:
        """学習時に介入を与えたなら、以降は必ず渡させる（黙って 0 を補わない）。"""
        cut = int(len(world.timestamps) * 0.6)
        actions = np.zeros((cut, 1))
        actions[cut // 2 :] = 1.0
        model = JepaTimeSeriesModel.untrained(
            model_id="m",
            version="1.0.0",
            domain="d",
            channels=world.channels,
            config=JepaConfig(window=16, latent_dim=8, hidden_dim=32),
        ).fit(world.observations[:cut], actions=actions, epochs=10, seed=1)
        state = model.encode(world.observations[:cut], list(world.timestamps[:cut]))
        with pytest.raises(WorldModelError, match="fitted with 1-dimensional actions"):
            model.step(state)
        assert model.step(state, action=np.array([1.0])).ensemble is not None


class TestRollout:
    def test_it_returns_several_trajectories(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        trajectories = trained.rollout(state, horizon=4, samples=3, seed=11)
        assert len(trajectories) == 3
        assert all(len(trajectory.states) == 4 for trajectory in trajectories)

    def test_it_is_seed_stable(self, trained: JepaTimeSeriesModel, world: SynthWorld) -> None:
        """勾配学習なので、seed 無しでは 2 回の結果を比較できない。"""
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        first = trained.rollout(state, horizon=3, samples=2, seed=5)
        second = trained.rollout(state, horizon=3, samples=2, seed=5)
        assert np.allclose(first[0].states[-1].z, second[0].states[-1].z)


class TestHealthAndAttribution:
    def test_health_reports_all_three_metrics(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        cut = int(len(world.timestamps) * 0.6)
        health = trained.health(
            world.observations[:cut], list(world.timestamps[:cut]), align_residual=0.07
        )
        assert health.latent_shift >= 0.0
        assert health.pred_error >= 0.0
        assert health.align_residual == pytest.approx(0.07)
        assert health.wm_version == "https://sbp.example/model/jepa-ts/1.0.0"

    def test_shift_rises_on_a_different_distribution(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        cut = int(len(world.timestamps) * 0.6)
        baseline = trained.health(world.observations[:cut], list(world.timestamps[:cut]))
        drifted = trained.health(world.observations[:cut] * 3.0 + 5.0, list(world.timestamps[:cut]))
        assert drifted.latent_shift > baseline.latent_shift

    def test_attribution_covers_every_latent_dimension(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        attribution = trained.attribute(state, np.ones(trained.latent_dim), label="probe:running")
        assert len(attribution.latent_dims) == trained.latent_dim
        assert attribution.probe_id == "probe:running"

    def test_mismatched_weights_are_rejected(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        with pytest.raises(WorldModelError, match="weights have shape"):
            trained.attribute(state, np.ones(3))


class TestGroundingStillWorks:
    def test_a_linear_probe_separates_running(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        """M4 の主張: 族を替えても接地層のコードは変わらない。"""
        latents = trained.encode_series(world.observations)
        labels = world.oracle.labels_for(Fluent.RUNNING)
        cut = int(len(labels) * 0.6)
        probe = LinearProbe(dim=trained.latent_dim, seed=2026)
        probe.fit(latents[:cut], labels[:cut], epochs=500)
        probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))
        metrics = probe.evaluate(latents[cut:], labels[cut:], alignment_residual=0.0)
        assert metrics.auroc >= 0.85, f"AUROC {metrics.auroc:.4f}"

    def test_the_level_must_be_declared(
        self, trained: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        with pytest.raises(WorldModelError, match="has no fast latent"):
            trained.encode(
                world.observations[:100], list(world.timestamps[:100]), level=LatentLevel.FAST
            )
