"""active なプローブの束を走らせる（M8-02）。

接地ワーカーが潜在の行列を渡すと、**フルーエントのローカル名 → 較正済み確率**を返す。
:mod:`sbp.bridge.serialization` が置き場から戻したプローブをここで束ね、
:func:`sbp.bridge.lifting.lift_events` の入力の形にそろえる。

**``active`` だけを走らせる。** ``candidate`` はシャドウ評価の対象で、その出力は主信念に
入らない（設計書 §5.2 の昇格ゲート）。束に混ぜて走らせると、呼び出し側は身分を見ずに
結果を使う。

**束は 1 つの潜在空間に縛られる。** 構築時に ``wm_version`` と次元の一致を要求する。
違う WM 版のプローブを 1 つの束に入れると、同じ潜在行列を 2 つの空間として解釈することに
なる——次元が偶然一致していれば動いてしまい、出力は無意味になる（設計書 §4.8 の
互換性行列が扱う問題で、「次元が同じだから動く」は最も危険な思い込みである）。

**走るプローブが 0 本なら構築で落とす。** 空の束は「不一致 0 件」と同じ姿をしていて、
接地が止まっていることと区別できない（``docs/PROGRESS.md`` 所見 32）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from sbp.bridge.probes import ProbeStatus
from sbp.core.errors import BridgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

    from sbp.bridge.probes import LinearProbe, ProbeSpec

__all__ = ["ProbeSet", "fluent_name"]

log = get_logger(__name__)


def fluent_name(grounds: str) -> str:
    """概念 IRI からフルーエントのローカル名を取る。

    ``https://.../core#Running`` → ``Running``。CLAUDE.md の命名規約により、ローカル名は
    オントロジーの用語名と一致している（``lift_events`` 側で snake_case に直される）。

    Args:
        grounds: プローブが接地する概念の IRI。

    Returns:
        ローカル名。

    Raises:
        BridgeError: ローカル名を取り出せない場合。
    """
    local = grounds.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if not local:
        raise BridgeError(f"cannot derive a fluent local name from {grounds!r}")
    return local


class ProbeSet:
    """1 つの潜在空間に対する ``active`` なプローブの束。

    Attributes:
        wm_version: この束が対応する WM 版の IRI。
        latent_dim: 期待する潜在次元。
    """

    def __init__(
        self,
        entries: Sequence[tuple[LinearProbe, ProbeSpec]],
        *,
        site: str | None = None,
        operating_mode: str | None = None,
        ensemble_noise: float = 0.0,
    ) -> None:
        """Args:
        entries: ``(プローブ, 登録メタデータ)`` の列。``active`` 以外と、この拠点・運転モードに
            当てはまらないものは除かれる。
        site: 拠点識別子（``ProbeSpec.validity_sites`` で絞る）。
        operating_mode: 運転モード（``ProbeSpec.operating_modes`` で絞る）。
        ensemble_noise: 認識的不確実性の見積りに使う重みの摂動幅。

        Raises:
            BridgeError: 走るプローブが 0 本の場合、``wm_version`` または次元が混ざって
                いる場合、同じフルーエントに対する ``active`` が 2 本ある場合。
        """
        selected: list[tuple[LinearProbe, ProbeSpec]] = []
        skipped_inactive = 0
        skipped_scope = 0
        for probe, spec in entries:
            if spec.status is not ProbeStatus.ACTIVE:
                skipped_inactive += 1
                continue
            if not spec.applies_to(site=site, operating_mode=operating_mode):
                skipped_scope += 1
                continue
            selected.append((probe, spec))

        if not selected:
            raise BridgeError(
                f"no active probe applies here (site={site!r}, mode={operating_mode!r}); "
                f"{skipped_inactive} were not active and {skipped_scope} were out of scope. "
                "An empty probe set looks exactly like 'nothing happened' downstream, so "
                "refusing to build it rather than grounding nothing silently."
            )

        versions = {spec.wm_version for _probe, spec in selected}
        if len(versions) > 1:
            raise BridgeError(
                f"a probe set must belong to one latent space, got wm_versions {sorted(versions)}; "
                "mixing them interprets the same latent matrix in two spaces (design §4.8)"
            )
        dims = {probe.dim for probe, _spec in selected}
        if len(dims) > 1:
            raise BridgeError(
                f"probes in one set must share the latent dimension, got {sorted(dims)}"
            )

        by_fluent: dict[str, tuple[LinearProbe, ProbeSpec]] = {}
        for probe, spec in selected:
            name = fluent_name(spec.grounds)
            if name in by_fluent:
                raise BridgeError(
                    f"two active probes ground {name!r} ({by_fluent[name][1].probe_id} and "
                    f"{spec.probe_id}); which one speaks for the fluent would be arbitrary"
                )
            by_fluent[name] = (probe, spec)

        self._by_fluent = by_fluent
        self._ensemble_noise = ensemble_noise
        self.wm_version = next(iter(versions))
        self.latent_dim = next(iter(dims))
        log.info(
            "bridge.probe_set_built",
            fluents=sorted(by_fluent),
            wm_version=self.wm_version,
            latent_dim=self.latent_dim,
            skipped_inactive=skipped_inactive,
            skipped_scope=skipped_scope,
        )

    @property
    def fluents(self) -> tuple[str, ...]:
        """この束が接地するフルーエントのローカル名（辞書順）。"""
        return tuple(sorted(self._by_fluent))

    def spec_of(self, fluent: str) -> ProbeSpec:
        """フルーエントを接地しているプローブの登録メタデータ。

        Args:
            fluent: フルーエントのローカル名。

        Raises:
            KeyError: この束が接地していないフルーエントの場合。
        """
        return self._by_fluent[fluent][1]

    def probe_ids(self) -> dict[str, str]:
        """フルーエントのローカル名 → プローブ IRI。

        昇格したイベントの証拠に「どのプローブが言ったか」を残すために使う
        （``lift_events`` の ``probe_ids``）。
        """
        return {name: spec.probe_id for name, (_probe, spec) in self._by_fluent.items()}

    def __len__(self) -> int:
        """走るプローブの本数。"""
        return len(self._by_fluent)

    def __call__(self, latents: NDArray[np.float64]) -> Mapping[str, NDArray[np.float64]]:
        """潜在の行列を全プローブに通す。

        Args:
            latents: 潜在（``(標本数, 次元)``）。

        Returns:
            フルーエントのローカル名 → **較正済み**確率の列（不変条件 7）。

        Raises:
            BridgeError: 潜在の形がこの束の次元と合わない場合。
        """
        matrix = np.asarray(latents, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != self.latent_dim:
            raise BridgeError(
                f"probe set expects latents of shape (n, {self.latent_dim}), got {matrix.shape}"
            )
        outputs: dict[str, NDArray[np.float64]] = {}
        for name, (probe, _spec) in self._by_fluent.items():
            outputs[name] = probe.predict(matrix, ensemble_noise=self._ensemble_noise).probabilities
        return outputs
