"""WM 重みの直列化（Koopman。M8-24-2、ADR-0023）。

重点:

1. **往復で挙動が変わらない** — 復元したモデルは同じ潜在・同じ健全性指標を返す。
2. **信用できない入力として読む** — pickle・改ざん・形の食い違い・版違い・有限でない値を拒否する。
3. **版が鍵に出る・上書きしない・``latest`` 禁止**（設計書 §4.8）。
4. **未学習を保存させない**。
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pytest

from sbp.core.errors import ArtifactImmutableError, WorldModelError
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.testing import SynthConfig, SynthWorld
from sbp.worldmodel import KoopmanConfig, KoopmanModel
from sbp.worldmodel.serialization import (
    STATE_SCHEMA_VERSION,
    artifact_key,
    dumps,
    load_model,
    loads,
    save_model,
)

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
MODEL_ID = "https://sbp.example/wm/koopman"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(start=T0, duration=timedelta(hours=2), cycles=3), seed=2026
    )


@pytest.fixture(scope="module")
def trained(world: SynthWorld) -> KoopmanModel:
    model = KoopmanModel.untrained(
        model_id=MODEL_ID,
        version="0.1.0",
        domain="https://sbp.example/ent#Pump",
        channels=world.channels,
        config=KoopmanConfig(delays=6),
    )
    return model.fit(world.observations, seed=2026)


def _rewrite(data: bytes, mutate: dict[str, object]) -> bytes:
    """npz を読み、配列やメタを差し替えて書き直す（改ざんの再現）。"""
    archive = np.load(io.BytesIO(data), allow_pickle=False)
    arrays = {name: archive[name] for name in archive.files}
    for name, value in mutate.items():
        if name == "__meta__":
            meta = json.loads(bytes(arrays["__meta__"]).decode("utf-8"))
            meta.update(value)  # type: ignore[arg-type]
            arrays["__meta__"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
        else:
            arrays[name] = value  # type: ignore[assignment]
    buffer = io.BytesIO()
    np.savez(buffer, allow_pickle=True, **arrays)
    return buffer.getvalue()


class TestRoundTrip:
    def test_the_restored_model_encodes_identically(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        restored = loads(dumps(trained))
        window = world.observations[:40]
        stamps = list(world.timestamps[:40])
        original = trained.encode(window, stamps)
        again = restored.encode(window, stamps)
        np.testing.assert_array_equal(original.z, again.z)
        assert original.wm_version == again.wm_version

    def test_the_restored_model_reports_the_same_health(
        self, trained: KoopmanModel, world: SynthWorld
    ) -> None:
        restored = loads(dumps(trained))
        args = (world.observations[:60], list(world.timestamps[:60]))
        before = trained.health(*args)
        after = restored.health(*args)
        assert after.latent_shift == pytest.approx(before.latent_shift)
        assert after.pred_error == pytest.approx(before.pred_error)

    def test_meta_and_config_survive(self, trained: KoopmanModel) -> None:
        restored = loads(dumps(trained))
        assert restored.meta.model_id == trained.meta.model_id
        assert restored.meta.version == "0.1.0"
        assert restored.channels == trained.channels
        assert restored.config == trained.config
        assert restored.latent_dim == trained.latent_dim

    def test_a_model_trained_with_actions_keeps_its_control_term(self, world: SynthWorld) -> None:
        model = KoopmanModel.untrained(
            model_id=MODEL_ID,
            version="0.2.0",
            domain="d",
            channels=world.channels,
            config=KoopmanConfig(delays=4),
        )
        actions = np.random.default_rng(0).normal(size=(world.observations.shape[0], 2))
        model.fit(world.observations, actions=actions, seed=1)
        restored = loads(dumps(model))
        assert "control" in restored.export_state()


class TestUntrustedInput:
    def test_an_untrained_model_is_not_saved(self, world: SynthWorld) -> None:
        untrained = KoopmanModel.untrained(
            model_id=MODEL_ID, version="0.1.0", domain="d", channels=world.channels
        )
        with pytest.raises(WorldModelError):
            dumps(untrained)

    def test_pickled_arrays_are_refused(self, trained: KoopmanModel) -> None:
        """読み込みで任意コードが動かないよう、pickle を含む配列は読まない。"""
        tampered = _rewrite(dumps(trained), {"operator": np.array([object()], dtype=object)})
        with pytest.raises(WorldModelError, match="cannot read"):
            loads(tampered)

    def test_garbage_is_refused(self) -> None:
        with pytest.raises(WorldModelError):
            loads(b"not an npz")

    def test_a_missing_meta_is_refused(self, trained: KoopmanModel) -> None:
        archive = np.load(io.BytesIO(dumps(trained)), allow_pickle=False)
        arrays = {name: archive[name] for name in archive.files if name != "__meta__"}
        buffer = io.BytesIO()
        np.savez(buffer, **arrays)
        with pytest.raises(WorldModelError, match="no metadata"):
            loads(buffer.getvalue())

    def test_an_unknown_format_major_version_is_refused(self, trained: KoopmanModel) -> None:
        tampered = _rewrite(dumps(trained), {"__meta__": {"schema_version": "2.0.0"}})
        with pytest.raises(WorldModelError, match="format"):
            loads(tampered)

    def test_a_compatible_minor_version_is_read(self, trained: KoopmanModel) -> None:
        minor = STATE_SCHEMA_VERSION.split(".")[0] + ".9.0"
        loads(_rewrite(dumps(trained), {"__meta__": {"schema_version": minor}}))

    def test_an_operator_of_the_wrong_shape_is_refused(self, trained: KoopmanModel) -> None:
        tampered = _rewrite(dumps(trained), {"operator": np.zeros((3, 3))})
        with pytest.raises(WorldModelError, match="operator"):
            loads(tampered)

    def test_a_changed_channel_count_is_refused(self, trained: KoopmanModel) -> None:
        tampered = _rewrite(dumps(trained), {"__meta__": {"channels": ["only_one"]}})
        with pytest.raises(WorldModelError):
            loads(tampered)

    def test_non_finite_values_are_refused(self, trained: KoopmanModel) -> None:
        operator = trained.export_state()["operator"].copy()
        operator[0, 0] = np.nan
        with pytest.raises(WorldModelError, match="non-finite"):
            loads(_rewrite(dumps(trained), {"operator": operator}))

    def test_a_latest_version_inside_the_meta_is_refused(self, trained: KoopmanModel) -> None:
        with pytest.raises(WorldModelError, match="latest"):
            loads(_rewrite(dumps(trained), {"__meta__": {"version": "latest"}}))


class TestStore:
    def test_the_key_carries_the_version(self) -> None:
        key = artifact_key(MODEL_ID, "0.1.0")
        assert key == "models/sbp.example_wm_koopman/0.1.0/koopman.npz"

    def test_latest_has_no_key(self) -> None:
        with pytest.raises(WorldModelError, match="latest"):
            artifact_key(MODEL_ID, "latest")

    def test_save_and_load_round_trip_through_a_store(
        self, trained: KoopmanModel, tmp_path: Path
    ) -> None:
        store = LocalArtifactStore(tmp_path)
        save_model(store, trained)
        restored = load_model(store, model_id=MODEL_ID, version="0.1.0")
        assert restored.meta.version_iri == trained.meta.version_iri

    def test_the_same_version_is_not_overwritten(
        self, trained: KoopmanModel, tmp_path: Path
    ) -> None:
        store = LocalArtifactStore(tmp_path)
        save_model(store, trained)
        with pytest.raises(ArtifactImmutableError):
            save_model(store, trained)

    def test_weights_filed_under_another_version_are_refused(
        self, trained: KoopmanModel, tmp_path: Path
    ) -> None:
        """別の版の重みを別の鍵に置いても、中身との食い違いで落ちる（置き間違いを使わない）。"""
        store = LocalArtifactStore(tmp_path)
        store.put(artifact_key(MODEL_ID, "9.9.9"), dumps(trained))
        with pytest.raises(WorldModelError, match="belong to"):
            load_model(store, model_id=MODEL_ID, version="9.9.9")
