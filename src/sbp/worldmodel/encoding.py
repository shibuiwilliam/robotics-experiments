"""観測窓の潜在化と潜在索引への書き込み（M8-24、ADR-0023）。

推定パスの先頭（設計書 §2.5）: 観測窓 → ``WorldModel`` の潜在 → ``LatentIndex``。現場のエッジが
**スプール**（JSONL）に置いた窓を読み、学習済みの重みで潜在にして索引に書く。bridge-worker が
その索引を読む。

**観測値は永続化しない**（不変条件 9）。書くのは潜在だけで、スプールは現場の置き場——ここは
読み取り専用で、削除も書き換えもしない。

**窓は重ねて置く。** 時間遅れ埋め込みは直前の ``delays - 1`` 行を要するので、次の窓がその行を
含まないと、窓の頭の ``delays - 1`` 時刻は潜在を持てない（前詰めで偽の潜在を作らない。
:meth:`~sbp.worldmodel.families.koopman.KoopmanModel.embed_series`）。

**実体ごとのウォーターマーク**（処理済みの時刻）で、書いた時刻より後の行だけを書く。潜在索引への書き込みは
上書きなので壊れはしないが、同じ窓を毎周期エンコードし直す無駄を省く。**書いた後に進める**（所見 52:
進めてから書くと、書けなかった時刻を二度と書かない）。

1 つの不正な窓（チャンネルの食い違い・時刻の逆行・非有限）で全体を止めない。理由つきで数え、
ログに出す（黙って捨てない）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np

from sbp.core.errors import WorldModelError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from numpy.typing import NDArray

    from sbp.worldmodel.base import ModelHealth
    from sbp.worldmodel.families.koopman import KoopmanModel

__all__ = [
    "EncodeReport",
    "EncodeRunner",
    "JsonlObservationSource",
    "ObservationSource",
    "ObservationWindow",
]

log = get_logger(__name__)

_EPOCH = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """1 実体の観測窓。

    Attributes:
        entity: 実体 IRI。
        channels: チャンネル名（モデルの入力順と一致すること）。
        timestamps: 各行の時刻（timezone-aware UTC、厳密に増加）。
        values: 観測値（``(時刻, チャンネル)``、有限）。
    """

    entity: str
    channels: tuple[str, ...]
    timestamps: tuple[datetime, ...]
    values: NDArray[np.float64]


class ObservationSource(Protocol):
    """観測窓の入口。現場のエッジが置く窓を、種類によらず同じ形で渡す。"""

    def windows(self) -> Iterable[ObservationWindow]:
        """いま読める観測窓を返す。"""
        ...

    @property
    def rejected(self) -> Sequence[str]:
        """読めなかった窓の理由（直近の :meth:`windows` で）。"""
        ...


class JsonlObservationSource:
    """スプール（JSONL）から窓を読む参照実装。

    1 行 = 1 窓: ``{"entity": IRI, "channels": [...], "timestamps": [ISO8601...],
    "values": [[...], ...]}``。ファイル 1 つ、またはディレクトリ（``*.jsonl`` を名前順）を渡す。
    **読み取り専用**——現場のエッジが書く場所を変更しない。
    """

    def __init__(self, path: Path) -> None:
        """Args:
        path: JSONL のファイル、または ``*.jsonl`` を含むディレクトリ。
        """
        self._path = path
        self._rejected: list[str] = []

    @property
    def rejected(self) -> Sequence[str]:
        """読めなかった行の理由（``<ファイル>:<行番号>: <理由>``）。"""
        return tuple(self._rejected)

    def windows(self) -> Iterable[ObservationWindow]:
        """窓を読む（不正な行は理由を残して飛ばす）。"""
        self._rejected = []
        files = sorted(self._path.glob("*.jsonl")) if self._path.is_dir() else [self._path]
        found: list[ObservationWindow] = []
        for file in files:
            with file.open(encoding="utf-8") as handle:
                for number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        found.append(_parse_window(line))
                    except (ValueError, KeyError, TypeError, WorldModelError) as exc:
                        reason = f"{file.name}:{number}: {exc}"
                        self._rejected.append(reason)
                        log.warning("encoding.window_rejected", reason=reason)
        return found


def _parse_window(line: str) -> ObservationWindow:
    """1 行を窓にする。不正なら ``ValueError`` / ``WorldModelError``。"""
    raw = json.loads(line)
    stamps = tuple(ensure_utc(datetime.fromisoformat(str(text))) for text in raw["timestamps"])
    values = np.asarray(raw["values"], dtype=np.float64)
    channels = tuple(str(name) for name in raw["channels"])
    if values.ndim != 2 or values.shape != (len(stamps), len(channels)):
        raise ValueError(
            f"values shape {values.shape} does not match {len(stamps)} timestamps × "
            f"{len(channels)} channels"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("values contain non-finite numbers")
    if any(later <= earlier for earlier, later in pairwise(stamps)):
        raise ValueError("timestamps are not strictly increasing")
    entity = str(raw["entity"])
    if not entity:
        raise ValueError("entity is empty")
    return ObservationWindow(entity=entity, channels=channels, timestamps=stamps, values=values)


class LatentSink(Protocol):
    """潜在索引への書き込み（``LatentIndex`` が満たす）。"""

    async def register(self, *, wm_version: str, dim: int) -> object:
        """WM 版を次元に対応する表へ登録する。"""
        ...

    async def write(
        self,
        *,
        entity: str,
        wm_version: str,
        level: str,
        timestamps: Sequence[datetime],
        vectors: NDArray[np.float64],
    ) -> int:
        """潜在を書く。"""
        ...


class WatermarkLedger(Protocol):
    """ウォーターマーク（``WatermarkStore`` が満たす）。"""

    async def load(self) -> dict[str, datetime]:
        """実体 → 処理済みの時刻。"""
        ...

    async def save(self, entity: str, processed_through: datetime) -> None:
        """1 実体ぶんを書く（後退させない）。"""
        ...


@dataclass(slots=True)
class EncodeReport:
    """エンコード 1 回の結果。**内訳を持つ**——0 件の理由（全部処理済み・窓が短い・不正）を区別する。

    Attributes:
        windows: 読んだ窓の数。
        rows_written: 潜在索引に書いた行数。
        skipped_processed: ウォーターマーク以前で書かなかった窓（全行が処理済み）。
        skipped_short: ``delays`` 行に満たず潜在を作れなかった窓。
        skipped_mismatch: チャンネルがモデルと食い違う窓。
        rejected: 入口が読めなかった行の数。
        latency_s: 窓ごとの推論時間（秒。指標 ``inference_latency`` の元）。
        health: 直近の（最も長い）窓での健全性（``latent_shift`` / ``pred_error``）。
    """

    windows: int = 0
    rows_written: int = 0
    skipped_processed: int = 0
    skipped_short: int = 0
    skipped_mismatch: int = 0
    rejected: int = 0
    latency_s: list[float] = field(default_factory=list)
    health: ModelHealth | None = None


class EncodeRunner:
    """窓を潜在にして索引に書く。

    Attributes:
        model: 学習済みの Koopman モデル。
    """

    def __init__(
        self,
        model: KoopmanModel,
        *,
        index: LatentSink,
        watermarks: WatermarkLedger,
        level: str = "mid",
    ) -> None:
        """Args:
        model: 学習済みのモデル（未学習は拒否する）。
        index: 潜在索引。
        watermarks: ウォーターマーク（worker 名・版・階層は呼び出し側で束ねる）。
        level: 潜在の階層。

        Raises:
            WorldModelError: 未学習の場合。
        """
        if not model.is_trained:
            raise WorldModelError("cannot encode with an untrained world model")
        self.model = model
        self._index = index
        self._watermarks = watermarks
        self._level = level

    def _health(self, window: ObservationWindow) -> ModelHealth | None:
        """健全性を測る。測れなくても書き込みは止めない（警告に残す）。"""
        try:
            return self.model.health(window.values, list(window.timestamps))
        except WorldModelError as exc:
            log.warning("encoding.health_failed", entity=window.entity, error=str(exc))
            return None

    async def run(self, source: ObservationSource) -> EncodeReport:
        """入口の窓をすべて処理する。

        Args:
            source: 観測窓の入口。

        Returns:
            内訳つきの結果。
        """
        wm_version = self.model.meta.version_iri
        await self._index.register(wm_version=wm_version, dim=self.model.latent_dim)
        processed = await self._watermarks.load()
        delays = self.model.config.delays
        report = EncodeReport()
        longest = 0
        # 同じ実体の窓が前後して届いても取りこぼさない: 始端の順に処理する（ウォーターマークは単調）
        ordered = sorted(
            source.windows(), key=lambda w: w.timestamps[0] if w.timestamps else _EPOCH
        )
        for window in ordered:
            report.windows += 1
            if window.channels != self.model.channels:
                report.skipped_mismatch += 1
                log.warning(
                    "encoding.channel_mismatch",
                    entity=window.entity,
                    expected=list(self.model.channels),
                    got=list(window.channels),
                )
                continue
            if len(window.timestamps) < delays:
                report.skipped_short += 1
                continue
            started = time.perf_counter()
            vectors = self.model.embed_series(window.values)
            report.latency_s.append(time.perf_counter() - started)
            stamps = window.timestamps[delays - 1 :]
            if len(window.timestamps) > longest and len(window.timestamps) > delays:
                longest = len(window.timestamps)
                report.health = self._health(window)
            mark = processed.get(window.entity)
            keep = [i for i, at in enumerate(stamps) if mark is None or at > mark]
            if not keep:
                report.skipped_processed += 1
                continue
            written = await self._index.write(
                entity=window.entity,
                wm_version=wm_version,
                level=self._level,
                timestamps=[stamps[i] for i in keep],
                vectors=vectors[keep],
            )
            # 書いた後に進める（所見 52）。書けなかった時刻を二度と書かなくならないように
            await self._watermarks.save(window.entity, stamps[keep[-1]])
            processed[window.entity] = stamps[keep[-1]]
            report.rows_written += written
        report.rejected = len(source.rejected)
        log.info(
            "encoding.run",
            wm_version=wm_version,
            windows=report.windows,
            rows_written=report.rows_written,
            skipped_processed=report.skipped_processed,
            skipped_short=report.skipped_short,
            skipped_mismatch=report.skipped_mismatch,
            rejected=report.rejected,
        )
        return report
