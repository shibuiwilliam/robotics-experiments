"""プローブ学習ジョブの組み立て（M8-23、ADR-0024）。Kafka・DB を使わない部分。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from scripts.train_probe import (
    _active_residuals,  # pyright: ignore[reportPrivateUsage]
    train_and_store,
)

from sbp.bridge.serialization import artifact_key, loads
from sbp.bridge.training import EntitySeries
from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.config import BridgeSettings, Settings
from sbp.core.errors import ArtifactImmutableError, ConfigError
from sbp.core.events import EventEnvelope, Producer, RecordEvidence, ValidTime
from sbp.core.ids import stable_event_id
from sbp.core.modality import Modality
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.testing import SynthConfig, SynthWorld
from sbp.testing.synth import Fluent
from sbp.worldmodel.families.koopman import KoopmanConfig, KoopmanModel

if TYPE_CHECKING:
    pass

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
FLUENT = "https://sbp.example/ent#Running"
PROBE = "https://sbp.example/probe/Running/1.0.0"
WM = "https://sbp.example/wm/koopman/0.1.0"


@pytest.fixture(scope="module")
def inputs() -> tuple[list[EventEnvelope], EntitySeries]:
    world = SynthWorld(config=SynthConfig(start=T0, duration=timedelta(hours=8), cycles=8), seed=31)
    model = KoopmanModel.untrained(
        model_id="https://sbp.example/wm/koopman",
        version="0.1.0",
        domain="d",
        channels=world.channels,
        config=KoopmanConfig(delays=8),
    ).fit(world.observations, seed=31)
    labels = world.oracle.labels_for(Fluent.RUNNING)
    events = [
        EventEnvelope(
            event_id=stable_event_id(world.timestamps[i], f"claim|{i}"),
            type=f"{vocab.core.BusinessEvent}/{FLUENT}",
            subject=PUMP,
            valid_time=ValidTime(start=world.timestamps[i], uncertainty_s=0.0),
            tx_time=world.timestamps[i],
            modality=Modality.OBSERVED,
            belief=BooleanBelief(p=0.9 if labels[i] else 0.1),
            evidence=(
                RecordEvidence(record="https://sbp.example/r", source="https://sbp.example/s"),
            ),
            producer=Producer(agent="t", version="0"),
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        for i in range(8, len(world.timestamps), 20)
    ]
    series = EntitySeries(PUMP, world.timestamps[7:], model.embed_series(world.observations))
    return events, series


def _settings(**bridge: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
        bridge=BridgeSettings(wm_version=WM, weak_label_min_half_width_s=60.0, **bridge),  # type: ignore[arg-type]
    )


def test_a_promoted_bundle_is_stored_under_its_versioned_key(
    tmp_path: Path,
    inputs: tuple[list[EventEnvelope], EntitySeries],
) -> None:
    events, series = inputs
    store = LocalArtifactStore(tmp_path)
    outcome, samples = train_and_store(
        _settings(), events, [series], store=store, fluent=FLUENT, probe_id=PROBE, seed=1
    )
    assert outcome.bundle is not None
    assert loads(store.get(artifact_key(outcome.spec))).spec.probe_id == PROBE
    assert {s.name.key for s in samples} == {"probe_auroc", "probe_ece", "align_residual"}


def test_a_probe_that_fails_the_gate_stores_nothing_but_still_records_metrics(
    tmp_path: Path,
    inputs: tuple[list[EventEnvelope], EntitySeries],
) -> None:
    events, series = inputs
    store = LocalArtifactStore(tmp_path)
    outcome, samples = train_and_store(
        _settings(promote_max_ece=0.0),
        events,
        [series],
        store=store,
        fluent=FLUENT,
        probe_id=PROBE,
        seed=1,
    )
    assert outcome.bundle is None
    assert not list(tmp_path.rglob("*.json")), "ゲートを通らないものを置き場に入れない"
    assert len(samples) == 3, "落ちた理由が見えるよう、指標は残す"


def test_the_same_version_is_never_overwritten(
    tmp_path: Path,
    inputs: tuple[list[EventEnvelope], EntitySeries],
) -> None:
    events, series = inputs
    store = LocalArtifactStore(tmp_path)
    args = {"store": store, "fluent": FLUENT, "probe_id": PROBE, "seed": 1}
    train_and_store(_settings(), events, [series], **args)  # type: ignore[arg-type]
    with pytest.raises(ArtifactImmutableError):
        train_and_store(_settings(), events, [series], **args)  # type: ignore[arg-type]


def test_a_missing_wm_version_is_refused(
    tmp_path: Path,
    inputs: tuple[list[EventEnvelope], EntitySeries],
) -> None:
    events, series = inputs
    with pytest.raises(ConfigError, match="SBP_BRIDGE_WM_VERSION"):
        train_and_store(
            Settings(_env_file=None),  # type: ignore[call-arg]
            events,
            [series],
            store=LocalArtifactStore(tmp_path),
            fluent=FLUENT,
            probe_id=PROBE,
            seed=1,
        )


def test_the_active_probes_residuals_are_the_promotion_baseline(
    tmp_path: Path, inputs: tuple[list[EventEnvelope], EntitySeries]
) -> None:
    """比較対象は運用中の active の整合残差。無視すると AND 条件が空洞になる。"""
    events, series = inputs
    store = LocalArtifactStore(tmp_path)
    first, _ = train_and_store(
        _settings(), events, [series], store=store, fluent=FLUENT, probe_id=PROBE, seed=1
    )
    assert first.bundle is not None and first.metrics is not None
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
        bridge=BridgeSettings(wm_version=WM, weak_label_min_half_width_s=60.0, probe_ids=(PROBE,)),
    )
    assert _active_residuals(settings, store) == (first.metrics.alignment_residual,)
