"""What-if の実行（M10-03、ADR-0036）。介入の下の rollout を、介入なしの rollout と並べて返す。

**入れるのは介入（``INTERVENTION``）だけ**、**介入付きで学習した WM にだけ**実行する。
介入なしの rollout を What-if として返す経路は作らない（介入の効果が常に 0 の嘘になる）。
計画差分は条件付けが学習されていないため、反実仮想は ``ActionEvent`` が永続化されていない
（H-07）ため、入口を作らない。

基準と介入ありは**同じ起点・同じ乱数シード**で回す。差は介入による。
結果の文は ``hypothetical`` でシナリオ用グラフに書く（主信念に入れない。不変条件 3）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import numpy as np

from sbp.bridge.runner import fluent_name
from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.reasoning.whatif import (
    Scenario,
    ScenarioKind,
    encode_intervention,
    run_scenario,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from numpy.typing import NDArray

    from sbp.worldmodel.base import LatentState, Trajectory

__all__ = [
    "WhatIfRefusedError",
    "WhatIfResult",
    "run_intervention_whatif",
    "scenario_key",
    "scenario_payload",
]

log = get_logger(__name__)


class WhatIfRefusedError(ReasoningError):
    """What-if を実行できない（理由を人が読める形で持つ）。"""


class InterventionModel(Protocol):
    """介入付きで rollout できる WM（``KoopmanModel`` が満たす）。"""

    @property
    def action_dim(self) -> int | None:
        """介入の次元（介入付きで学習していなければ ``None``）。"""
        ...

    def rollout(
        self,
        state: LatentState,
        *,
        horizon: int,
        samples: int = 1,
        actions: Sequence[NDArray[np.float64]] | None = None,
        context: NDArray[np.float64] | None = None,
        seed: int,
    ) -> tuple[Trajectory, ...]:
        """地平 ``horizon`` の軌道を ``samples`` 本返す。"""
        ...


@dataclass(frozen=True, slots=True)
class WhatIfResult:
    """What-if 1 件の結果。

    Attributes:
        scenario: シナリオ（``hypothetical`` のアサーションの元。識別子は決定的）。
        lead_times_s: 各刻みの起点からの先行時間（秒）。
        baseline: 介入なしの成立確率（刻みごと）。
        intervened: 介入ありの成立確率（刻みごと）。
    """

    scenario: Scenario
    lead_times_s: tuple[float, ...]
    baseline: tuple[float, ...]
    intervened: tuple[float, ...]

    @property
    def delta(self) -> tuple[float, ...]:
        """介入による変化（介入あり − 基準）。"""
        return tuple(i - b for i, b in zip(self.intervened, self.baseline, strict=True))


def scenario_key(
    *,
    site: str,
    entity: str,
    fluent: str,
    wm_version: str,
    origin_iso: str,
    action_type: str,
    channel: int,
    magnitude: float,
    horizon: int,
) -> UUID:
    """シナリオの識別子（同じ問いは同じ値。再実行で二重に積まない）。

    Args:
        site: 拠点識別子。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        wm_version: WM 版 IRI。
        origin_iso: 起点の時刻（ISO 8601）。
        action_type: アクション型 IRI。
        channel: 立てた介入チャンネル。
        magnitude: 介入の強さ。
        horizon: 介入を効かせた刻み数。
    """
    key = (
        f"whatif|{site}|{entity}|{fluent}|{wm_version}|{origin_iso}|"
        f"{action_type}|{channel}|{magnitude}|{horizon}"
    )
    return uuid5(NAMESPACE_URL, key)


def _curve(
    trajectories: Sequence[Trajectory],
    probe: Callable[[NDArray[np.float64]], Mapping[str, NDArray[np.float64]]],
    name: str,
    steps: int,
) -> tuple[float, ...]:
    """各刻みの成立確率（軌道をまたいだ平均）。"""
    curve: list[float] = []
    for index in range(steps):
        latents = np.vstack([t.states[index].z for t in trajectories])
        outputs = probe(latents)
        if name not in outputs:
            raise WhatIfRefusedError(
                f"no probe grounds {name!r}; a what-if of it would be made of nothing"
            )
        curve.append(float(np.clip(np.asarray(outputs[name], dtype=np.float64), 0.0, 1.0).mean()))
    return tuple(curve)


def run_intervention_whatif(
    *,
    model: InterventionModel,
    probe: Callable[[NDArray[np.float64]], Mapping[str, NDArray[np.float64]]],
    site: str,
    entity: str,
    fluent: str,
    state: LatentState,
    action_type: str,
    channel: int,
    magnitude: float,
    horizon: int,
    samples: int,
    seed: int,
) -> WhatIfResult:
    """介入の下の rollout と、介入なしの rollout を並べる。

    Args:
        model: 介入付きで学習した WM。
        probe: 潜在の行列 → フルーエント別の較正済み確率（``ProbeSet``）。
        site: 拠点識別子（識別子の元）。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        state: 起点の潜在（不確実性を持つこと）。
        action_type: 介入するアクション型 IRI。
        channel: 立てる介入チャンネル（スプールの ``actions`` の列番号）。
        magnitude: 介入の強さ。
        horizon: 介入を効かせる刻み数。
        samples: 軌道の本数。
        seed: 乱数シード（基準と介入ありで共通）。

    Returns:
        結果。

    Raises:
        WhatIfRefusedError: WM が介入付きで学習されていない、またはチャンネルが範囲外の場合。
    """
    action_dim = model.action_dim
    if action_dim is None:
        raise WhatIfRefusedError(
            "this world model was fitted without interventions, so it cannot answer a what-if "
            "(its effect would be zero by construction). Put an `actions` column in the spool "
            "and retrain (ADR-0036)"
        )
    try:
        encoding = encode_intervention(
            action_type,
            action_dim=action_dim,
            horizon=horizon,
            magnitude=magnitude,
            channel=channel,
        )
    except ReasoningError as exc:
        raise WhatIfRefusedError(str(exc)) from exc

    scenario = run_scenario(
        model,  # type: ignore[arg-type] — Protocol を満たす（rollout の引数が同じ）
        state,
        kind=ScenarioKind.INTERVENTION,
        entity=entity,
        intervention=encoding,
        samples=samples,
        seed=seed,
    )
    baseline_runs = model.rollout(state, horizon=encoding.horizon, samples=samples, seed=seed)
    name = fluent_name(fluent)
    steps = len(scenario.trajectories[0].states)
    scenario = replace(
        scenario,
        scenario_id=scenario_key(
            site=site,
            entity=entity,
            fluent=fluent,
            wm_version=state.wm_version,
            origin_iso=state.at.isoformat(),
            action_type=action_type,
            channel=channel,
            magnitude=magnitude,
            horizon=horizon,
        ),
    )
    result = WhatIfResult(
        scenario=scenario,
        lead_times_s=tuple(
            (s.at - state.at).total_seconds() for s in scenario.trajectories[0].states
        ),
        baseline=_curve(baseline_runs, probe, name, steps),
        intervened=_curve(scenario.trajectories, probe, name, steps),
    )
    log.info(
        "reasoning.whatif_run",
        scenario=str(scenario.scenario_id),
        entity=entity,
        fluent=fluent,
        steps=steps,
        max_abs_delta=max(abs(d) for d in result.delta),
    )
    return result


def scenario_payload(result: WhatIfResult, *, action_type: str, channel: int) -> dict[str, Any]:
    """索引表に入れる JSON（曲線と介入と、読む人への注意）。

    Args:
        result: 結果。
        action_type: アクション型 IRI。
        channel: 介入チャンネル。
    """
    intervention = result.scenario.intervention
    return {
        "kind": result.scenario.kind.value,
        "action_type": action_type,
        "channel": channel,
        "magnitude": float(intervention.vector[channel]) if intervention is not None else 0.0,
        "horizon": 0 if intervention is None else intervention.horizon,
        "lead_times_s": list(result.lead_times_s),
        "baseline": list(result.baseline),
        "intervened": list(result.intervened),
        "delta": list(result.delta),
        "graph": result.scenario.graph_name,
        "note": (
            "介入の効果は、介入付きで学習した WM の線形な制御項から出る。"
            "妥当性（介入を観測した件数）は数えていない——因果の主張としては使わない（ADR-0036）"
        ),
    }
