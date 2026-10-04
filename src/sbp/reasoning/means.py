"""観測手段の割り当て（M8-05、ADR-0011）。

フルーエントごとに「どうやって確かめるか」（:class:`~sbp.reasoning.requests.ObservationMeans`）
を宣言から引く。宣言は ``observation/<domain>.yaml``。

**オントロジーに置かない**（ADR-0011）。コストと信頼度は**拠点が持つ計測器の性質**であり、
拠点ごとに違う。オントロジーに置くと、拠点が計測器を更新するたびにオントロジーの版が上がり、
再接地の判定に無関係な変更が混ざる。センサ由来のプローブと概念の対応は
:class:`~sbp.bridge.probes.ProbeSpec` の ``grounds`` が既に持っているので、ここに書くと
二重管理にもなる。

**宣言したフルーエントがオントロジーに実在することを読み込み時に検査する**（不変条件 5）。
存在しない概念の観測手段を宣言できてしまうと、タイプミスが「その概念は既定手段で観測する」
という静かな挙動になる。

**既定手段を持つ。** 宣言の無いフルーエントにも手段を当てないと、要求が 1 件も変換されず
スケジューラが常に 0 件を返す。ただし**既定を人への質問にしない**——頻度上限に当たって何も
選ばれない周期が出るうえ、「自動で人に聞き続ける」挙動は望ましくない。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from sbp.core import vocab
from sbp.core.errors import ConfigError
from sbp.core.logging import get_logger
from sbp.reasoning.requests import ObservationMeans
from sbp.reasoning.scheduler import QueryKind

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["MeansRegistry"]

log = get_logger(__name__)

DEFAULT_DIRECTORY = Path("observation")
"""宣言の置き場。"""


def _parse_means(entry: Mapping[str, object], *, where: str) -> ObservationMeans:
    """1 件の宣言を :class:`ObservationMeans` にする。

    Args:
        entry: YAML の 1 エントリ。
        where: エラーメッセージに出す場所（ファイル名など）。

    Raises:
        ConfigError: ``kind`` が未知、または信頼度・コストが範囲外の場合。
    """
    raw_kind = entry.get("kind")
    try:
        kind = QueryKind(str(raw_kind))
    except ValueError as exc:
        raise ConfigError(
            f"unknown observation kind {raw_kind!r} in {where}; "
            f"known kinds: {sorted(k.value for k in QueryKind)}"
        ) from exc
    try:
        reliability = float(entry["reliability"])  # type: ignore[arg-type]
        cost = float(entry["cost"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(
            f"observation means in {where} needs numeric 'reliability' and 'cost'"
        ) from exc
    if not 0.0 < reliability <= 1.0:
        raise ConfigError(
            f"reliability must be in (0, 1] in {where}, got {reliability}; a means that is "
            "never right carries no information and one that is always right is not a means"
        )
    if cost < 0.0:
        raise ConfigError(f"cost must not be negative in {where}, got {cost}")
    return ObservationMeans(kind=kind, reliability=reliability, cost=cost)


class MeansRegistry:
    """フルーエント IRI → 観測手段。

    Attributes:
        default: 宣言の無いフルーエントに当てる手段。
    """

    def __init__(self, means: dict[str, ObservationMeans], *, default: ObservationMeans) -> None:
        """Args:
        means: フルーエント IRI → 手段。
        default: 既定手段。
        """
        self._means = means
        self.default = default

    @classmethod
    def from_directory(cls, directory: Path = DEFAULT_DIRECTORY) -> MeansRegistry:
        """ディレクトリ内の全宣言を読み込む。

        Args:
            directory: ``observation/`` のパス。

        Returns:
            レジストリ。

        Raises:
            ConfigError: 宣言が 1 つも無い、既定手段が無い、同じフルーエントの宣言が
                重複している、またはフルーエントがオントロジーに実在しない場合。
        """
        means: dict[str, ObservationMeans] = {}
        default: ObservationMeans | None = None
        files = sorted(directory.glob("*.yaml"))
        if not files:
            raise ConfigError(
                f"no observation means declared under {directory}; without them the "
                "scheduler converts no request and silently issues nothing (ADR-0011)"
            )
        for path in files:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if (raw_default := payload.get("default")) is not None:
                if default is not None:
                    raise ConfigError(
                        f"two files declare a default observation means ({path.name}); "
                        "which one applies would be arbitrary"
                    )
                default = _parse_means(raw_default, where=f"{path.name} default")
            for entry in payload.get("means", []):
                fluent = _expand(str(entry.get("fluent", "")), where=path.name)
                if fluent not in vocab.FLUENT_TYPES:
                    raise ConfigError(
                        f"{fluent} is declared in {path.name} but is not a FluentType in the "
                        f"ontology (version {vocab.ONTOLOGY_VERSION}); a typo here would read "
                        "as 'that concept uses the default means' (invariant 5)"
                    )
                if fluent in means:
                    raise ConfigError(f"duplicate observation means for {fluent} in {path.name}")
                means[fluent] = _parse_means(entry, where=f"{path.name} {fluent}")
        if default is None:
            raise ConfigError(
                f"no default observation means under {directory}; fluents without an explicit "
                "means would convert to no query at all"
            )
        log.info(
            "reasoning.means_loaded",
            declared=len(means),
            default_kind=default.kind.value,
            files=[path.name for path in files],
        )
        return cls(means, default=default)

    def for_fluent(self, fluent: str) -> ObservationMeans:
        """フルーエントの観測手段を返す（宣言が無ければ既定）。

        Args:
            fluent: フルーエント IRI または CURIE。
        """
        return self._means.get(_expand(fluent, where="lookup"), self.default)

    @property
    def declared(self) -> tuple[str, ...]:
        """明示的に宣言されているフルーエント（辞書順）。"""
        return tuple(sorted(self._means))


def _expand(value: str, *, where: str) -> str:
    """CURIE を完全 IRI にする。

    Args:
        value: CURIE または完全 IRI。
        where: エラーメッセージに出す場所。

    Raises:
        ConfigError: 空、または未知のプレフィクスの場合。
    """
    from sbp.core.errors import MappingError  # noqa: PLC0415
    from sbp.integration.mappings import expand_curie  # noqa: PLC0415 — 循環 import 回避

    if not value:
        raise ConfigError(f"observation means in {where} has no 'fluent'")
    try:
        return expand_curie(value)
    except MappingError as exc:
        raise ConfigError(f"{exc} (in {where})") from exc
