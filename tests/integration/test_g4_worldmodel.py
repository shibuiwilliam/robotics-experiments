"""ゲート G4 の統合テスト（docs/TASK.md M4-09）。

検収内容: SynthWorld の観測 → WM の ``encode`` → **M2 のプローブ／昇格がそのまま動作**し、
整合残差が計測できる。合成潜在版と指標を比較する。

ゲート G4 の条件:

- G3 維持
- M4-09 緑
- **2 モデル族が同一 Protocol で差し替え可能**（レジストリ経由の切替テスト）

この里程の主張は「合成潜在を実モデルに差し替えても接地層の IF が変わらない」である。したがって
このテストで**プローブ側のコードを族ごとに分岐させてはならない**。分岐が必要になった時点で
主張は崩れている。

真値は ``sbp.testing`` 経由でのみ参照する（不変条件 10）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import psycopg
import pytest

from sbp.bridge import (
    LinearProbe,
    ProbeKind,
    ProbeMetrics,
    ProbeSpec,
    ProbeStatus,
    TemperatureScaler,
    alignment_residual,
    equipment_maintenance_transition,
    promote,
    symbolize,
)
from sbp.core.config import BridgeSettings
from sbp.core.time import now
from sbp.knowledge import LatentIndex, PlanStrategy, Selectivity, plan_hybrid
from sbp.testing import Fluent, SynthConfig, SynthWorld
from sbp.worldmodel import (
    CompatibilityEntry,
    JepaConfig,
    JepaTimeSeriesModel,
    KoopmanConfig,
    KoopmanModel,
    LatentLevel,
    ModelRegistry,
    ModelStatus,
    WorldModel,
)

if TYPE_CHECKING:
    from sbp.core.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

AUROC_TARGET = 0.85
ECE_TARGET = 0.05
PUMP = "https://sbp.example/site/siteA/asset/P-101"
DOMAIN = "https://sbp.example/ent#Pump"
KOOPMAN_ID = "https://sbp.example/model/koopman"
JEPA_ID = "https://sbp.example/model/jepa-ts"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=4),
            duration=timedelta(hours=4),
            cycles=4,
        ),
        seed=2026,
    )


@pytest.fixture(scope="module")
def cut(world: SynthWorld) -> int:
    return int(len(world.timestamps) * 0.6)


@pytest.fixture(scope="module")
def koopman(world: SynthWorld, cut: int) -> KoopmanModel:
    model = KoopmanModel.untrained(
        model_id=KOOPMAN_ID,
        version="1.0.0",
        domain=DOMAIN,
        channels=world.channels,
        config=KoopmanConfig(delays=8),
    )
    return model.fit(world.observations[:cut], seed=2026)


@pytest.fixture(scope="module")
def jepa(world: SynthWorld, cut: int) -> JepaTimeSeriesModel:
    model = JepaTimeSeriesModel.untrained(
        model_id=JEPA_ID,
        version="1.0.0",
        domain=DOMAIN,
        channels=world.channels,
        config=JepaConfig(window=16, latent_dim=16, hidden_dim=64),
    )
    return model.fit(world.observations[:cut], epochs=60, seed=2026)


def _ground(model: WorldModel, world: SynthWorld, cut: int) -> dict[str, float]:
    """族に依らない接地の手順（プローブ学習 → 較正 → 評価 → 整合残差）。

    **この関数が族ごとに分岐しないことが G4 の主張である。** 受け取るのは ``WorldModel``
    Protocol だけで、どの族かは見ていない。
    """
    latents = model.encode_series(world.observations)  # type: ignore[attr-defined]
    labels = world.oracle.labels_for(Fluent.RUNNING)
    probe = LinearProbe(dim=latents.shape[1], seed=2026)
    probe.fit(latents[:cut], labels[:cut], epochs=500)
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))
    holdout = probe.predict(latents[cut:], ensemble_noise=0.02)
    states = symbolize(
        {
            "Running": holdout.probabilities,
            "UnderMaintenance": np.zeros_like(holdout.probabilities),
        }
    )
    report = alignment_residual(states, equipment_maintenance_transition())
    metrics = probe.evaluate(latents[cut:], labels[cut:], alignment_residual=report.residual)
    health = model.health(
        world.observations[cut:],
        list(world.timestamps[cut:]),
        align_residual=report.residual,
    )
    return {
        "auroc": metrics.auroc,
        "ece": metrics.ece,
        "residual": report.residual,
        "latent_shift": health.latent_shift,
        "pred_error": health.pred_error,
        "dim": float(latents.shape[1]),
    }


@pytest.fixture(scope="module")
def koopman_scores(koopman: KoopmanModel, world: SynthWorld, cut: int) -> dict[str, float]:
    return _ground(koopman, world, cut)


@pytest.fixture(scope="module")
def jepa_scores(jepa: JepaTimeSeriesModel, world: SynthWorld, cut: int) -> dict[str, float]:
    return _ground(jepa, world, cut)


@pytest.fixture(scope="module")
def synthetic_scores(world: SynthWorld, cut: int) -> dict[str, float]:
    """合成潜在での基準（G2 の値）。実モデルの劣化を測る相手。"""
    latents = world.latents("mid")
    labels = world.oracle.labels_for(Fluent.RUNNING)
    probe = LinearProbe(dim=latents.shape[1], seed=2026)
    probe.fit(latents[:cut], labels[:cut], epochs=500)
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))
    holdout = probe.predict(latents[cut:], ensemble_noise=0.02)
    states = symbolize(
        {
            "Running": holdout.probabilities,
            "UnderMaintenance": np.zeros_like(holdout.probabilities),
        }
    )
    report = alignment_residual(states, equipment_maintenance_transition())
    metrics = probe.evaluate(latents[cut:], labels[cut:], alignment_residual=report.residual)
    return {"auroc": metrics.auroc, "ece": metrics.ece, "residual": report.residual}


class TestG4GroundingOnRealLatents:
    """接地層のコードを変えずに実モデルの潜在で動くか。"""

    @pytest.mark.parametrize("family", ["koopman", "jepa"])
    def test_the_probe_meets_the_phase1_targets(
        self,
        family: str,
        koopman_scores: dict[str, float],
        jepa_scores: dict[str, float],
    ) -> None:
        scores = koopman_scores if family == "koopman" else jepa_scores
        assert scores["auroc"] >= AUROC_TARGET, f"{family}: AUROC {scores['auroc']:.4f}"
        assert scores["ece"] <= ECE_TARGET, f"{family}: ECE {scores['ece']:.4f}"

    @pytest.mark.parametrize("family", ["koopman", "jepa"])
    def test_the_alignment_residual_is_measurable(
        self,
        family: str,
        koopman_scores: dict[str, float],
        jepa_scores: dict[str, float],
    ) -> None:
        """整合残差が計測できること（値の大小ではなく、計測が成立すること）。"""
        scores = koopman_scores if family == "koopman" else jepa_scores
        assert 0.0 <= scores["residual"] <= 1.0

    def test_real_latents_do_not_fall_far_behind_the_synthetic_baseline(
        self,
        koopman_scores: dict[str, float],
        jepa_scores: dict[str, float],
        synthetic_scores: dict[str, float],
    ) -> None:
        """G2 の基準からの劣化を測る。これが M4 の主眼である。"""
        for name, scores in (("koopman", koopman_scores), ("jepa", jepa_scores)):
            drop = synthetic_scores["auroc"] - scores["auroc"]
            assert drop <= 0.05, f"{name}: AUROC が基準から {drop:.4f} 落ちた"

    def test_real_latents_are_smaller_than_the_synthetic_one(
        self, koopman_scores: dict[str, float], jepa_scores: dict[str, float]
    ) -> None:
        """合成潜在は 64 次元。実モデルはそれより小さい潜在で同じ仕事をしている。"""
        assert koopman_scores["dim"] < 64
        assert jepa_scores["dim"] < 64

    @pytest.mark.parametrize("family", ["koopman", "jepa"])
    def test_health_is_reported_on_the_holdout(
        self,
        family: str,
        koopman_scores: dict[str, float],
        jepa_scores: dict[str, float],
    ) -> None:
        scores = koopman_scores if family == "koopman" else jepa_scores
        assert scores["latent_shift"] >= 0.0
        assert scores["pred_error"] >= 0.0


class TestG4FamilySwap:
    """2 モデル族が同一 Protocol で差し替え可能（ゲート G4 の条件 3）。"""

    def test_both_families_satisfy_the_protocol(
        self, koopman: KoopmanModel, jepa: JepaTimeSeriesModel
    ) -> None:
        assert isinstance(koopman, WorldModel)
        assert isinstance(jepa, WorldModel)

    def test_the_same_code_path_encodes_both(
        self, koopman: KoopmanModel, jepa: JepaTimeSeriesModel, world: SynthWorld
    ) -> None:
        """呼び出し側が族を見分けずに encode / step / rollout できる。"""
        for model in (koopman, jepa):
            state = model.encode(
                world.observations[:200], list(world.timestamps[:200]), level=LatentLevel.MID
            )
            advanced = model.step(state)
            trajectories = model.rollout(state, horizon=3, samples=2, seed=5)
            assert advanced.at > state.at
            assert len(trajectories) == 2
            assert state.wm_version.endswith("/1.0.0")

    def test_the_registry_switches_which_family_serves(
        self,
        koopman: KoopmanModel,
        jepa: JepaTimeSeriesModel,
        koopman_scores: dict[str, float],
        jepa_scores: dict[str, float],
    ) -> None:
        """レジストリ経由の切替。評価済みでなければ昇格できない。"""
        registry = ModelRegistry()
        for model, scores in ((koopman, koopman_scores), (jepa, jepa_scores)):
            entry = registry.register(model.meta)
            entry.record_compatibility(
                CompatibilityEntry(
                    probe_id="probe:running",
                    auroc=scores["auroc"],
                    ece=scores["ece"],
                    align_residual=scores["residual"],
                    evaluated_at=now().isoformat(),
                )
            )

        registry.promote(
            KOOPMAN_ID,
            "1.0.0",
            ModelStatus.CANARY,
            required_probes=["probe:running"],
            min_auroc=AUROC_TARGET,
            max_ece=ECE_TARGET,
            max_residual=0.2,
        )
        serving = registry.serving(DOMAIN)
        assert [entry.meta.model_id for entry in serving] == [KOOPMAN_ID]

        # 族を入れ替える: koopman を退役させ jepa を出す
        registry.promote(
            KOOPMAN_ID,
            "1.0.0",
            ModelStatus.DEPRECATED,
            min_auroc=0.0,
            max_ece=1.0,
            max_residual=1.0,
        )
        registry.promote(
            JEPA_ID,
            "1.0.0",
            ModelStatus.CANARY,
            required_probes=["probe:running"],
            min_auroc=AUROC_TARGET,
            max_ece=ECE_TARGET,
            max_residual=0.2,
        )
        assert [entry.meta.model_id for entry in registry.serving(DOMAIN)] == [JEPA_ID]
        # 退役した版も説明のためにロードできる
        assert registry.get(KOOPMAN_ID, "1.0.0").status is ModelStatus.DEPRECATED

    def test_probe_promotion_works_on_a_real_latent(self, koopman_scores: dict[str, float]) -> None:
        """M2 の昇格ゲート（AUROC ∧ ECE ∧ 整合残差）が実モデルの潜在で通る。"""
        settings = BridgeSettings()
        spec = ProbeSpec(
            probe_id="https://sbp.example/site/siteA/probe/running-koopman",
            grounds="https://sbp.example/ent#Running",
            kind=ProbeKind.CLASSIFIER,
            wm_version=f"{KOOPMAN_ID}/1.0.0",
            latent_level="mid",
            status=ProbeStatus.CANDIDATE,
            metrics=ProbeMetrics(
                auroc=koopman_scores["auroc"],
                ece=koopman_scores["ece"],
                alignment_residual=koopman_scores["residual"],
                n_samples=576,
            ),
            ontology_version="0.4.0",
        )
        promoted = promote(
            spec,
            settings=settings,
            # 既存 active の整合残差。koopman の残差がこの中央値以下であることが条件
            active_alignment_residuals=(0.05, 0.08),
        )
        assert promoted.status is ProbeStatus.ACTIVE


class TestG4LatentIndex:
    """潜在索引（M4-08）が実モデルの潜在を受け入れ、近傍検索が効く。"""

    @pytest.mark.asyncio
    async def test_latents_round_trip_through_the_index(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        latents = koopman.encode_series(world.observations)[:120]
        stamps = list(world.timestamps[:120])
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            index = LatentIndex(connection)
            registration = await index.register(
                wm_version=koopman.meta.version_iri, dim=koopman.latent_dim
            )
            assert registration.dim == koopman.latent_dim
            written = await index.write(
                entity=PUMP,
                wm_version=koopman.meta.version_iri,
                level="mid",
                timestamps=stamps,
                vectors=latents,
            )
            assert written == 120

            # 書き込んだベクトル自身が最近傍になる（距離 0）
            neighbours = await index.neighbours(
                query=latents[42],
                wm_version=koopman.meta.version_iri,
                level="mid",
                limit=3,
            )
            assert neighbours[0].at == stamps[42]
            assert neighbours[0].distance == pytest.approx(0.0, abs=1e-6)
            assert neighbours[0].similarity == pytest.approx(1.0, abs=1e-6)
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_a_dimension_mismatch_is_refused(
        self, settings: Settings, koopman: KoopmanModel, jepa: JepaTimeSeriesModel
    ) -> None:
        """潜在空間が変わったなら版も変わる。黙って別の表に入れない。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            index = LatentIndex(connection)
            await index.register(wm_version=koopman.meta.version_iri, dim=koopman.latent_dim)
            with pytest.raises(Exception, match="registered with dim"):
                await index.write(
                    entity=PUMP,
                    wm_version=koopman.meta.version_iri,
                    level="mid",
                    timestamps=[now()],
                    vectors=np.zeros((1, jepa.latent_dim)),
                )
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_the_level_scopes_the_search(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        """階層が違う潜在は同じ空間にあるように見えて意味が違う。混ぜない。"""
        latents = koopman.encode_series(world.observations)[:40]
        stamps = list(world.timestamps[:40])
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            index = LatentIndex(connection)
            await index.register(wm_version=koopman.meta.version_iri, dim=koopman.latent_dim)
            await index.write(
                entity=PUMP,
                wm_version=koopman.meta.version_iri,
                level="mid",
                timestamps=stamps,
                vectors=latents,
            )
            same_level = await index.neighbours(
                query=latents[0], wm_version=koopman.meta.version_iri, level="mid", limit=5
            )
            other_level = await index.neighbours(
                query=latents[0], wm_version=koopman.meta.version_iri, level="slow", limit=5
            )
            assert len(same_level) == 5
            assert other_level == ()
            await connection.rollback()


class TestG4HybridQuery:
    """ハイブリッドクエリ計画（M4-08）が記号側の選択率で順序を決める。"""

    def test_a_selective_graph_predicate_drives_the_search(self) -> None:
        plan = plan_hybrid(
            limit=5,
            selectivity=Selectivity(graph_candidates=2, total_entities=500, vector_rows=120),
            graph_candidates=[PUMP, "https://sbp.example/site/siteA/asset/P-102"],
        )
        assert plan.strategy is PlanStrategy.GRAPH_FIRST
        assert not plan.may_be_incomplete

    def test_an_unselective_predicate_warns_about_incompleteness(self) -> None:
        """空の結果を「該当なし」と誤報しないための旗。"""
        plan = plan_hybrid(
            limit=5,
            selectivity=Selectivity(graph_candidates=480, total_entities=500, vector_rows=120),
        )
        assert plan.strategy is PlanStrategy.VECTOR_FIRST
        assert plan.may_be_incomplete
