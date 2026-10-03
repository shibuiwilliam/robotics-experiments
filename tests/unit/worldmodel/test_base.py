"""`WorldModel` Protocol と潜在状態の単体テスト（設計書 §4.3、§4.4、ADR-0007）。

重点:

1. **点推定の潜在を作れない** — 共分散かアンサンブルのどちらかが必須（不変条件 7 の前提）。
2. **版を持たない潜在を作れない** — as-of 説明が成立しなくなる（設計書 §8.8）。
3. **`latest` という版を許さない** — 今日の信念を当時のモデルで説明できなくなる。
4. **`probe` / `explain` は Protocol に無い** — 記号の決定は接地層（ADR-0007）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.core.errors import WorldModelError
from sbp.worldmodel import (
    Attribution,
    LatentLevel,
    LatentSpec,
    LatentState,
    ModelHealth,
    Trajectory,
    WorldModel,
    WorldModelMeta,
)

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
WM = "https://sbp.example/model/koopman/1.0.0"


def _state(**overrides: object) -> LatentState:
    defaults: dict[str, object] = {
        "z": np.array([1.0, 2.0, 3.0]),
        "level": LatentLevel.MID,
        "at": T0,
        "wm_version": WM,
        "covariance": np.eye(3) * 0.04,
    }
    defaults.update(overrides)
    return LatentState(**defaults)  # type: ignore[arg-type]


class TestLatentLevel:
    def test_levels_have_distinct_timescales(self) -> None:
        """階層を分けるのは「同じモデルで ms と日を扱えない」からである。"""
        scales = [level.typical_step_s for level in LatentLevel]
        assert scales == sorted(scales)
        assert len(set(scales)) == len(scales)


class TestLatentSpec:
    def test_an_empty_spec_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="at least one level"):
            LatentSpec(dims={})

    def test_a_non_positive_dimension_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="must be positive"):
            LatentSpec(dims={LatentLevel.MID: 0})

    def test_an_undeclared_level_fails_loudly(self) -> None:
        """宣言していない階層を黙って 0 次元で返さない。"""
        spec = LatentSpec(dims={LatentLevel.MID: 8})
        with pytest.raises(WorldModelError, match="has no slow latent"):
            spec.dim_of(LatentLevel.SLOW)

    def test_timescale_falls_back_to_the_level_default(self) -> None:
        spec = LatentSpec(dims={LatentLevel.MID: 8})
        assert spec.step_of(LatentLevel.MID) == LatentLevel.MID.typical_step_s

    def test_an_explicit_timescale_wins(self) -> None:
        spec = LatentSpec(dims={LatentLevel.MID: 8}, timescales={LatentLevel.MID: 5.0})
        assert spec.step_of(LatentLevel.MID) == pytest.approx(5.0)


class TestWorldModelMeta:
    def test_latest_is_forbidden(self) -> None:
        """今日の信念を「当時のモデル」で説明できなくなる。"""
        with pytest.raises(WorldModelError, match="'latest' is forbidden"):
            WorldModelMeta(
                model_id="m",
                version="latest",
                domain="d",
                modalities=("a",),
                latent_spec=LatentSpec(dims={LatentLevel.MID: 4}),
            )

    def test_a_model_without_modalities_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="no input modalities"):
            WorldModelMeta(
                model_id="m",
                version="1.0.0",
                domain="d",
                modalities=(),
                latent_spec=LatentSpec(dims={LatentLevel.MID: 4}),
            )

    def test_the_version_iri_identifies_the_weights(self) -> None:
        meta = WorldModelMeta(
            model_id="https://sbp.example/model/koopman",
            version="1.2.0",
            domain="d",
            modalities=("a",),
            latent_spec=LatentSpec(dims={LatentLevel.MID: 4}),
        )
        assert meta.version_iri == "https://sbp.example/model/koopman/1.2.0"


class TestLatentState:
    def test_a_point_estimate_is_rejected(self) -> None:
        """点推定の潜在からは較正済みの確信度が作れない（不変条件 7）。"""
        with pytest.raises(WorldModelError, match="must carry its uncertainty"):
            LatentState(z=np.zeros(3), level=LatentLevel.MID, at=T0, wm_version=WM)

    def test_a_state_without_a_version_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="must name the model version"):
            _state(wm_version="")

    def test_a_single_member_ensemble_is_rejected(self) -> None:
        """1 標本のアンサンブルは広がりを表せない（不確実性を持っているふりになる）。"""
        with pytest.raises(WorldModelError, match="at least 2 members"):
            LatentState(
                z=np.zeros(3),
                level=LatentLevel.MID,
                at=T0,
                wm_version=WM,
                ensemble=np.zeros((1, 3)),
            )

    def test_shape_mismatches_are_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="covariance must be"):
            _state(covariance=np.eye(2))
        with pytest.raises(WorldModelError, match="ensemble must be"):
            LatentState(
                z=np.zeros(3),
                level=LatentLevel.MID,
                at=T0,
                wm_version=WM,
                ensemble=np.zeros((3, 2)),
            )

    def test_spread_comes_from_whichever_uncertainty_is_present(self) -> None:
        gaussian = _state(covariance=np.diag([0.04, 0.09, 0.16]))
        assert gaussian.spread == pytest.approx([0.2, 0.3, 0.4])
        members = np.array([[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]])
        ensemble = LatentState(
            z=members.mean(axis=0),
            level=LatentLevel.MID,
            at=T0,
            wm_version=WM,
            ensemble=members,
        )
        assert ensemble.spread == pytest.approx([np.sqrt(2.0)] * 3)

    def test_sampling_an_ensemble_reuses_its_members(self) -> None:
        """アンサンブルは既に「モデルが出した分布」。ガウスで近似し直すと裾が消える。"""
        members = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
        state = LatentState(
            z=members.mean(axis=0),
            level=LatentLevel.MID,
            at=T0,
            wm_version=WM,
            ensemble=members,
        )
        drawn = state.samples(6, seed=1)
        assert drawn.shape == (6, 2)
        # 循環して使うので、元の標本以外の値は出ない
        for row in drawn:
            assert any(np.allclose(row, member) for member in members)

    def test_sampling_a_gaussian_is_seed_stable(self) -> None:
        state = _state()
        assert np.allclose(state.samples(4, seed=5), state.samples(4, seed=5))
        assert not np.allclose(state.samples(4, seed=5), state.samples(4, seed=6))

    def test_a_non_positive_sample_count_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="must be positive"):
            _state().samples(0, seed=1)

    def test_naive_timestamps_are_normalised_to_utc(self) -> None:
        """不変条件 8: timezone-aware UTC のみ。"""
        state = _state(at=datetime(2026, 9, 12, 9, tzinfo=UTC))
        assert state.at.tzinfo is UTC


class TestTrajectory:
    def test_states_must_advance_in_time(self) -> None:
        first = _state(at=T0)
        second = _state(at=T0)
        with pytest.raises(WorldModelError, match="must advance in time"):
            Trajectory(states=(first, second), horizon_origin=T0)

    def test_an_empty_trajectory_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="at least one state"):
            Trajectory(states=(), horizon_origin=T0)

    def test_lead_times_are_measured_from_the_origin(self) -> None:
        """起点別に精度を追跡するため（設計書 §8.3 の「信頼できる先行時間」）。"""
        states = tuple(_state(at=T0 + timedelta(seconds=10 * (index + 1))) for index in range(3))
        trajectory = Trajectory(states=states, horizon_origin=T0)
        assert trajectory.lead_times() == (10.0, 20.0, 30.0)
        assert trajectory.horizon == pytest.approx(30.0)


class TestModelHealth:
    def _health(self, **overrides: float) -> ModelHealth:
        values = {"latent_shift": 0.05, "pred_error": 0.1, "align_residual": 0.05}
        values.update(overrides)
        return ModelHealth(samples=100, wm_version=WM, **values)  # type: ignore[arg-type]

    def test_all_three_must_pass(self) -> None:
        """予測誤差だけを見ると「当たるが記号と合わない潜在」を見逃す（設計書 §4.8）。"""
        thresholds = {"shift_warn": 0.1, "error_warn": 0.3, "residual_warn": 0.2}
        assert self._health().is_healthy(**thresholds)
        assert not self._health(align_residual=0.5).is_healthy(**thresholds)
        assert not self._health(pred_error=0.9).is_healthy(**thresholds)
        assert not self._health(latent_shift=0.9).is_healthy(**thresholds)

    def test_breaches_name_the_failing_metrics(self) -> None:
        breached = self._health(latent_shift=0.9, align_residual=0.5).breaches(
            shift_warn=0.1, error_warn=0.3, residual_warn=0.2
        )
        assert set(breached) == {"latent_shift", "align_residual"}


class TestAttribution:
    def test_the_dominant_dimension_uses_absolute_contribution(self) -> None:
        """負の寄与も「効いている」。符号で順位を決めると大きな負の寄与を見落とす。"""
        attribution = Attribution(probe_id="p", latent_dims={0: 0.2, 1: -0.9, 2: 0.4})
        assert attribution.dominant_dim == 1

    def test_no_dimensions_means_no_dominant(self) -> None:
        assert Attribution(probe_id="p", latent_dims={}).dominant_dim is None


class TestProtocolSurface:
    def test_probe_and_explain_are_not_in_the_protocol(self) -> None:
        """記号の決定は接地層（ADR-0007）。WM に probe を置くと層依存に反する。"""
        members = set(WorldModel.__protocol_attrs__)  # type: ignore[attr-defined]
        assert "probe" not in members
        assert "explain" not in members

    def test_the_protocol_requires_the_six_methods(self) -> None:
        members = set(WorldModel.__protocol_attrs__)  # type: ignore[attr-defined]
        assert {"encode", "step", "rollout", "embed", "health", "attribute", "meta"} <= members
