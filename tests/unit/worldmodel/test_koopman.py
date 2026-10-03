"""Koopman 族の単体テスト（設計書 §4.2、§4.3）。

重点:

1. **M2 の接地層がそのまま動く** — Koopman 潜在に線形プローブを当てて AUROC / ECE が基準を満たす。
2. **地平が伸びると不確実性が増える** — ノイズ項を足さないと「遠い未来を確信」する予測になる。
3. **学習していない介入を受け付けない** — 介入なしで同定したモデルで What-if を許すと嘘になる。
4. **未学習での使用を拒む** — 零行列の遷移を黙って返さない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.bridge import LinearProbe, TemperatureScaler
from sbp.core.errors import WorldModelError
from sbp.testing import Fluent, SynthConfig, SynthWorld
from sbp.worldmodel import KoopmanConfig, KoopmanModel, LatentLevel, WorldModel

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
AUROC_TARGET = 0.85
ECE_TARGET = 0.05


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(start=T0, duration=timedelta(hours=4), cycles=4), seed=2026
    )


@pytest.fixture(scope="module")
def trained(world: SynthWorld) -> KoopmanModel:
    model = KoopmanModel.untrained(
        model_id="https://sbp.example/model/koopman",
        version="1.0.0",
        domain="https://sbp.example/ent#Pump",
        channels=world.channels,
        config=KoopmanConfig(delays=8),
    )
    cut = int(len(world.timestamps) * 0.6)
    return model.fit(world.observations[:cut], seed=2026)


class TestProtocolConformance:
    def test_it_satisfies_the_protocol(self, trained: KoopmanModel) -> None:
        assert isinstance(trained, WorldModel)

    def test_it_stays_within_the_parameter_budget(self, trained: KoopmanModel) -> None:
        """CLAUDE.md: MacBook で学習・推論できる数百万パラメータ級まで。"""
        assert trained.parameter_count < 1_000_000

    def test_the_latent_dimension_follows_the_delay_embedding(self, world: SynthWorld) -> None:
        model = KoopmanModel.untrained(
            model_id="m",
            version="1.0.0",
            domain="d",
            channels=world.channels,
            config=KoopmanConfig(delays=5),
        )
        assert model.latent_dim == len(world.channels) * 5


class TestConfigValidation:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"delays": 0}, "delays must be at least 1"),
            ({"rank": 0}, "rank must be positive"),
            ({"ridge": -1.0}, "ridge must be non-negative"),
            ({"ensemble_size": 1}, "ensemble size must be 2..5"),
            ({"ensemble_size": 9}, "ensemble size must be 2..5"),
        ],
    )
    def test_invalid_settings_are_rejected(self, kwargs: dict[str, object], message: str) -> None:
        with pytest.raises(WorldModelError, match=message):
            KoopmanConfig(**kwargs)  # type: ignore[arg-type]


class TestFitting:
    def test_an_untrained_model_refuses_to_encode(self, world: SynthWorld) -> None:
        """零行列の遷移を黙って返さない。"""
        model = KoopmanModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=world.channels
        )
        assert not model.is_trained
        with pytest.raises(WorldModelError, match="is not fitted"):
            model.encode(world.observations[:20], list(world.timestamps[:20]))

    def test_too_few_samples_are_rejected(self, world: SynthWorld) -> None:
        model = KoopmanModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=world.channels
        )
        with pytest.raises(WorldModelError, match="at least 16 samples"):
            model.fit(world.observations[:8], seed=1)

    def test_a_channel_count_mismatch_is_rejected(self, trained: KoopmanModel) -> None:
        with pytest.raises(WorldModelError, match="expects 3 channels"):
            trained.encode(np.zeros((20, 2)), [T0] * 20)

    def test_a_constant_channel_does_not_produce_nan(self) -> None:
        """std=0 のチャンネルで割ると潜在が NaN になる（標準化の落とし穴）。"""
        observations = np.column_stack([np.sin(np.arange(60) / 5.0), np.full(60, 3.0)])
        model = KoopmanModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=("varying", "constant")
        )
        model.fit(observations, seed=1)
        latents = model.encode_series(observations)
        assert np.isfinite(latents).all()

    def test_fitting_is_deterministic(self, world: SynthWorld) -> None:
        """最小二乗は決定的。seed を変えても同じ作用素が出る。"""
        cut = 400
        first = KoopmanModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=world.channels
        ).fit(world.observations[:cut], seed=1)
        second = KoopmanModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=world.channels
        ).fit(world.observations[:cut], seed=999)
        state = (T0, list(world.timestamps[:cut]))
        assert np.allclose(
            first.encode(world.observations[:cut], state[1]).z,
            second.encode(world.observations[:cut], state[1]).z,
        )


class TestEncoding:
    def test_the_latent_carries_uncertainty_and_version(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:100], list(world.timestamps[:100]))
        assert state.covariance is not None
        assert state.total_uncertainty > 0.0
        assert state.wm_version == "https://sbp.example/model/koopman/1.0.0"
        assert state.at == world.timestamps[99]

    def test_a_short_window_is_rejected(self, trained: KoopmanModel, world: SynthWorld) -> None:
        with pytest.raises(WorldModelError, match="at least 8 observations"):
            trained.encode(world.observations[:4], list(world.timestamps[:4]))

    def test_mismatched_timestamps_are_rejected(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        with pytest.raises(WorldModelError, match="timestamps"):
            trained.encode(world.observations[:50], list(world.timestamps[:40]))

    def test_the_series_encoding_keeps_the_label_alignment(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        """前詰めするのはラベル列と長さを揃えるため（ずれると時刻対応が静かに狂う）。"""
        latents = trained.encode_series(world.observations)
        assert latents.shape[0] == world.observations.shape[0]
        assert latents.shape[1] == trained.latent_dim

    def test_an_undeclared_level_is_rejected(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        with pytest.raises(WorldModelError, match="has no slow latent"):
            trained.encode(
                world.observations[:50], list(world.timestamps[:50]), level=LatentLevel.SLOW
            )


class TestTransition:
    def test_uncertainty_grows_with_the_horizon(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        """ノイズ項 Q を足さないと「遠い未来を確信している」予測になる。"""
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        after = trained.step(state)
        assert after.total_uncertainty > state.total_uncertainty
        assert after.at > state.at

    def test_an_action_without_training_is_refused(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        """介入なしで同定したモデルで What-if を許すと、答えが嘘になる。"""
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        with pytest.raises(WorldModelError, match="fitted without actions"):
            trained.step(state, action=np.ones(2))

    def test_a_wrong_sized_context_is_refused(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        """黙って切り詰めると条件付けの一部だけが効く状態になる。"""
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        with pytest.raises(WorldModelError, match="conditioning vector must have"):
            trained.step(state, context=np.ones(3))

    def test_a_context_shifts_the_transition(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        plain = trained.step(state)
        conditioned = trained.step(state, context=np.ones(trained.latent_dim))
        assert not np.allclose(plain.z, conditioned.z)

    def test_an_action_conditioned_model_accepts_actions(self, world: SynthWorld) -> None:
        cut = 400
        actions = np.zeros((cut, 1))
        actions[200:] = 1.0
        model = KoopmanModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=world.channels
        ).fit(world.observations[:cut], actions=actions, seed=1)
        state = model.encode(world.observations[:cut], list(world.timestamps[:cut]))
        with_action = model.step(state, action=np.array([1.0]))
        without = model.step(state, action=np.array([0.0]))
        assert not np.allclose(with_action.z, without.z)


class TestRollout:
    def test_it_returns_a_distribution_not_a_single_path(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        trajectories = trained.rollout(state, horizon=6, samples=3, seed=11)
        assert len(trajectories) == 3
        assert all(len(trajectory.states) == 6 for trajectory in trajectories)
        firsts = [trajectory.states[0].z for trajectory in trajectories]
        assert not np.allclose(firsts[0], firsts[1])

    def test_lead_times_advance(self, trained: KoopmanModel, world: SynthWorld) -> None:
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        trajectory = trained.rollout(state, horizon=4, samples=1, seed=11)[0]
        assert list(trajectory.lead_times()) == sorted(trajectory.lead_times())
        assert trajectory.horizon > 0.0

    def test_it_is_seed_stable(self, trained: KoopmanModel, world: SynthWorld) -> None:
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        first = trained.rollout(state, horizon=3, samples=2, seed=5)
        second = trained.rollout(state, horizon=3, samples=2, seed=5)
        assert np.allclose(first[0].states[-1].z, second[0].states[-1].z)

    @pytest.mark.parametrize(
        ("horizon", "samples", "message"),
        [
            (0, 1, "horizon must be at least 1"),
            (2, 0, "samples must be at least 1"),
        ],
    )
    def test_invalid_rollout_arguments_are_rejected(
        self,
        trained: KoopmanModel,
        world: SynthWorld,
        horizon: int,
        samples: int,
        message: str,
    ) -> None:
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        with pytest.raises(WorldModelError, match=message):
            trained.rollout(state, horizon=horizon, samples=samples, seed=1)

    def test_too_few_actions_are_rejected(self, world: SynthWorld) -> None:
        cut = 400
        actions = np.zeros((cut, 1))
        model = KoopmanModel.untrained(
            model_id="m", version="1.0.0", domain="d", channels=world.channels
        ).fit(world.observations[:cut], actions=actions, seed=1)
        state = model.encode(world.observations[:cut], list(world.timestamps[:cut]))
        with pytest.raises(WorldModelError, match="needs 5 actions"):
            model.rollout(state, horizon=5, actions=[np.array([0.0])], seed=1)


class TestAttribution:
    def test_it_folds_contributions_onto_channels(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        """潜在次元のままでは現場に説明できない。チャンネル（計測量）に畳んで返す。"""
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        attribution = trained.attribute(state, np.ones(trained.latent_dim), label="probe:running")
        assert set(attribution.modalities) == set(world.channels)
        assert attribution.probe_id == "probe:running"
        assert attribution.dominant_dim is not None

    def test_mismatched_weights_are_rejected(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        state = trained.encode(world.observations[:200], list(world.timestamps[:200]))
        with pytest.raises(WorldModelError, match="weights have shape"):
            trained.attribute(state, np.ones(3))


class TestHealth:
    def test_shift_is_small_on_the_training_distribution(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        cut = int(len(world.timestamps) * 0.6)
        health = trained.health(world.observations[:cut], list(world.timestamps[:cut]))
        assert health.latent_shift < 0.05
        assert health.pred_error < 0.3

    def test_shift_rises_on_a_different_distribution(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        """ドリフト監視が働くこと。入力が変わったのに指標が動かないなら監視になっていない。"""
        cut = int(len(world.timestamps) * 0.6)
        baseline = trained.health(world.observations[:cut], list(world.timestamps[:cut]))
        shifted = world.observations[:cut] * 3.0 + 5.0
        drifted = trained.health(shifted, list(world.timestamps[:cut]))
        assert drifted.latent_shift > baseline.latent_shift

    def test_the_alignment_residual_is_passed_in(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        """整合残差は bridge が計算する（ADR-0007）。渡されなければ 0 のまま。"""
        cut = 400
        without = trained.health(world.observations[:cut], list(world.timestamps[:cut]))
        assert without.align_residual == 0.0
        with_residual = trained.health(
            world.observations[:cut], list(world.timestamps[:cut]), align_residual=0.12
        )
        assert with_residual.align_residual == pytest.approx(0.12)


class TestGroundingStillWorks:
    """M4 の主張: 合成潜在を実モデルに替えても接地層の IF が変わらない。"""

    def test_a_linear_probe_on_koopman_latents_meets_the_phase1_targets(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        latents = trained.encode_series(world.observations)
        labels = world.oracle.labels_for(Fluent.RUNNING)
        cut = int(len(labels) * 0.6)
        probe = LinearProbe(dim=trained.latent_dim, seed=2026)
        probe.fit(latents[:cut], labels[:cut], epochs=500)
        probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))
        metrics = probe.evaluate(latents[cut:], labels[cut:], alignment_residual=0.0)
        assert metrics.auroc >= AUROC_TARGET, f"AUROC {metrics.auroc:.4f}"
        assert metrics.ece <= ECE_TARGET, f"ECE {metrics.ece:.4f}"

    def test_the_probe_needs_no_knowledge_of_the_model(self, trained: KoopmanModel) -> None:
        """プローブは潜在の次元数しか知らない（ADR-0007 の境界）。"""
        probe = LinearProbe(dim=trained.latent_dim, seed=1)
        assert probe.dim == trained.latent_dim
