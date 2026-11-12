"""イベントログのトピック定義（設計書 §6.1）。

トピックの追加はここに書き ``make seed`` で作成する（手作業禁止。knowledge ルール）。
トピック名は ``<site>.<layer>.<class>``、パーティションキーは主体実体 IRI。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sbp.core.events import EventLayer, topic_name

__all__ = ["TopicSpec", "central_topics", "topics_for_site"]


@dataclass(frozen=True, slots=True)
class TopicSpec:
    """トピック 1 件の仕様。

    Attributes:
        name: トピック名（``<site>.<layer>.<class>``）。
        partitions: パーティション数。実体単位の順序を保つため、増やしても既存キーの
            割り当てが変わらないことを前提にする（増設は再割り当てを招くので慎重に）。
        retention_ms: 保持期間（ミリ秒）。``-1`` は無期限（設計書 §6.1「保持と圧縮: 無期限」）。
        description: 何が流れるトピックか。
    """

    name: str
    partitions: int
    retention_ms: int
    description: str


_SITE_TOPICS: Final[tuple[tuple[EventLayer, str, int, str], ...]] = (
    (
        EventLayer.BRIDGE,
        "physical",
        3,
        "接地層が昇格した物理事象（TypedProbabilisticEvent、様相 inferred）",
    ),
    (
        EventLayer.INTEGRATION,
        "business",
        3,
        "業務統合層が変換した業務事象（Record 由来、信頼性メタ付き）",
    ),
    (
        EventLayer.INTEGRATION,
        "mapping",
        1,
        "写像のドリフト検知（MappingDriftDetected）。例外ではなくイベントとして出す",
    ),
    (
        EventLayer.REASONING,
        "belief",
        3,
        "信念更新の結果（様相 inferred、三源寄与率を来歴に持つ）",
    ),
    (
        EventLayer.REASONING,
        "discrepancy",
        1,
        "照合エンジンが起票した不一致（五類型）とその状態遷移",
    ),
    (
        EventLayer.REASONING,
        "expectation",
        1,
        "アクション実行が生成した期待と検証結果",
    ),
    (
        EventLayer.REASONING,
        "forecast",
        1,
        "予測（様相 predicted、起点と先行時間つき）",
    ),
    (
        EventLayer.ACTIONS,
        "command",
        1,
        "アクションの実行・承認・補償（冪等 command_id つき）",
    ),
    (
        EventLayer.WORLDMODEL,
        "health",
        1,
        "WM のドリフト通知（潜在シフト・予測誤差・整合残差の閾値超過）",
    ),
)

_CENTRAL_TOPICS: Final[tuple[tuple[EventLayer, str, int, str], ...]] = (
    (
        EventLayer.REASONING,
        "discrepancy",
        1,
        "拠点横断で集約した不一致（中央の照合・監査用）",
    ),
    (
        EventLayer.WORLDMODEL,
        "registry",
        1,
        "モデル版・プローブ版の昇格と互換性行列の更新",
    ),
)

_RETENTION_FOREVER: Final = -1
"""無期限保持。状態はイベントログからの投影であり、再構築可能性を保つため消さない。"""


def topics_for_site(site_id: str) -> list[TopicSpec]:
    """指定拠点のトピック一覧を返す。

    Args:
        site_id: 拠点識別子（例 ``siteA``）。
    """
    return [
        TopicSpec(
            name=topic_name(site_id, layer, event_class),
            partitions=partitions,
            retention_ms=_RETENTION_FOREVER,
            description=description,
        )
        for layer, event_class, partitions, description in _SITE_TOPICS
    ]


def central_topics() -> list[TopicSpec]:
    """中央のトピック一覧を返す。"""
    return [
        TopicSpec(
            name=topic_name("central", layer, event_class),
            partitions=partitions,
            retention_ms=_RETENTION_FOREVER,
            description=description,
        )
        for layer, event_class, partitions, description in _CENTRAL_TOPICS
    ]


def all_topics(site_id: str) -> list[TopicSpec]:
    """拠点 + 中央のトピック一覧を返す。"""
    return topics_for_site(site_id) + central_topics()
