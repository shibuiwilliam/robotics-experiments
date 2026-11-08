"""エンコードジョブと WM 学習の部品（M8-24-4、ADR-0023）。

DB を要る部分（``encode_once``）は統合テスト（M8-24-5）。ここは設定の検査・系列の組み立て・
学習と置き場への保存を確かめる。重点: **既定値を置かない**（空の置き場や適当な版を黙って使わない）、
**窓をつなぐときに別の実体・別のチャンネルを混ぜない**、**既存の版には上書きしない**。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pytest
from scripts._wm import split_wm_version, spool_path
from scripts.train_koopman import longest_series, series_with_actions, train_from_spool

from sbp.core.config import BridgeSettings, Settings
from sbp.core.errors import ArtifactImmutableError, ConfigError
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.testing import SynthConfig, SynthWorld
from sbp.worldmodel.encoding import JsonlObservationSource, ObservationWindow
from sbp.worldmodel.serialization import load_model

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
WM = "https://sbp.example/wm/koopman/0.1.0"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(start=T0, duration=timedelta(hours=2), cycles=3), seed=2026
    )


def _write_spool(path: Path, world: SynthWorld, *, entity: str = PUMP, rows: int = 300) -> None:
    window = {
        "entity": entity,
        "channels": list(world.channels),
        "timestamps": [at.isoformat() for at in world.timestamps[:rows]],
        "values": world.observations[:rows].tolist(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(window) + "\n")


class TestSettingsChecks:
    def test_there_is_no_default_spool(self) -> None:
        with pytest.raises(ConfigError, match="no spool directory"):
            spool_path(Settings(_env_file=None))  # type: ignore[call-arg]

    def test_a_missing_spool_path_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="does not exist"):
            spool_path(Settings(_env_file=None), tmp_path / "nope")  # type: ignore[call-arg]

    def test_the_argument_wins_over_the_setting(self, tmp_path: Path) -> None:
        assert spool_path(Settings(_env_file=None), tmp_path) == tmp_path  # type: ignore[call-arg]

    @pytest.mark.parametrize("bad", ["", "no-slash", "/0.1.0", "https://x/"])
    def test_a_wm_version_without_model_and_version_is_refused(self, bad: str) -> None:
        with pytest.raises(ConfigError):
            split_wm_version(bad)

    def test_a_wm_version_is_split_into_model_and_version(self) -> None:
        assert split_wm_version(WM) == ("https://sbp.example/wm/koopman", "0.1.0")


class TestLongestSeries:
    def _window(self, entity: str, start: int, stop: int, world: SynthWorld) -> ObservationWindow:
        return ObservationWindow(
            entity=entity,
            channels=tuple(world.channels),
            timestamps=tuple(world.timestamps[start:stop]),
            values=world.observations[start:stop],
        )

    def test_overlapping_windows_are_joined_without_duplicates(self, world: SynthWorld) -> None:
        windows = [self._window(PUMP, 0, 50, world), self._window(PUMP, 40, 100, world)]
        _channels, stamps, values = longest_series(windows)
        assert len(stamps) == 100
        assert stamps == sorted(stamps)
        np.testing.assert_array_equal(values, world.observations[:100])

    def test_the_longest_entity_is_chosen_and_entities_are_not_mixed(
        self, world: SynthWorld
    ) -> None:
        windows = [self._window(PUMP, 0, 30, world), self._window(PUMP + "-2", 0, 90, world)]
        _channels, stamps, _values = longest_series(windows)
        assert len(stamps) == 90

    def test_channels_that_differ_within_an_entity_are_refused(self, world: SynthWorld) -> None:
        a = self._window(PUMP, 0, 30, world)
        b = ObservationWindow(
            entity=PUMP,
            channels=("other",) * len(world.channels),
            timestamps=a.timestamps,
            values=a.values,
        )
        with pytest.raises(ConfigError, match="different channels"):
            longest_series([a, b])

    def test_an_empty_spool_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="no readable window"):
            longest_series([])


class TestTrain:
    def _settings(self) -> Settings:
        return Settings(_env_file=None, bridge=BridgeSettings(wm_version=WM))  # type: ignore[call-arg]

    def test_it_trains_and_stores_weights_under_the_configured_version(
        self, tmp_path: Path, world: SynthWorld
    ) -> None:
        spool = tmp_path / "spool"
        spool.mkdir()
        _write_spool(spool / "a.jsonl", world)
        store = LocalArtifactStore(tmp_path / "store")
        model, key = train_from_spool(
            self._settings(), spool, store=store, domain="d", delays=6, rank=None, seed=1
        )
        assert key == "models/sbp.example_wm_koopman/0.1.0/koopman.npz"
        restored = load_model(store, model_id="https://sbp.example/wm/koopman", version="0.1.0")
        assert restored.meta.version_iri == model.meta.version_iri
        assert restored.is_trained

    def test_the_same_version_is_never_overwritten(self, tmp_path: Path, world: SynthWorld) -> None:
        spool = tmp_path / "spool"
        spool.mkdir()
        _write_spool(spool / "a.jsonl", world)
        store = LocalArtifactStore(tmp_path / "store")
        args = {"domain": "d", "delays": 6, "rank": None, "seed": 1}
        train_from_spool(self._settings(), spool, store=store, **args)  # type: ignore[arg-type]
        with pytest.raises(ArtifactImmutableError):
            train_from_spool(self._settings(), spool, store=store, **args)  # type: ignore[arg-type]

    def test_a_missing_wm_version_is_refused(self, tmp_path: Path, world: SynthWorld) -> None:
        spool = tmp_path / "spool"
        spool.mkdir()
        _write_spool(spool / "a.jsonl", world)
        with pytest.raises(ConfigError, match="SBP_BRIDGE_WM_VERSION"):
            train_from_spool(
                Settings(_env_file=None),  # type: ignore[call-arg]
                spool,
                store=LocalArtifactStore(tmp_path / "store"),
                domain="d",
                delays=6,
                rank=None,
                seed=1,
            )


class TestActions:
    """介入付きの学習（What-if の前提。ADR-0036）。"""

    def _window(self, world: SynthWorld, *, actions: bool, n: int = 80) -> ObservationWindow:
        steps = np.zeros((n, 2))
        steps[::7, 0] = 1.0  # 介入チャンネル 0 を時々印加
        return ObservationWindow(
            entity=PUMP,
            channels=tuple(world.channels),
            timestamps=tuple(world.timestamps[:n]),
            values=world.observations[:n],
            actions=steps if actions else None,
        )

    def test_actions_come_back_aligned_with_the_rows(self, world: SynthWorld) -> None:
        _c, stamps, values, actions = series_with_actions([self._window(world, actions=True)])
        assert actions is not None
        assert actions.shape == (len(stamps), 2) and values.shape[0] == len(stamps)

    def test_a_spool_without_actions_gives_none(self, world: SynthWorld) -> None:
        *_rest, actions = series_with_actions([self._window(world, actions=False)])
        assert actions is None

    def test_actions_on_only_some_rows_are_refused(self, world: SynthWorld) -> None:
        with_actions = self._window(world, actions=True, n=60)
        without = ObservationWindow(
            entity=PUMP,
            channels=tuple(world.channels),
            timestamps=tuple(world.timestamps[40:100]),
            values=world.observations[40:100],
        )
        with pytest.raises(ConfigError, match="only some rows"):
            series_with_actions([with_actions, without])

    def test_the_spool_reader_checks_the_action_shape(self, tmp_path: Path) -> None:
        base = {
            "entity": PUMP,
            "channels": ["a"],
            "timestamps": ["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"],
            "values": [[1.0], [2.0]],
        }
        good = json.dumps({**base, "actions": [[0.0], [1.0]]})
        bad = json.dumps({**base, "actions": [[0.0]]})
        (tmp_path / "a.jsonl").write_text(good + "\n" + bad + "\n", encoding="utf-8")
        source = JsonlObservationSource(tmp_path)
        windows = list(source.windows())
        assert len(windows) == 1
        assert windows[0].actions is not None and windows[0].actions.shape == (2, 1)
        assert len(source.rejected) == 1 and "actions shape" in source.rejected[0]

    def test_training_with_actions_gives_a_model_that_accepts_interventions(
        self, tmp_path: Path, world: SynthWorld
    ) -> None:
        spool = tmp_path / "spool"
        spool.mkdir()
        window = self._window(world, actions=True, n=len(world.timestamps))
        line = {
            "entity": window.entity,
            "channels": list(window.channels),
            "timestamps": [t.isoformat() for t in window.timestamps],
            "values": window.values.tolist(),
            "actions": None if window.actions is None else window.actions.tolist(),
        }
        (spool / "a.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
        settings = Settings(_env_file=None, bridge=BridgeSettings(wm_version=WM))  # type: ignore[call-arg]
        model, _key = train_from_spool(
            settings,
            spool,
            store=LocalArtifactStore(tmp_path / "store"),
            domain="d",
            delays=6,
            rank=None,
            seed=1,
        )
        assert model.action_dim == 2

    def test_a_model_trained_without_actions_has_no_action_dim(
        self, tmp_path: Path, world: SynthWorld
    ) -> None:
        spool = tmp_path / "spool"
        spool.mkdir()
        _write_spool(spool / "a.jsonl", world)
        settings = Settings(_env_file=None, bridge=BridgeSettings(wm_version=WM))  # type: ignore[call-arg]
        model, _key = train_from_spool(
            settings,
            spool,
            store=LocalArtifactStore(tmp_path / "store"),
            domain="d",
            delays=6,
            rank=None,
            seed=1,
        )
        assert model.action_dim is None
