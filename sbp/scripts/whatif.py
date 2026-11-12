"""``make whatif`` — 介入の下の rollout を、介入なしと並べて返す（M10-03、ADR-0036）。

実体の最新の潜在を起点に、アクション型・チャンネル・強さで介入した rollout と、介入なしの rollout
（同じシード）をプローブに通し、成立確率の曲線の差を返す。結果の文は ``hypothetical`` でシナリオ用
グラフに書き（主信念に入れない。不変条件 3）、曲線は ``scenarios`` 表に書く。

- **介入付きで学習した WM にだけ実行する。** そうでなければ理由を示して拒否する（終了コード 2）。
- 同じ問い（実体・フルーエント・WM 版・起点・介入）は 2 度書かない。
- WM は ``SBP_BRIDGE_WM_VERSION``、プローブは ``--probe-id``。乱数シードは必須。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import psycopg
from services.bridge_worker import load_probe_set

from sbp.core import vocab
from sbp.core.config import Settings, get_settings
from sbp.core.errors import ConfigError
from sbp.core.logging import configure_logging, get_logger
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.knowledge import (
    AssertionStore,
    HttpGraphStore,
    LatentIndex,
    S3ArtifactStore,
    ScenarioStore,
)
from sbp.reasoning.whatif_run import (
    WhatIfRefusedError,
    WhatIfResult,
    run_intervention_whatif,
    scenario_payload,
)
from sbp.worldmodel.base import LatentLevel, LatentState
from sbp.worldmodel.serialization import load_model
from scripts._wm import split_wm_version

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.bridge.runner import ProbeSet
    from sbp.worldmodel.families.koopman import KoopmanModel

__all__ = ["main", "whatif_once"]

log = get_logger(__name__)

_LEVEL = LatentLevel.MID


async def whatif_once(
    settings: Settings,
    *,
    entity: str,
    fluent: str,
    action_type: str,
    channel: int,
    magnitude: float,
    horizon: int,
    model: KoopmanModel,
    probes: ProbeSet,
    seed: int,
    probe_ids: Sequence[str],
    connection: psycopg.AsyncConnection[tuple[object, ...]],
    store: AssertionStore,
) -> WhatIfResult | None:
    """What-if を 1 件実行して書く。

    Args:
        settings: 設定。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        action_type: 介入するアクション型 IRI。
        channel: 立てる介入チャンネル（スプールの ``actions`` の列番号）。
        magnitude: 介入の強さ。
        horizon: 介入を効かせる刻み数。
        model: 学習済みの WM。
        probes: プローブ。
        seed: 乱数シード。
        probe_ids: 使ったプローブの IRI（シナリオの識別子の元）。
        connection: Postgres 接続。
        store: アサーションストア。

    Returns:
        結果（実体の潜在が無い・古い場合は ``None``）。

    Raises:
        WhatIfRefusedError: WM が介入付きで学習されていない、チャンネルが範囲外の場合。
    """
    reasoning = settings.reasoning
    latest = await LatentIndex(connection).latest(
        entity=entity, wm_version=settings.bridge.wm_version, level=_LEVEL.value
    )
    if latest is None:
        log.warning("whatif.no_latent", entity=entity)
        return None
    at, vector = latest
    if now() - at > timedelta(seconds=reasoning.forecast_max_origin_age_s):
        log.warning("whatif.origin_too_old", entity=entity, origin=at.isoformat())
        return None
    state = LatentState(
        z=vector,
        level=_LEVEL,
        at=at,
        wm_version=model.meta.version_iri,
        covariance=np.eye(vector.size) * reasoning.forecast_state_std**2,
    )
    result = run_intervention_whatif(
        model=model,
        probe=probes,
        site=settings.site_id,
        entity=entity,
        fluent=fluent,
        state=state,
        action_type=action_type,
        channel=channel,
        magnitude=magnitude,
        horizon=horizon,
        samples=reasoning.forecast_samples,
        seed=(seed + int(at.timestamp())) % 2**31,
        probe_ids=probe_ids,
    )
    scenario = result.scenario
    # 結果の文を先に、索引を後に（索引が書けなければ再実行で補完される。文は欠けた点だけ足す）
    existing = {
        row.valid_from
        for row in store.query(
            subject=entity,
            predicate=fluent,
            modality=Modality.HYPOTHETICAL,
            scenario=scenario.graph_name,
        )
    }
    for assertion in scenario.to_assertions(
        fluent=fluent,
        probabilities=list(result.intervened),
        ontology_version=vocab.ONTOLOGY_VERSION,
    ):
        if assertion.valid_from not in existing:
            store.assert_(assertion)
    await ScenarioStore(connection, site=settings.site_id).save(
        scenario_id=scenario.scenario_id,
        entity=entity,
        fluent=fluent,
        wm_version=scenario.wm_version,
        origin=scenario.origin,
        action_type=action_type,
        payload=scenario_payload(result, action_type=action_type, channel=channel),
    )
    return result


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    model_id, version = split_wm_version(settings.bridge.wm_version)
    artifacts = S3ArtifactStore.from_settings(settings)
    model = load_model(artifacts, model_id=model_id, version=version)
    probes = load_probe_set(artifacts, probe_ids=args.probe_id)
    if probes.wm_version != settings.bridge.wm_version:
        raise ConfigError(
            f"the probes belong to {probes.wm_version} but SBP_BRIDGE_WM_VERSION is "
            f"{settings.bridge.wm_version}; a what-if would read the latents in the wrong space"
        )
    store = AssertionStore(HttpGraphStore(settings), site_id=settings.site_id)
    horizon = args.horizon or settings.reasoning.forecast_horizon_steps
    async with await psycopg.AsyncConnection.connect(
        settings.pg_dsn, autocommit=True
    ) as connection:
        try:
            result = await whatif_once(
                settings,
                entity=args.entity,
                fluent=args.fluent,
                action_type=args.action_type,
                channel=args.channel,
                magnitude=args.magnitude,
                horizon=horizon,
                model=model,
                probes=probes,
                seed=args.seed,
                probe_ids=args.probe_id,
                connection=connection,
                store=store,
            )
        except WhatIfRefusedError as exc:
            log.error("whatif.refused", reason=str(exc))
            return 2
    if result is None:
        return 1
    log.info(
        "whatif.done",
        scenario=str(result.scenario.scenario_id),
        max_abs_delta=max(abs(d) for d in result.delta),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。終了コード: 0 成功 / 1 潜在が無い・古い / 2 拒否 / 3 設定の誤り。"""
    parser = argparse.ArgumentParser(description="介入の下の rollout を介入なしと並べて返す")
    parser.add_argument("--entity", required=True, help="対象実体の IRI")
    parser.add_argument("--fluent", required=True, help="フルーエント型の IRI")
    parser.add_argument("--probe-id", action="append", required=True, help="プローブの IRI")
    parser.add_argument("--action-type", required=True, help="介入するアクション型の IRI")
    parser.add_argument("--channel", type=int, default=0, help="介入チャンネル（actions の列番号）")
    parser.add_argument("--magnitude", type=float, default=1.0, help="介入の強さ")
    parser.add_argument("--horizon", type=int, default=0, help="介入を効かせる刻み数（既定は設定）")
    parser.add_argument("--seed", type=int, required=True, help="乱数シード（必須）")
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    try:
        return asyncio.run(_run(args))
    except ConfigError as exc:
        log.error("whatif.config_error", reason=str(exc))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
