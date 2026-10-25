"""イベント → 信念更新 → アサーションの配線（設計書 §8.1、M7 の運用タスク）。

M5-08 のワーカーは**周期の管理と停止の振る舞いだけ**を持ち、``step()`` は処理件数 0 を返していた。
ここがその欠けていた中身である。

**配線を ``reasoning`` に置き、``services/`` には置かない。** ワーカーにロジックを書くと、
テストが Compose 必須になる（`.claude/rules/infra-testing.md`）。ここは純粋な変換で、
入力は ``EventEnvelope``、出力は ``Assertion`` と来歴である。

**実体単位にフィルタを持つ。** 前向きフィルタは「直前の事後」と「その状態が続いた長さ」を
状態として持つので、実体とフルーエントの組ごとに 1 本必要である。イベントの順序保証は実体単位
（不変条件 4）であり、**フィルタの単位をそれより細かくも粗くもできない**——粗くすると別の実体の
観測が混ざり、細かくすると順序の保証が足りなくなる。

**グローバルな時刻順を仮定しない**（不変条件 4）。実体内で時刻が戻ったイベントは、前向きフィルタ
に入れずに捨てる。入れると滞在時間が負になり、半マルコフの計算が壊れる。捨てたことは記録する
——「静かに無視した」ことが分からないと、パーティション設定の誤りに気付けない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core import vocab
from sbp.core.audit import AuditMask, no_audit
from sbp.core.belief import BooleanBelief
from sbp.core.errors import ReasoningError
from sbp.core.logging import bind_event, get_logger
from sbp.reasoning.belief import BeliefFilter, TransitionPrior

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from structlog.stdlib import BoundLogger

    from sbp.core.assertion import Assertion
    from sbp.core.events import EventEnvelope
    from sbp.integration.reliability import ReliabilityModel
    from sbp.reasoning.belief import BeliefUpdate

__all__ = [
    "BeliefIngest",
    "IngestOutcome",
    "SkipReason",
    "default_transition",
    "fluent_of_event",
]

log = get_logger(__name__)


class SkipReason:
    """イベントを信念更新に入れなかった理由。

    文字列定数にしているのは、ログと指標のラベルにそのまま出すためである。
    """

    NOT_A_FLUENT = "型からフルーエントを取り出せない"
    """``PhysicalEvent/ent:Running`` の形でない。昇格イベント以外（心拍など）が混ざるとここ。"""
    NOT_BOOLEAN = "真偽フルーエントではない"
    """数値・カテゴリの信念は別の更新則が必要（この版では真偽のみ）。"""
    OUT_OF_ORDER = "実体内で時刻が戻った"
    """前向きフィルタに入れると滞在時間が負になる。"""
    AUDIT_EXCLUDED = "監査隔離の対象"
    """不変条件 6。監査対象の記録とその派生信念を入力にしない。"""


_COMPOSITE_HEADS: tuple[str, ...] = (
    f"{vocab.core.PhysicalEvent}/",
    f"{vocab.core.BusinessEvent}/",
    "sbp-core:PhysicalEvent/",
    "sbp-core:BusinessEvent/",
)
"""フルーエントを運ぶ複合型の頭（事象昇格の ``PhysicalEvent/<fluent>``、取込の主張
``BusinessEvent/<fluent>``。設計書 §6.1、ADR-0016）。CURIE と完全 IRI の両方を受ける。"""

_FLUENT_LOCAL_NAMES: frozenset[str] = frozenset(
    iri.rsplit("#", 1)[-1] for iri in vocab.FLUENT_TYPES
)
"""オントロジーの FluentType のローカル名。複合型の末尾がこれに当たるときだけフルーエントとする。"""


def fluent_of_event(event_type: str) -> str | None:
    """イベント型からフルーエントのローカル名を取り出す。

    ``sbp-core:PhysicalEvent/ent:Running`` → ``Running``。フルーエントを運ぶ複合型でなければ
    ``None``。

    **区切りの有無だけで複合型と判定しない。** 完全 IRI はスキームに ``/`` を含むので、
    ``https://…/ent#OperationLog``（取込の「記録が存在する」イベント）が ``OperationLog`` という
    フルーエントに化け、信念の取込は述語が無いと例外で落ちていた（M8-25-6 の統合テストで発見、
    ``docs/PROGRESS.md`` 所見 116）。頭が既知の事象クラスで、末尾がオントロジーの FluentType の
    ときに限る。

    **``None`` を「全フルーエント」と解釈しない。** 取り出せない型は信念更新に入れない。
    推測で特定のフルーエントに割り当てると、関係ないイベントが信念を動かす。

    Args:
        event_type: イベント型の IRI。

    Returns:
        フルーエントのローカル名、取り出せなければ ``None``。
    """
    for head in _COMPOSITE_HEADS:
        if event_type.startswith(head):
            tail = event_type[len(head) :]
            local = tail.rsplit(":", 1)[-1].rsplit("#", 1)[-1]
            return local if local in _FLUENT_LOCAL_NAMES else None
    return None


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """イベント 1 件を処理した結果。

    Attributes:
        event_id: 処理したイベントの識別子（ログの相関に使う）。
        entity: 対象実体。
        fluent: 対象フルーエント（入れなかった場合は ``None``）。
        update: 信念更新の結果（入れなかった場合は ``None``）。
        assertion: 書くべきアサーション（入れなかった場合は ``None``）。
        skipped: 入れなかった理由（入れた場合は ``None``）。
    """

    event_id: str
    entity: str
    fluent: str | None
    update: BeliefUpdate | None
    assertion: Assertion | None
    skipped: str | None

    @property
    def applied(self) -> bool:
        """信念を更新したか。"""
        return self.update is not None


@dataclass(slots=True)
class BeliefIngest:
    """イベントを受けて実体単位の前向きフィルタを進める。

    Attributes:
        transition: 遷移事前分布（フルーエント共通の既定）。
        provenance: 来歴バンドル IRI の接頭辞。
        ontology_version: アサーションに刻むオントロジー版。
        predicate_of: フルーエント名 → 述語 IRI の解決（``vocab`` から渡す。
            **IRI をここに直書きしない**、不変条件 5）。
        reliability: 情報源の信頼性モデル。
        filters: 実体 × フルーエントごとのフィルタ。
        skipped: 入れなかったイベントの理由別の件数。
    """

    provenance: str
    ontology_version: str
    predicate_of: dict[str, str]
    transition: TransitionPrior = field(default_factory=TransitionPrior)
    reliability: dict[tuple[str, str], ReliabilityModel] | None = None
    filters: dict[tuple[str, str], BeliefFilter] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    _last_seen: dict[tuple[str, str], datetime] = field(default_factory=dict)

    def filter_for(self, *, entity: str, fluent: str) -> BeliefFilter:
        """実体 × フルーエントのフィルタを得る（無ければ作る）。

        Args:
            entity: 実体 IRI。
            fluent: フルーエントのローカル名。
        """
        key = (entity, fluent)
        existing = self.filters.get(key)
        if existing is not None:
            return existing
        created = BeliefFilter(fluent=fluent, entity=entity, transition=self.transition)
        self.filters[key] = created
        return created

    def handle(self, event: EventEnvelope, *, mask: AuditMask | None = None) -> IngestOutcome:
        """イベント 1 件を信念更新に入れる。

        ``mask`` は省略できるが、省略は「監査対象が無い」を**明示的に選ぶ**こと
        （:func:`sbp.core.audit.no_audit` を渡すのと同じ）。不変条件 6 の趣旨は
        「忘れて通る経路を作らない」ことなので、既定で監査マスクを外す挙動にはしていない
        ——``no_audit()`` が入ることをここで記録する。

        Args:
            event: 処理するイベント。
            mask: 監査隔離マスク。

        Returns:
            処理結果。信念を更新していない場合は ``skipped`` に理由が入る。
        """
        applied_mask = mask if mask is not None else no_audit()
        bound = bind_event(log, event, topic="reasoning.ingest")
        entity = str(event.subject)

        fluent = fluent_of_event(str(event.type))
        if fluent is None:
            return self._skip(bound, event, entity, None, SkipReason.NOT_A_FLUENT)
        if not isinstance(event.belief, BooleanBelief):
            return self._skip(bound, event, entity, fluent, SkipReason.NOT_BOOLEAN)
        if applied_mask.excludes_event(event):
            # 不変条件 6: 監査対象の記録と派生信念を入力にしない
            return self._skip(bound, event, entity, fluent, SkipReason.AUDIT_EXCLUDED)

        key = (entity, fluent)
        previous_at = self._last_seen.get(key)
        if previous_at is not None and event.tx_time < previous_at:
            # 実体内で時刻が戻った。入れると滞在時間が負になる
            return self._skip(bound, event, entity, fluent, SkipReason.OUT_OF_ORDER)

        predicate = self.predicate_of.get(fluent)
        if predicate is None:
            raise ReasoningError(
                f"no predicate IRI for fluent {fluent!r}; pass the mapping from sbp.core.vocab "
                "(invariant 5 forbids writing IRIs in code)"
            )

        probe_filter = self.filter_for(entity=entity, fluent=fluent)
        update = probe_filter.step(
            at=event.tx_time,
            probe_probabilities=(event.belief.p,),
            reliability=self.reliability,
            mask=applied_mask,
        )
        self._last_seen[key] = event.tx_time

        assertion = update.to_assertion(
            predicate=predicate,
            provenance=f"{self.provenance}/{event.event_id}",
            ontology_version=self.ontology_version,
        )
        bound.debug(
            "reasoning.ingest_applied",
            entity=entity,
            fluent=fluent,
            prior=update.prior,
            posterior=update.posterior,
            dominant=update.contribution.dominant,
        )
        return IngestOutcome(
            event_id=str(event.event_id),
            entity=entity,
            fluent=fluent,
            update=update,
            assertion=assertion,
            skipped=None,
        )

    def handle_batch(
        self, events: Sequence[EventEnvelope], *, mask: AuditMask | None = None
    ) -> list[IngestOutcome]:
        """複数イベントを順に入れる。

        **実体ごとに時刻順へ並べ替えてから入れる。** トピックのパーティションが実体単位でも、
        1 バッチの中には複数実体が混ざる。実体をまたいで時刻順に並べる必要はない（グローバル
        順序を仮定しない）が、**同じ実体の中では順序が要る**。

        Args:
            events: 処理するイベント。
            mask: 監査隔離マスク。

        Returns:
            各イベントの処理結果（入力と同じ並びではなく、処理した順）。
        """
        ordered = sorted(events, key=lambda event: (str(event.subject), event.tx_time))
        return [self.handle(event, mask=mask) for event in ordered]

    def _skip(
        self,
        bound: BoundLogger,
        event: EventEnvelope,
        entity: str,
        fluent: str | None,
        reason: str,
    ) -> IngestOutcome:
        """入れなかったことを記録する（静かに捨てない）。"""
        self.skipped[reason] = self.skipped.get(reason, 0) + 1
        bound.info(
            "reasoning.ingest_skipped",
            entity=entity,
            fluent=fluent,
            reason=reason,
            total_for_reason=self.skipped[reason],
        )
        return IngestOutcome(
            event_id=str(event.event_id),
            entity=entity,
            fluent=fluent,
            update=None,
            assertion=None,
            skipped=reason,
        )

    @property
    def tracked_entities(self) -> int:
        """フィルタを持っている実体 × フルーエントの数（滞留の目安）。"""
        return len(self.filters)

    def prune(self, *, before: datetime) -> int:
        """最後の観測が ``before`` より古いフィルタを捨てる。

        **放置するとフィルタが単調増加する。** 実体が増え続ける環境（搬送中の物品など）では、
        1 実体あたり数百バイトでも無視できない量になる。捨てた実体の信念は失われず、
        次の観測で事前から再開する（アサーションは知識グラフに残っている）。

        Args:
            before: この時刻より古いものを捨てる。

        Returns:
            捨てた数。
        """
        stale = [key for key, at in self._last_seen.items() if at < before]
        for key in stale:
            self.filters.pop(key, None)
            self._last_seen.pop(key, None)
        if stale:
            log.info("reasoning.ingest_pruned", dropped=len(stale), remaining=len(self.filters))
        return len(stale)


def default_transition(*, step: timedelta = timedelta(seconds=10)) -> TransitionPrior:
    """既定の遷移事前（刻み幅だけを指定する場合の入口）。

    Args:
        step: フィルタの刻み幅。
    """
    return TransitionPrior(step=step)
