"""ゲート G2 の統合テスト（docs/TASK.md M2-10）。

検収内容: SynthWorld の合成潜在 → プローブ → 昇格 → イベントログ → 知識グラフまで通し、
既知真値に対する AUROC / ECE を計測する。

ゲート G2 の条件:

- G1 維持
- M2-10 緑
- 合成データ上で昇格ゲート（AUROC ≥ 0.85 ∧ ECE ≤ 0.05 ∧ 整合残差 ≤ 中央値）を通る
  プローブが 1 概念以上存在する

これは M2 の主張「潜在から記号への橋が架かり、較正された確信度で信念になる」を実機で確かめる。
真値は ``sbp.testing`` の中でのみ参照し（不変条件 10）、プローブ自身は真値を見ない。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest

from sbp.bridge import (
    LinearProbe,
    ProbeKind,
    ProbeSpec,
    ProbeStatus,
    TemperatureScaler,
    alignment_residual,
    equipment_maintenance_transition,
    lift_events,
    load_event_calculus,
    promote,
    symbolize,
)
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import BridgeSettings
from sbp.core.events import EventLayer, ProbeEvidence, topic_name
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.eventlog import (
    ConsumerPolicy,
    InMemoryIdempotencyStore,
    KafkaEventProducer,
    process_once,
)
from sbp.knowledge import HttpGraphStore, schema_graph
from sbp.testing import LATENT_DIM, Fluent, SynthConfig, SynthWorld

if TYPE_CHECKING:
    from sbp.bridge import EventCalculusRules, ProbeMetrics
    from sbp.core.config import Settings
    from sbp.core.events import EventEnvelope
    from sbp.knowledge import AssertionStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REPO_ROOT = Path(__file__).resolve().parents[2]
PUMP = "https://sbp.example/site/siteA/asset/P-101"
WM_VERSION = "https://sbp.example/site/siteA/wm/synth-1.0"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    """4 サイクルの合成世界（分割しても両クラスが入り、変化点が複数ある）。"""
    return SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=4),
            duration=timedelta(hours=4),
            cycles=4,
        ),
        seed=2026,
    )


@pytest.fixture(scope="module")
def rules() -> EventCalculusRules:
    return load_event_calculus(
        (REPO_ROOT / "ontology" / "process" / "equipment-maintenance.lp").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="module")
def grounded(world: SynthWorld) -> dict[str, object]:
    """Running プローブを学習・較正・評価した結果をまとめて返す。

    真値の参照はこの fixture の中だけ（``sbp.testing`` 経由）。以降のテストは
    プローブの出力と指標だけを見る。
    """
    latents = world.latents("mid")
    labels = world.oracle.labels_for(Fluent.RUNNING)
    cut = int(len(labels) * 0.6)

    probe = LinearProbe(dim=LATENT_DIM, seed=2026)
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
    return {
        "probe": probe,
        "metrics": metrics,
        "alignment": report,
        "probabilities_all": probe.predict(latents).probabilities,
        "cut": cut,
    }


class TestG2GroundingQuality:
    """接地の品質が設計書 §13.1 の Phase 1 目標を満たすか。"""

    def test_auroc_meets_the_phase1_target(self, grounded: dict[str, object]) -> None:
        """設計書 §13.1: 上位 20 概念の AUROC ≥ 0.85。"""
        metrics: ProbeMetrics = grounded["metrics"]  # type: ignore[assignment]
        assert metrics.auroc >= 0.85, f"AUROC が Phase 1 目標未達: {metrics.auroc:.4f}"

    def test_ece_meets_the_phase1_target(self, grounded: dict[str, object]) -> None:
        """設計書 §13.1: ECE ≤ 0.05。"""
        metrics: ProbeMetrics = grounded["metrics"]  # type: ignore[assignment]
        assert metrics.ece <= 0.05, f"ECE が Phase 1 目標未達: {metrics.ece:.4f}"

    def test_alignment_residual_is_small(self, grounded: dict[str, object]) -> None:
        """整合残差が小さい（記号遷移が許す系列になっている）。"""
        metrics: ProbeMetrics = grounded["metrics"]  # type: ignore[assignment]
        assert metrics.alignment_residual < 0.1, (
            f"整合残差が大きい: {metrics.alignment_residual:.4f}"
        )

    def test_at_least_one_probe_passes_the_promotion_gate(
        self, grounded: dict[str, object]
    ) -> None:
        """**ゲート G2 の核心条件**: 昇格ゲートを通るプローブが 1 つ以上ある。"""
        metrics: ProbeMetrics = grounded["metrics"]  # type: ignore[assignment]
        spec = ProbeSpec(
            probe_id="https://sbp.example/site/siteA/probe/running-g2",
            grounds=vocab.ent.Running,
            kind=ProbeKind.CLASSIFIER,
            wm_version=WM_VERSION,
            metrics=metrics,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        promoted = promote(spec, settings=BridgeSettings(), active_alignment_residuals=())
        assert promoted.status is ProbeStatus.ACTIVE
        assert promoted.linear, "線形プローブで足りている（潜在の品質が保たれている）"


class TestG2EventLiftingReachesTheEventLog:
    """昇格した事象が実ブローカーを通る。"""

    async def test_lifted_event_round_trips(
        self,
        settings: Settings,
        kafka_available: None,
        world: SynthWorld,
        rules: EventCalculusRules,
        grounded: dict[str, object],
    ) -> None:
        """合成潜在から昇格した事象がイベントログを往復する。"""
        del kafka_available
        probabilities: np.ndarray = grounded["probabilities_all"]  # type: ignore[assignment]
        events = lift_events(
            subject=PUMP,
            probe_outputs={"Running": probabilities},
            timestamps=world.timestamps,
            rules=rules,
            settings=BridgeSettings(),
            probe_ids={"Running": "https://sbp.example/site/siteA/probe/running-g2"},
            wm_version=WM_VERSION,
            alignment_residual=0.02,
        )
        assert events, "事象が 1 つも昇格していない"

        envelope = events[0].to_envelope(
            site_id=settings.site_id,
            event_type_iri=vocab.ent.StopCmd,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        topic = topic_name(settings.site_id, EventLayer.BRIDGE, "physical")
        async with KafkaEventProducer(settings) as producer:
            await producer.send(topic, envelope)
            await producer.flush()

        seen: list[EventEnvelope] = []

        async def handler(event: EventEnvelope) -> None:
            seen.append(event)

        assert await process_once(
            envelope,
            topic=topic,
            handler=handler,
            store=InMemoryIdempotencyStore(),
            policy=ConsumerPolicy(consumer_group=f"g2-{envelope.event_id.hex[:8]}"),
        )
        assert len(seen) == 1
        assert seen[0].modality is Modality.INFERRED, "プローブ由来は inferred（不変条件 3）"
        assert seen[0].valid_time.uncertainty_s > 0.0, "点時刻のイベントになっていない"
        # 証拠はプローブ由来であり、生スコアを持つ（不変条件 7）
        evidence = seen[0].evidence[0]
        assert isinstance(evidence, ProbeEvidence)
        assert evidence.raw_score is not None, "生スコアが証拠に残っている"


class TestG2BeliefsLandInTheGraph:
    """接地した信念が知識グラフに載る（較正済みの確信度で）。"""

    def test_probe_output_becomes_a_calibrated_belief(
        self, assertion_store: AssertionStore, world: SynthWorld, grounded: dict[str, object]
    ) -> None:
        """プローブの較正済み出力が inferred アサーションとして載る。"""
        probabilities: np.ndarray = grounded["probabilities_all"]  # type: ignore[assignment]
        index = len(probabilities) // 2
        confidence = float(probabilities[index])

        assertion_store.assert_(
            Assertion(
                subject=PUMP,
                predicate=vocab.ent.Running,
                object=True,
                valid_from=world.timestamps[index],
                tx_from=now(),
                modality=Modality.INFERRED,
                belief=BooleanBelief(p=confidence),
                provenance="https://sbp.example/site/siteA/prov/g2-probe-run",
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )
        rows = assertion_store.query(subject=PUMP, modality=Modality.INFERRED)
        assert len(rows) == 1
        assert rows[0].confidence == pytest.approx(confidence, abs=1e-6)
        assert ":inferred:" in rows[0].graph

    def test_three_sources_coexist_for_the_same_fluent(
        self, assertion_store: AssertionStore, world: SynthWorld, grounded: dict[str, object]
    ) -> None:
        """**三源が同じフルーエントについて別々の様相で共存する。**

        これが M2 到達点の意味である。設計書 §14 の「計画は稼働、記録は稼働、観測は停止」
        という状況が、同じ実体・同じ述語について 3 つの様相として並ぶ。統合（事後の計算）は
        M3-02 の仕事であり、ここでは**並ぶところまで**を確認する。
        """
        probabilities: np.ndarray = grounded["probabilities_all"]  # type: ignore[assignment]
        # 真値で停止している時点を選ぶ（プローブは低い確率を出すはず）
        index = int(np.argmin(probabilities))
        at = world.timestamps[index]
        tx = now()

        for modality, confidence, provenance in (
            (Modality.PLANNED, 0.9, "g2-plan"),
            (Modality.OBSERVED, 0.95, "g2-record"),
            (Modality.INFERRED, float(probabilities[index]), "g2-probe"),
        ):
            assertion_store.assert_(
                Assertion(
                    subject=PUMP,
                    predicate=vocab.ent.Running,
                    object=True,
                    valid_from=at,
                    tx_from=tx,
                    modality=modality,
                    belief=BooleanBelief(p=confidence),
                    provenance=f"https://sbp.example/site/siteA/prov/{provenance}",
                    ontology_version=vocab.ONTOLOGY_VERSION,
                )
            )

        rows = assertion_store.query(subject=PUMP, predicate=vocab.ent.Running)
        by_modality = {r.modality: r for r in rows}
        assert set(by_modality) == {Modality.PLANNED, Modality.OBSERVED, Modality.INFERRED}
        assert len({r.graph for r in rows}) == 3, "様相ごとに別のグラフに入っている"

        observation = by_modality[Modality.INFERRED].confidence
        record = by_modality[Modality.OBSERVED].confidence
        assert observation < record, (
            "設計書 §14 の状況（記録は稼働と言うが観測は停止を示す）が再現されていない: "
            f"observation={observation:.3f} record={record:.3f}"
        )


class TestG2StackReadiness:
    def test_ontology_version_is_current_in_the_store(self, graph_store: object) -> None:
        """コードと知識グラフのオントロジー版が一致している。"""

        assert isinstance(graph_store, HttpGraphStore)
        rows = graph_store.query(
            "SELECT (COUNT(*) AS ?n) WHERE { GRAPH "
            f"<{schema_graph(vocab.ONTOLOGY_VERSION)}> {{ ?s ?p ?o }} }}"
        )
        assert int(rows[0]["n"] or "0") > 800, (
            f"オントロジー {vocab.ONTOLOGY_VERSION} が未投入。`make seed` を実行する"
        )

    def test_synth_world_is_deterministic(self) -> None:
        """同じ seed なら同じ世界（検証の再現性の前提）。"""
        config = SynthConfig(
            start=now().replace(microsecond=0), duration=timedelta(hours=1), cycles=2
        )
        a = SynthWorld(config=config, seed=99)
        b = SynthWorld(config=config, seed=99)
        assert np.array_equal(a.observations, b.observations)
        assert np.array_equal(a.latents("mid"), b.latents("mid"))
