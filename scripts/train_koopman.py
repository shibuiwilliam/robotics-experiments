"""``make train-wm`` — スプールの履歴窓から Koopman を学習して重みを置き場に置く（M8-24）。

推定パスの先頭（観測 → 潜在）で使う WM は、学習したプロセスの外で動かすために置き場へ出す
（:mod:`sbp.worldmodel.serialization`）。**既存の版には上書きしない**——同じ版を名乗る別の重みが
入り込むと、過去の信念の説明が静かに変わる（版を上げる）。

- WM 版は ``SBP_BRIDGE_WM_VERSION``（``<モデル IRI>/<版>``）。
- 学習は 1 つの連続した系列に対して行う。スプールの窓を実体ごとにつなぎ（重なりは時刻で重複排除）、
  **最も長い実体の系列**を使う（窓をまたいで別の実体をつなぐと、つなぎ目が偽の遷移になる）。
- 真値を読まない（不変条件 10）。ラベルを使わない自己教師の同定である。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from sbp.core import vocab
from sbp.core.config import Settings, get_settings
from sbp.core.errors import ConfigError
from sbp.core.logging import configure_logging, get_logger
from sbp.knowledge import S3ArtifactStore
from sbp.worldmodel import KoopmanConfig, KoopmanModel
from sbp.worldmodel.encoding import JsonlObservationSource
from sbp.worldmodel.serialization import save_model
from scripts._wm import split_wm_version, spool_path

if TYPE_CHECKING:
    from collections.abc import Iterable

    from numpy.typing import NDArray

    from sbp.knowledge.artifacts import ArtifactStore
    from sbp.worldmodel.encoding import ObservationWindow

__all__ = ["longest_series", "main", "train_from_spool"]

log = get_logger(__name__)


def longest_series(
    windows: Iterable[ObservationWindow],
) -> tuple[tuple[str, ...], list[datetime], NDArray[np.float64]]:
    """窓を実体ごとにつなぎ、最も長い連続系列を返す（重なりは時刻で重複排除）。

    Args:
        windows: スプールの窓。

    Returns:
        ``(チャンネル, 時刻, 値)``。

    Raises:
        ConfigError: 窓が無い、またはチャンネルが実体の窓の間で食い違う場合。
    """
    per_entity: dict[str, tuple[tuple[str, ...], dict[datetime, NDArray[np.float64]]]] = {}
    for window in windows:
        channels, rows = per_entity.setdefault(window.entity, (window.channels, {}))
        if window.channels != channels:
            raise ConfigError(
                f"entity {window.entity} has windows with different channels "
                f"({list(channels)} vs {list(window.channels)}); refusing to join them"
            )
        for at, row in zip(window.timestamps, window.values, strict=True):
            rows[at] = row
    if not per_entity:
        raise ConfigError("the spool has no readable window; nothing to learn from")
    entity = max(per_entity, key=lambda name: len(per_entity[name][1]))
    channels, rows = per_entity[entity]
    stamps = sorted(rows)
    log.info("train_wm.series_chosen", entity=entity, rows=len(stamps), entities=len(per_entity))
    return channels, stamps, np.vstack([rows[at] for at in stamps])


def train_from_spool(
    settings: Settings,
    spool: Path,
    *,
    store: ArtifactStore,
    domain: str,
    delays: int,
    rank: int | None,
    seed: int,
) -> tuple[KoopmanModel, str]:
    """スプールから学習して置き場に置く。

    Args:
        settings: 設定（``SBP_BRIDGE_WM_VERSION`` が要る）。
        spool: スプールの置き場。
        store: 重みの置き場。
        domain: 対象ドメインの IRI。
        delays: 時間遅れ埋め込みの段数。
        rank: 作用素の切り詰めランク（``None`` なら切り詰めない）。
        seed: 乱数シード（契約として受け取る）。

    Returns:
        ``(学習済みのモデル, 置いた鍵)``。

    Raises:
        ConfigError: WM 版が読めない、窓が無い場合。
        ArtifactImmutableError: その版が既に置かれている場合。
    """
    model_id, version = split_wm_version(settings.bridge.wm_version)
    source = JsonlObservationSource(spool)
    channels, _stamps, values = longest_series(source.windows())
    model = KoopmanModel.untrained(
        model_id=model_id,
        version=version,
        domain=domain,
        channels=channels,
        config=KoopmanConfig(delays=delays, rank=rank),
    ).fit(values, seed=seed)
    key = save_model(store, model)
    return model, key


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description="スプールから Koopman を学習して置き場に置く")
    parser.add_argument("--spool", type=Path, default=None, help="スプールの置き場（既定は設定）")
    parser.add_argument("--domain", default=str(vocab.core.Asset), help="対象ドメインの IRI")
    parser.add_argument("--delays", type=int, default=8, help="時間遅れ埋め込みの段数")
    parser.add_argument("--rank", type=int, default=None, help="作用素の切り詰めランク")
    parser.add_argument("--seed", type=int, required=True, help="乱数シード（必須）")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    model, key = train_from_spool(
        settings,
        spool_path(settings, args.spool),
        store=S3ArtifactStore.from_settings(settings),
        domain=args.domain,
        delays=args.delays,
        rank=args.rank,
        seed=args.seed,
    )
    log.info("train_wm.done", wm_version=model.meta.version_iri, key=key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
