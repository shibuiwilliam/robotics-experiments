"""不一致の起票通知（M8-04、ADR-0010）。

照合エンジンが検出した :class:`~sbp.reasoning.reconciliation.discrepancy.Discrepancy` を
イベントログに流す形にする。

**イベントは通知であって不一致そのものではない。** ``EventEnvelope`` は「誰かの主張」を運ぶ
共通の封筒で、不一致の全体（根拠アサーション・証拠・状態履歴・学習信号）を入れる場所を
持たない。入れようとすると封筒に任意のペイロードを足すことになり、全イベントが同じ形で
扱えるという §6.1 の前提が崩れる。

そのため封筒には **「どの実体に、どの類型の不一致が、いつの区間で、どれだけの確信度で
起票されたか」** だけを載せ、不一致の本体は参照（``discrepancy_id``）で辿らせる。
``docs/PROGRESS.md`` の既知の制約に「不一致の永続化と API 露出が未実装」として残る——
通知だけでは UI が中身を読めない（M8-12）。

**様相は ``inferred``。** 不一致は規則（五類型の検出器）の出力である（不変条件 3）。

**主体は 1 つしか選べない。** パーティションキーは ``subject`` 1 つで、類型 4（同一性）の
ように複数実体が関わる不一致では残りが順序保証の単位から外れる。先頭を選ぶのではなく
**辞書順で最小の実体**にしてあるのは、同じ不一致が再検出されたときに同じパーティションへ
行くためである（``subjects`` の順序は検出経路によって変わりうる）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.events import EventEnvelope, Producer, RuleEvidence, ValidTime
from sbp.core.modality import Modality

if TYPE_CHECKING:
    from sbp.reasoning.reconciliation.discrepancy import Discrepancy

__all__ = ["NOTIFY_VERSION", "discrepancy_event", "discrepancy_type_iri"]

NOTIFY_VERSION = "1.0.0"
"""通知を作るコードの版（``Producer.version``）。"""

_DETECTOR_BASE = f"{vocab.core.Discrepancy}/detector"
"""検出器の規則 IRI の接頭辞。

検出器の名前（``kind1`` など）は ``.lp`` の規則名ではなく実装側の識別子なので、
オントロジーに個体を置かず ``Discrepancy`` の IRI の下に組む。**``vocab`` から組み立てる**
ので、オントロジーの名前空間が変われば一緒に動く（不変条件 5）。
"""


def discrepancy_type_iri(discrepancy: Discrepancy) -> str:
    """不一致イベントの型 IRI。

    ``<core#Discrepancy>/kind2`` のような複合型にする（事象昇格が
    ``sbp-core:PhysicalEvent/<fluent>`` を作るのと同じ形）。類型を型に出すのは、
    購読側が類型で絞れるようにするためである。

    Args:
        discrepancy: 不一致。
    """
    return f"{vocab.core.Discrepancy}/kind{int(discrepancy.kind)}"


def discrepancy_event(discrepancy: Discrepancy, *, site_id: str) -> EventEnvelope:
    """不一致をイベントログに流す封筒にする。

    Args:
        discrepancy: 起票された不一致。
        site_id: 検出した拠点（``Producer.agent`` に出る）。

    Returns:
        ``inferred`` の :class:`~sbp.core.events.EventEnvelope`。
    """
    interval = discrepancy.interval
    return EventEnvelope(
        type=discrepancy_type_iri(discrepancy),
        subject=min(discrepancy.subjects),
        valid_time=ValidTime(
            start=interval.start,
            end=interval.end,
            # 不一致の区間は観測の窓から決まるので、変化点推定の不確実さは持たない。
            # **0 を明示する**——フィールドを省くと既定値が無いので構築に失敗する
            uncertainty_s=0.0,
        ),
        tx_time=discrepancy.detected_at,
        modality=Modality.INFERRED,
        # 「この不一致が成立している」確率は深刻度の中の `confidence`（区間内の平均事後）。
        # 深刻度スコアそのものは順位付けのための合成値なので確率ではない
        belief=BooleanBelief(p=discrepancy.severity.confidence),
        evidence=(RuleEvidence(rule=f"{_DETECTOR_BASE}/{discrepancy.detector}"),),
        producer=Producer(agent=f"reconcile@{site_id}", version=NOTIFY_VERSION),
        correlation_id=discrepancy.discrepancy_id,
        ontology_version=discrepancy.ontology_version,
    )
