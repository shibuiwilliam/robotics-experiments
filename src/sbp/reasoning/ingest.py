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
from sbp.core.events import RecordEvidence
from sbp.core.logging import bind_event, get_logger
from sbp.reasoning.belief import BeliefFilter, TransitionPrior, claim_log_odds

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from structlog.stdlib import BoundLogger

    from sbp.core.assertion import Assertion
    from sbp.core.events import EventEnvelope
    from sbp.integration.reliability import ReliabilityModel
    from sbp.reasoning.belief import BeliefUpdate, PlanView

    PlanProvider = Callable[[str, str, datetime], PlanView | None]
    """``(実体, フルーエント型の IRI, 時刻) -> その時刻に有効な計画``（無ければ ``None``）。"""

__all__ = [
    "BeliefIngest",
    "IngestOutcome",
    "SkipReason",
    "default_transition",
    "fluent_of_event",
    "is_record_claim",
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
    STALE_CLAIM = "同じ情報源のより新しい主張が既にある"
    """遅れて届いた古い主張。置き換えると最新の状態を古い主張が上書きする（ADR-0027）。"""
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


def _claim_source(event: EventEnvelope) -> str:
    """主張の情報源（``RecordEvidence.source``）。無ければ空文字（1 つの情報源として扱う）。"""
    for evidence in event.evidence:
        if isinstance(evidence, RecordEvidence):
            return evidence.source
    return ""


def is_record_claim(event_type: str) -> bool:
    """業務記録の主張イベント（``BusinessEvent/<fluent>``）か。

    主張イベントは記録尤度に、物理イベント（``PhysicalEvent/<fluent>``）は観測尤度に入る。
    **どちらの源に入るかは型だけで決める**——``modality`` や証拠の種類で決めると、取込が
    ``observed`` で出す主張と、接地が ``inferred`` で出す観測の区別が証拠の書き方に依存する。

    Args:
        event_type: イベント型の IRI。
    """
    return fluent_of_event(event_type) is not None and event_type.startswith(
        (f"{vocab.core.BusinessEvent}/", "sbp-core:BusinessEvent/")
    )


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
        is_claim: 業務記録の主張だったか（偽なら物理観測）。
        undo: この更新が進めたフィルタの状態を元に戻す関数（更新しなかった場合は ``None``）。
            **書き込みに失敗したら呼ぶ**——戻さずに再配送を受けると、同じ証拠を二重に数える。
    """

    event_id: str
    entity: str
    fluent: str | None
    update: BeliefUpdate | None
    assertion: Assertion | None
    skipped: str | None
    is_claim: bool = False
    undo: Callable[[], None] | None = None

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
        reliability: 情報源の信頼性モデル（記録そのものを渡す経路用。主張イベントは取込が
            信頼性を通した確率にして運んでくるので、ここには要らない）。
        plan_provider: 計画の引き当て（``None`` なら計画源は使わない）。
        filters: 実体 × フルーエントごとのフィルタ。
        skipped: 入れなかったイベントの理由別の件数。
    """

    provenance: str
    ontology_version: str
    predicate_of: dict[str, str]
    transition: TransitionPrior = field(default_factory=TransitionPrior)
    reliability: dict[tuple[str, str], ReliabilityModel] | None = None
    plan_provider: PlanProvider | None = None
    filters: dict[tuple[str, str], BeliefFilter] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    _last_seen: dict[tuple[str, str], datetime] = field(default_factory=dict)
    _touched: dict[tuple[str, str], datetime] = field(default_factory=dict)
    _claim_terms: dict[tuple[str, str], dict[str, tuple[float, datetime]]] = field(
        default_factory=dict
    )

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

        claim = is_record_claim(str(event.type))
        key = (entity, fluent)
        previous_at = self._last_seen.get(key)
        if not claim and previous_at is not None and event.tx_time < previous_at:
            # 実体内で観測の時刻が戻った。入れると滞在時間が負になる。**主張は数えない**——
            # 業務記録は計上が遅れて届くのが普通で（``tx_time`` は記録時刻）、観測の列とは
            # 別の時間軸を進む
            return self._skip(bound, event, entity, fluent, SkipReason.OUT_OF_ORDER)

        predicate = self.predicate_of.get(fluent)
        if predicate is None:
            raise ReasoningError(
                f"no predicate IRI for fluent {fluent!r}; pass the mapping from sbp.core.vocab "
                "(invariant 5 forbids writing IRIs in code)"
            )

        source = _claim_source(event) if claim else ""
        latest = self._claim_terms.get(key, {}).get(source)
        if claim and latest is not None and event.tx_time < latest[1]:
            return self._skip(bound, event, entity, fluent, SkipReason.STALE_CLAIM)

        existed = key in self.filters
        probe_filter = self.filter_for(entity=entity, fluent=fluent)
        before = (
            probe_filter.posterior,
            probe_filter.elapsed,
            self._last_seen.get(key),
            self._touched.get(key),
            dict(self._claim_terms.get(key, {})),
        )
        # 同じ情報源の主張は直前のものを置き換える。業務記録は同じ状態を繰り返し言うので（誤りも
        # 繰り返される）、独立な証拠として足すと件数だけで信念が飽和する（H-26、ADR-0027）。
        # 観測が挟まっても持ち分は残す（空にすると観測と主張の交互で再び積み上がる）
        terms = self._claim_terms.setdefault(key, {})
        superseded = terms[source][0] if claim and source in terms else 0.0
        plan = (
            self.plan_provider(entity, predicate, event.tx_time)
            if self.plan_provider is not None
            else None
        )
        # 主張は時間を進めない。事後は後の観測の証拠を含みうるので、時刻は最新の更新より後に
        # する（遅れて届いた主張に古い時刻を付けると、最新の読み出しがその主張を飛ばす）。
        # 同じ時刻の 2 件が並ばないよう 1 マイクロ秒だけ進める
        touched = self._touched.get(key)
        step_at = (
            max(event.tx_time, touched + timedelta(microseconds=1))
            if claim and touched is not None
            else event.tx_time
        )
        update = probe_filter.step(
            at=step_at,
            advance=not claim,
            probe_probabilities=() if claim else (event.belief.p,),
            claim_probabilities=(event.belief.p,) if claim else (),
            reliability=self.reliability,
            plan=plan,
            mask=applied_mask,
            superseded_claim_log_odds=superseded,
        )
        if claim:
            terms[source] = (claim_log_odds(event.belief.p), event.tx_time)
        if not claim:
            self._last_seen[key] = event.tx_time
        self._touched[key] = max(step_at, self._touched.get(key, step_at))

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
            is_claim=claim,
            undo=lambda: self._restore(key, existed=existed, before=before),
        )

    def _restore(
        self,
        key: tuple[str, str],
        *,
        existed: bool,
        before: tuple[
            float | None,
            timedelta,
            datetime | None,
            datetime | None,
            dict[str, tuple[float, datetime]],
        ],
    ) -> None:
        """更新前のフィルタの状態に戻す（書き込みに失敗した更新の取り消し）。

        Args:
            key: ``(実体, フルーエント)``。
            existed: 更新の前からフィルタがあったか（無ければ消す）。
            before: ``(事後, 滞在時間, 最後の観測, 最後の更新, 情報源ごとの主張の持ち分)``。
        """
        if not existed:
            self.filters.pop(key, None)
        else:
            restored = self.filters[key]
            restored.posterior, restored.elapsed = before[0], before[1]
        for table, value in ((self._last_seen, before[2]), (self._touched, before[3])):
            if value is None:
                table.pop(key, None)
            else:
                table[key] = value
        if existed:
            self._claim_terms[key] = before[4]
        else:
            self._claim_terms.pop(key, None)

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
        """最後に更新した時刻が ``before`` より古いフィルタを捨てる（観測も主張も数える）。

        **放置するとフィルタが単調増加する。** 実体が増え続ける環境（搬送中の物品など）では、
        1 実体あたり数百バイトでも無視できない量になる。捨てた実体の信念は失われず、
        次の観測で事前から再開する（アサーションは知識グラフに残っている）。

        Args:
            before: この時刻より古いものを捨てる。

        Returns:
            捨てた数。
        """
        stale = [key for key, at in self._touched.items() if at < before]
        for key in stale:
            self.filters.pop(key, None)
            self._last_seen.pop(key, None)
            self._touched.pop(key, None)
            self._claim_terms.pop(key, None)
        if stale:
            log.info("reasoning.ingest_pruned", dropped=len(stale), remaining=len(self.filters))
        return len(stale)


def default_transition(*, step: timedelta = timedelta(seconds=10)) -> TransitionPrior:
    """既定の遷移事前（刻み幅だけを指定する場合の入口）。

    Args:
        step: フィルタの刻み幅。
    """
    return TransitionPrior(step=step)
