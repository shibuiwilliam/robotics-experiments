"""``make reground`` — 版の昇格のあと、過去の信念を新しい版で再計算する（M9-01、ADR-0031）。

**既定は dry-run**（差分レポートだけを出し、何も書かない）。``--apply`` で旧文の ``tx_to`` を閉じ、
新文を足す。旧文は消さない（不変条件 2）。手順は ``docs/runbooks/regrounding.md``。

- 新しい版のプローブは ``--probe-id``（版を含む IRI、複数可）で指す。``latest`` は使えない。
- 使う WM は ``SBP_BRIDGE_WM_VERSION``。WM 版を上げたときは、先に ``make encode`` で潜在を作る。
- 主張（業務記録）は事象ログ（業務トピック）を最初から読む。運用の consumer group には触れない。
- 終了コード: 0 = 完了（dry-run を含む）、1 = 差分が閾値を超えていて**人のレビューが要る**。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import psycopg
from services.belief_worker import default_ingest
from services.bridge_worker import load_probe_set, load_rules

from sbp.core.config import Settings, get_settings
from sbp.core.errors import ConfigError
from sbp.core.logging import configure_logging, get_logger
from sbp.core.metrics import MetricSample, metric
from sbp.core.time import Interval, ensure_utc
from sbp.knowledge import (
    AssertionStore,
    HttpGraphStore,
    LatentIndex,
    MetricStore,
    RegroundingTrigger,
    S3ArtifactStore,
    plan_regrounding,
)
from sbp.reasoning.regrounding_run import RegroundingOutcome, reground
from scripts.train_probe import _read_claims  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main", "run_reground"]

log = get_logger(__name__)

_MAX_LATENTS = 200_000
"""1 実体の潜在の読み出し上限。打ち切ったら落とす（途中までの再生は信念を壊す）。"""


async def run_reground(
    settings: Settings,
    *,
    entities: Sequence[str],
    fluents: Sequence[str],
    interval: Interval,
    probe_ids: Sequence[str],
    from_version: str,
    to_version: str,
    reason: str,
    apply: bool,
    idle_s: float = 3.0,
) -> RegroundingOutcome:
    """再接地を 1 回実行する。

    Args:
        settings: 設定。
        entities: 対象実体の IRI。
        fluents: 対象フルーエント型の IRI。
        interval: 対象期間（有限）。
        probe_ids: 新しい版のプローブの IRI。
        from_version: 変更前の版（レポート用）。
        to_version: 変更後の版（レポート用）。
        reason: 理由（レポートの判断材料）。
        apply: 真なら書く。
        idle_s: 主張を読み切ったとみなす無音の秒数。

    Returns:
        結果。

    Raises:
        ConfigError: 潜在が無い、または読み出しの上限に当たった場合。
    """
    plan = plan_regrounding(
        trigger=RegroundingTrigger.PROBE_VERSION,
        reason=reason,
        entities=entities,
        fluents=fluents,
        interval=interval,
        from_version=from_version,
        to_version=to_version,
    )
    probes = load_probe_set(S3ArtifactStore.from_settings(settings), probe_ids=probe_ids)
    claims = await _read_claims(settings, idle_s=idle_s)
    async with await psycopg.AsyncConnection.connect(
        settings.pg_dsn, autocommit=True
    ) as connection:
        index = LatentIndex(connection)
        series = {}
        for entity in entities:
            rows = await index.since(
                entity=entity,
                wm_version=settings.bridge.wm_version,
                level="mid",
                limit=_MAX_LATENTS,
            )
            if not rows:
                raise ConfigError(f"no latents for {entity} under {settings.bridge.wm_version}")
            if len(rows) >= _MAX_LATENTS:
                raise ConfigError(f"latents of {entity} exceed {_MAX_LATENTS}; split the plan")
            series[entity] = (
                [at for at, _v in rows],
                np.vstack([v for _a, v in rows]).astype(np.float64),
            )
        outcome = reground(
            plan,
            store=AssertionStore(HttpGraphStore(settings), site_id=settings.site_id),
            series_by_entity=series,
            probes=probes,
            probe_ids=probes.probe_ids(),
            wm_version=probes.wm_version,
            rules=load_rules(),
            settings=settings,
            claims=claims,
            ingest_factory=lambda: default_ingest(settings),
            apply=apply,
        )
        sample = MetricSample(
            metric("regrounding_duration"),
            outcome.report.duration.total_seconds(),
            outcome.report.finished_at,
            {"trigger": plan.trigger.value, "applied": str(apply).lower()},
        )
        try:
            await MetricStore(connection, site=settings.site_id).write([sample])
        except Exception:  # 指標の不調で再接地の結果を失わない（書き込みは済んでいる）
            log.exception("reground.metrics_failed")
    return outcome


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="新しい版で過去の信念を再計算する（既定は dry-run）"
    )
    parser.add_argument("--entity", action="append", required=True, help="対象実体の IRI（複数可）")
    parser.add_argument("--fluent", action="append", required=True, help="フルーエント型の IRI")
    parser.add_argument("--start", required=True, help="対象期間の始端（ISO 8601、UTC）")
    parser.add_argument("--end", required=True, help="対象期間の終端（右開。必須）")
    parser.add_argument("--probe-id", action="append", required=True, help="新しいプローブの IRI")
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--reason", required=True, help="なぜ再接地するか")
    parser.add_argument("--apply", action="store_true", help="書く（既定は dry-run）")
    parser.add_argument("--report", type=Path, default=None, help="レポートの保存先（markdown）")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    interval = Interval(
        ensure_utc(datetime.fromisoformat(args.start)), ensure_utc(datetime.fromisoformat(args.end))
    )
    outcome = asyncio.run(
        run_reground(
            settings,
            entities=args.entity,
            fluents=args.fluent,
            interval=interval,
            probe_ids=args.probe_id,
            from_version=args.from_version,
            to_version=args.to_version,
            reason=args.reason,
            apply=args.apply,
        )
    )
    markdown = outcome.report.markdown()
    if args.report is not None:
        args.report.write_text(markdown, encoding="utf-8")
    log.info(
        "reground.done",
        applied=outcome.applied,
        added=len(outcome.added),
        closed=len(outcome.closed),
        needs_review=outcome.report.needs_review,
    )
    return 1 if outcome.report.needs_review else 0


if __name__ == "__main__":
    raise SystemExit(main())
