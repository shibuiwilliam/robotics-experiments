"""What-if の実行（M10-03、ADR-0036）。

重点:

1. **介入付きで学習していない WM は拒否する** — 介入なしの rollout を返さない（効果が 0 の嘘）。
2. **基準と介入ありは同じ起点・同じシード** — 強さ 0 の介入は差が 0、強さを上げると差が出る。
3. **識別子は決定的** — 同じ問いは同じシナリオ。
4. **結果の文は ``hypothetical``** で、主信念には入らない（不変条件 3）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pytest

from sbp.core import vocab
from sbp.core.modality import Modality
from sbp.knowledge import AssertionStore, EmbeddedGraphStore
from sbp.reasoning.whatif_run import (
    WhatIfRefusedError,
    run_intervention_whatif,
    scenario_key,
    scenario_payload,
)
from sbp.testing import SynthConfig, SynthWorld
from sbp.worldmodel.base import LatentState
from sbp.worldmodel.families.koopman import KoopmanConfig, KoopmanModel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from numpy.typing import NDArray

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
ENTITY = "https://sbp.example/site/siteA/asset/P-101"
FLUENT = str(vocab.ent.Running)
ACTION = "https://sbp.example/ent#StopCmd"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(config=SynthConfig(start=T0, duration=timedelta(hours=2), cycles=3), seed=7)


def _fit(world: SynthWorld, *, with_actions: bool) -> KoopmanModel:
    rows = len(world.timestamps)
    rng = np.random.default_rng(0)
    actions = None
    values = world.observations.copy()
    if with_actions:
        actions = (rng.random((rows, 2)) < 0.15).astype(np.float64)
        values[:, 0] += 2.0 * actions[:, 0]  # チャンネル 0 の介入が観測 0 を押し上げる
    return KoopmanModel.untrained(
        model_id="https://sbp.example/wm/koopman",
        version="1.0.0",
        domain="d",
        channels=tuple(world.channels),
        config=KoopmanConfig(delays=6, rank=None),
    ).fit(values, actions=actions, seed=1)


def _state(model: KoopmanModel, world: SynthWorld) -> LatentState:
    state = model.encode(world.observations[:40], world.timestamps[:40])
    return LatentState(
        z=state.z,
        level=state.level,
        at=state.at,
        wm_version=state.wm_version,
        covariance=np.eye(state.z.size) * 0.01,
    )


def _probe(latents: NDArray[np.float64]) -> Mapping[str, NDArray[np.float64]]:
    """潜在の第 0 成分のロジスティック（決定的な代役。プローブは bridge の仕事）。"""
    return {"Running": 1.0 / (1.0 + np.exp(-latents[:, 0]))}


def _run(model: KoopmanModel, state: LatentState, **overrides: object) -> object:
    args: dict[str, object] = {
        "model": model,
        "probe": _probe,
        "site": "siteA",
        "entity": ENTITY,
        "fluent": FLUENT,
        "state": state,
        "action_type": ACTION,
        "channel": 0,
        "magnitude": 3.0,
        "horizon": 6,
        "samples": 4,
        "seed": 11,
        "probe_ids": ["p1"],
    }
    args.update(overrides)
    return run_intervention_whatif(**args)  # type: ignore[arg-type]


class TestRefusal:
    def test_a_model_fitted_without_actions_is_refused_with_the_reason(
        self, world: SynthWorld
    ) -> None:
        model = _fit(world, with_actions=False)
        assert model.action_dim is None
        with pytest.raises(WhatIfRefusedError, match="fitted without interventions"):
            _run(model, _state(model, world))

    def test_a_channel_outside_the_action_space_is_refused(self, world: SynthWorld) -> None:
        model = _fit(world, with_actions=True)
        with pytest.raises(WhatIfRefusedError, match="channel"):
            _run(model, _state(model, world), channel=5)

    def test_a_fluent_no_probe_grounds_is_refused(self, world: SynthWorld) -> None:
        model = _fit(world, with_actions=True)
        with pytest.raises(WhatIfRefusedError, match="no probe grounds"):
            _run(model, _state(model, world), fluent=str(vocab.ent.Faulted))


class TestBaselineAndIntervention:
    def test_a_zero_strength_intervention_changes_nothing(self, world: SynthWorld) -> None:
        model = _fit(world, with_actions=True)
        result = _run(model, _state(model, world), magnitude=0.0)
        assert max(abs(d) for d in result.delta) < 1e-9, "同じ起点・同じシードなら差は 0"  # type: ignore[attr-defined]

    def test_a_real_intervention_moves_the_curve_away_from_the_baseline(
        self, world: SynthWorld
    ) -> None:
        model = _fit(world, with_actions=True)
        result = _run(model, _state(model, world), magnitude=3.0)
        assert max(abs(d) for d in result.delta) > 1e-3  # type: ignore[attr-defined]
        assert len(result.baseline) == len(result.intervened) == 6  # type: ignore[attr-defined]
        assert all(0.0 <= p <= 1.0 for p in result.intervened)  # type: ignore[attr-defined]

    def test_the_same_question_has_the_same_scenario_id(self, world: SynthWorld) -> None:
        model = _fit(world, with_actions=True)
        state = _state(model, world)
        first = _run(model, state)
        second = _run(model, state)
        other = _run(model, state, magnitude=2.0)
        assert first.scenario.scenario_id == second.scenario.scenario_id  # type: ignore[attr-defined]
        assert first.scenario.scenario_id != other.scenario.scenario_id  # type: ignore[attr-defined]
        assert first.intervened == second.intervened  # type: ignore[attr-defined]

    def test_scenario_key_depends_on_every_part_of_the_question(self) -> None:
        base = {
            "site": "s",
            "entity": "e",
            "fluent": "f",
            "wm_version": "w",
            "origin_iso": "t",
            "action_type": "a",
            "channel": 0,
            "magnitude": 1.0,
            "horizon": 3,
            "seed": 1,
            "samples": 4,
            "probe_ids": ["p1"],
        }
        ids = {scenario_key(**base)}  # type: ignore[arg-type]
        changes = (
            ("channel", 1),
            ("magnitude", 2.0),
            ("horizon", 4),
            ("entity", "e2"),
            ("seed", 2),
            ("samples", 8),
            ("probe_ids", ["p1", "p2"]),
        )
        for key, value in changes:
            ids.add(scenario_key(**{**base, key: value}))  # type: ignore[arg-type]
        assert len(ids) == 8, "結果を変える入力はすべて識別子に効く"


class TestIsolation:
    def test_the_result_is_hypothetical_and_never_in_the_main_belief(
        self, world: SynthWorld
    ) -> None:
        model = _fit(world, with_actions=True)
        result = _run(model, _state(model, world))
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        scenario = result.scenario  # type: ignore[attr-defined]
        for assertion in scenario.to_assertions(
            fluent=FLUENT,
            probabilities=list(result.intervened),  # type: ignore[attr-defined]
            ontology_version=vocab.ONTOLOGY_VERSION,
        ):
            assert assertion.modality is Modality.HYPOTHETICAL
            store.assert_(assertion)
        assert store.query(subject=ENTITY, predicate=FLUENT) == [], "主信念には入らない"
        in_scenario = store.query(
            subject=ENTITY,
            predicate=FLUENT,
            modality=Modality.HYPOTHETICAL,
            scenario=scenario.graph_name,
        )
        assert len(in_scenario) == 6

    def test_the_payload_states_that_validity_is_not_measured(self, world: SynthWorld) -> None:
        model = _fit(world, with_actions=True)
        result = _run(model, _state(model, world))
        payload = scenario_payload(result, action_type=ACTION, channel=0)  # type: ignore[arg-type]
        assert payload["kind"] == "intervention"
        assert "因果の主張としては使わない" in payload["note"]
        assert len(payload["delta"]) == 6
