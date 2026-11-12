"""``ObservationRequest`` → ``ObservationQuery`` の変換（設計書 §8.7、M7 の運用タスク）。

照合の解決が「判断できない」だったとき ``ObservationRequest`` が出る（M3-06）。
エピステミック・スケジューラが選ぶのは ``ObservationQuery`` で、両者は**別の型である**
——要求は「これを確かめたい」、クエリは「どうやって確かめるか」。変換には要求に無い情報が
必要になる。

| 要求が持つもの | クエリが要る追加情報 | どこから来るか |
|---|---|---|
| 実体・フルーエント・区間 | 観測の種類（``kind``） | 何で確かめるか（センサ / 人 / 業務システム） |
| 深刻度（``utility`` のヒント） | 事前確率 | 現在の信念（``knowledge`` の二時制クエリ） |
| 由来の不一致 | 信頼度・コスト | 観測手段ごとの性質（``Settings``） |

**事前確率を推測で埋めない。** 無い場合は変換しない（``None`` を返す）。事前が分からなければ
情報利得（EIG）が計算できず、**効用がコスト項だけになって「やらない方が良い」と判定される**
——それは「情報が無い」のであって「価値が無い」のではない。黙って 0.5 を入れると、あらゆる要求が
同じ中間の事前を持つことになり、スケジューラの選別が意味を失う。

**深刻度をそのまま価値にする。** 要求の ``utility`` は照合が付けた深刻度（事業価値 × 不一致の
大きさ、0〜1 に正規化済み）で、スケジューラの ``value`` はまさに ``v(x)`` である。
ここで再計算すると、照合とスケジューラで別の価値観が並ぶ。

**手段はフルーエントごとに解決する**（M8-05、ADR-0011）。全件に同じ手段を当てると、人の確認で
しか確かめられないフルーエントも「サンプリング率を上げれば分かる」ことになり、信頼度と
コストが実態から外れる。解決は呼び出し側の関数（``MeansResolver``）に委ねる——宣言の読み込みは
:mod:`sbp.reasoning.means` の仕事で、ここは変換だけを持つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.reasoning.scheduler import ObservationQuery, QueryKind

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sbp.reasoning.reconciliation.learning import ObservationRequest

    MeansResolver = Callable[[str], "ObservationMeans"]
    """``フルーエント IRI -> 観測手段``。:meth:`sbp.reasoning.means.MeansRegistry.for_fluent`。"""

__all__ = ["MeansResolver", "ObservationMeans", "SkippedRequest", "to_queries", "to_query"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ObservationMeans:
    """観測手段の性質（どうやって確かめるか）。

    Attributes:
        kind: 観測の種類。
        reliability: その手段の信頼度（混同行列からの要約）。
        cost: 実行コスト（効用と同じ尺度）。
    """

    kind: QueryKind
    reliability: float
    cost: float


@dataclass(frozen=True, slots=True)
class SkippedRequest:
    """変換できなかった要求。

    **捨てた理由を残す。** 要求が積まれたまま消えると、「観測が必要と判断したのに何も
    起きなかった」という状態が説明できない。

    Attributes:
        request: 元の要求。
        reason: なぜ変換できなかったか。
    """

    request: ObservationRequest
    reason: str


_NO_PRIOR = "現在の信念が無いので事前確率が決まらない（推測で埋めない）"
"""事前が無い場合の理由。"""


def to_query(
    request: ObservationRequest,
    *,
    priors: Mapping[tuple[str, str], float],
    means: ObservationMeans | MeansResolver,
) -> ObservationQuery | None:
    """要求 1 件をクエリにする。

    Args:
        request: 照合が出した追加観測の要求。
        priors: ``(実体, フルーエント)`` → 現在の信念（事前確率）。
        means: 観測手段。単一の :class:`ObservationMeans`、またはフルーエント IRI から
            手段を返す関数（``MeansResolver``）。

    Returns:
        ``ObservationQuery``、事前が無ければ ``None``。
    """
    key = (request.entity, request.fluent)
    prior = priors.get(key)
    if prior is None:
        return None
    resolved = means(request.fluent) if callable(means) else means
    return ObservationQuery(
        query_id=f"{request.discrepancy_id}:{request.fluent}",
        kind=resolved.kind,
        entity=request.entity,
        fluents=(request.fluent,),
        prior={request.fluent: prior},
        # 深刻度をそのまま v(x) にする（照合とスケジューラで価値観を二重に持たない）
        value={request.fluent: request.utility},
        reliability=resolved.reliability,
        cost=resolved.cost,
        reason=f"{request.reason}（discrepancy={request.discrepancy_id}）",
    )


def to_queries(
    requests: Sequence[ObservationRequest],
    *,
    priors: Mapping[tuple[str, str], float],
    means: ObservationMeans | MeansResolver,
) -> tuple[list[ObservationQuery], list[SkippedRequest]]:
    """要求の列をクエリの列にする。

    **変換できなかった分を別に返す。** 落とした件数を返さないと、要求が積まれたまま消えた
    ことが分からない（スケジューラの選択が 0 件なのか、入力が 0 件なのかも区別できない）。

    Args:
        requests: 要求の列。
        priors: 現在の信念。
        means: 観測手段。単一の手段、またはフルーエントごとに解決する関数。

    Returns:
        ``(クエリの列, 変換できなかった要求の列)``。
    """
    queries: list[ObservationQuery] = []
    skipped: list[SkippedRequest] = []
    for request in requests:
        query = to_query(request, priors=priors, means=means)
        if query is None:
            skipped.append(SkippedRequest(request=request, reason=_NO_PRIOR))
            continue
        queries.append(query)
    if skipped:
        log.info(
            "reasoning.requests_skipped",
            count=len(skipped),
            converted=len(queries),
            reason=_NO_PRIOR,
        )
    return queries, skipped
