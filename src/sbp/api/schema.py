"""GraphQL スキーマ（設計書 §10.1、M5-07）。

> GraphQL over ontology: ObjectType/LinkType/FunctionType を型として公開。時制・様相はフィールド
> 引数（``asOf``, ``validAt``, ``modality``, ``minConfidence``）

**時制と様相をフィールド引数にするのが要点である。** 「ポンプは稼働中か」という問いは、引数なしでは
答えられない——いつの話か（``validAt``）、いつ時点でそう信じていたか（``asOf``）、誰の主張か
（``modality``）で答えが変わる。引数を省略できる既定を「現在・全様相」にすると、呼び出し側は
自分が何を問うているか意識しないまま答えを受け取る。そこで **``modality`` は既定を
``inferred``（基盤の推定）に固定し**、他の様相は明示的に要求させる。

**確信度の下限も引数にする。** 「稼働中である」と「稼働中かもしれない（0.3）」を同じ答えとして
返すと、使う側が閾値を各自で決めることになり、同じ問いに違う答えが出る。

**GraphQL は読み取り専用。** アクションの実行は :mod:`sbp.api.commands`（コマンド API）に分けて
ある。GraphQL の mutation で副作用を出せるようにすると、承認フロー（設計書 §9.2）を迂回する
経路が生まれる。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Protocol, runtime_checkable

import strawberry

from sbp.api.actions_view import summarize
from sbp.api.authorization import AuthorizationError, visible_rows
from sbp.api.evidence import build_evidence
from sbp.core import vocab
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.knowledge.contributions import SERIES_LIMIT
from sbp.knowledge.decisions import DecisionKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.knowledge.assertions import AssertionRow
    from sbp.knowledge.concepts import ConceptProposalRecord
    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.knowledge.expectations import ExpectationRecord
else:  # Protocol の注釈を実行時にも解決できるようにする
    Sequence = __import__("collections.abc", fromlist=["Sequence"]).Sequence
    AssertionRow = object
    DiscrepancyRecord = object
    ExpectationRecord = object

__all__ = [
    "BeliefEvidenceType",
    "BeliefReader",
    "BeliefSource",
    "BeliefType",
    "DiscrepancyType",
    "ExpectationType",
    "Query",
    "build_schema",
]

log = get_logger(__name__)

_DEFAULT_MIN_CONFIDENCE = 0.0
"""確信度の下限の既定。0 は「全部返す」——ただし呼び出し側が閾値を意識できるよう引数で見せる。"""

_DEFAULT_QUEUE_LIMIT = 50
"""不一致キューの既定件数。人が 1 画面で見られる量に合わせる。"""

_MAX_QUEUE_LIMIT = 500
"""同上限。無制限にすると、未解決が溜まったときに 1 リクエストで全件が返る。"""


# ``Modality`` は値を持つ StrEnum なので継承できない（Python の enum の制約）。GraphQL 用の
# 列挙を別に作り、値を一致させる。一致はテストで検査する——ずれると API の様相指定が静かに
# 別の意味になる。
# 戻り値は使わず、登録の副作用だけを使う。``Modality`` 自体に GraphQL の定義が付くので、
# 注釈は ``Modality`` のまま書ける（別名を型として使うと pyright が解決できない）。
strawberry.enum(
    Modality,
    name="Modality",
    description="様相（observed / inferred / predicted / planned / normative / hypothetical）",
)


@strawberry.type
class BeliefType:
    """信念 1 件（アサーション 1 文）。

    Attributes:
        subject: 主語の IRI。
        predicate: 述語（フルーエント型）の IRI。
        object: 目的語の文字列表現。
        validFrom: 有効開始。
        validTo: 有効終了（継続中は ``None``）。
        txFrom: 記録開始。
        txTo: 記録終了（現在の信念なら ``None``）。
        modality: 様相。
        confidence: 較正済み確信度。
        provenance: 来歴バンドルの IRI。
        ontologyVersion: この文を作ったときのオントロジー版。
    """

    subject: str
    predicate: str
    object: str
    validFrom: datetime  # noqa: N815 — GraphQL は camelCase
    validTo: datetime | None  # noqa: N815
    txFrom: datetime  # noqa: N815
    txTo: datetime | None  # noqa: N815
    modality: Modality
    confidence: float
    provenance: str
    ontologyVersion: str  # noqa: N815

    @classmethod
    def from_row(cls, row: AssertionRow) -> BeliefType:
        """二時制ストアの行から作る。

        Args:
            row: ``AssertionStore.query`` が返す行。
        """
        return cls(
            subject=row.subject,
            predicate=row.predicate,
            object=row.object_,
            validFrom=row.valid_from,
            validTo=row.valid_to,
            txFrom=row.tx_from,
            txTo=row.tx_to,
            modality=Modality(row.modality),
            confidence=row.confidence,
            provenance=row.provenance,
            ontologyVersion=row.ontology_version,
        )


@strawberry.type
class DiscrepancyType:
    """不一致 1 件（M8-12、ADR-0013）。

    **証拠は IRI で返す。** アサーション ID から信念を引く経路が無いので（ADR-0013 の未決）、
    証拠の並置は呼び出し側が ``beliefs`` と組み合わせて行う。埋め込んで返すと、1 件あたり
    3 本のクエリが走り、キュー 50 件で 150 本になる。

    Attributes:
        discrepancyId: 識別子。
        kind: 五類型（1〜5）。
        status: ライフサイクル状態。
        subjects: 対象の実体 IRI。
        severity: 深刻度の総合スコア（並べ替えに使う）。
        detectedAt: 検出時刻。
        detector: 検出した規則の名前。
        assertions: 根拠になった信念アサーションの IRI。
        evidence: 根拠になった証拠（イベント・記録）の IRI。
        assigneeRole: 配賦先ロールの IRI。
        ontologyVersion: 検出時のオントロジー版。
        fluentRefs: 根拠のうち、オントロジーのフルーエント型であるもの（三源並置の鍵。イベント型は
            含めない——信念を引けない）。
        intervalStart: 不一致が成立していた区間の始端（証拠の三源並置の鍵。M8-17）。
        intervalEnd: 同じく終端（右開。継続中は ``None``）。
    """

    discrepancyId: str  # noqa: N815 — GraphQL は camelCase
    kind: int
    status: str
    subjects: list[str]
    severity: float
    detectedAt: datetime  # noqa: N815
    detector: str
    assertions: list[str]
    evidence: list[str]
    assigneeRole: str | None  # noqa: N815
    ontologyVersion: str  # noqa: N815
    intervalStart: datetime | None = None  # noqa: N815
    intervalEnd: datetime | None = None  # noqa: N815
    fluentRefs: list[str] = strawberry.field(default_factory=list)  # noqa: N815

    @classmethod
    def from_record(cls, record: DiscrepancyRecord) -> DiscrepancyType:
        """作業項目レコードから作る。

        Args:
            record: 表の行。
        """
        payload = record.payload
        raw_interval = payload.get("interval")
        interval: dict[str, object] = raw_interval if isinstance(raw_interval, dict) else {}
        return cls(
            discrepancyId=str(record.discrepancy_id),
            kind=record.kind,
            status=record.status,
            subjects=list(record.subjects),
            severity=record.severity_score,
            detectedAt=record.detected_at,
            detector=str(payload.get("detector", "")),
            assertions=[str(value) for value in payload.get("assertions", ())],
            evidence=[str(value) for value in payload.get("evidence", ())],
            assigneeRole=payload.get("assignee_role"),
            ontologyVersion=str(payload.get("ontology_version", "")),
            intervalStart=_parse_time(interval.get("start")),
            intervalEnd=_parse_time(interval.get("end")),
            fluentRefs=[
                str(value) for value in payload.get("assertions", ()) if value in vocab.FLUENT_TYPES
            ],
        )


def _parse_time(value: object) -> datetime | None:
    """payload の ISO 8601 文字列を ``datetime`` にする（無ければ ``None``）。

    Args:
        value: 文字列または ``None``。
    """
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        # 書いたのは UTC の pydantic モデルだが、古い行が offset を欠いても落とさない
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


@strawberry.type
class ExpectationType:
    """期待 1 件（M8-18）。

    **判定は 2 面を別々に返す。** 記号側（T 由来）と潜在側（rollout 由来）のどちらが外れたかで
    原因が違う（規則のずれ / WM の劣化 / アクションの失敗）。1 つにまとめると、UI が毎回
    証拠を開いて同じ判断をすることになる。

    **宣言していない面は ``null`` である。** 片面しか効果を宣言していない期待で両面の判定を
    要求すると、観測していない面が常に偽になって「違反」に見える。

    Attributes:
        expectationId: 識別子。
        status: ライフサイクル状態（``Created`` / ``Active`` / …）。
        actionType: アクション型 IRI。
        target: 対象実体の IRI。
        createdAt: 生成時刻。
        deadline: 期限（期待効果の最小値。``Created`` では ``null``）。
        symbolicVerdict: 記号側の判定（未検証・未宣言は ``null``）。
        rolloutVerdict: 潜在側の判定（同）。
        method: 判定方法（未検証なら ``null``）。
        effectCount: 宣言された期待効果の数。
        ontologyVersion: 生成時のオントロジー版。
    """

    expectationId: str  # noqa: N815 — GraphQL は camelCase
    status: str
    actionType: str  # noqa: N815
    target: str
    createdAt: datetime  # noqa: N815
    deadline: datetime | None
    symbolicVerdict: bool | None  # noqa: N815
    rolloutVerdict: bool | None  # noqa: N815
    method: str | None
    effectCount: int  # noqa: N815
    ontologyVersion: str  # noqa: N815

    @classmethod
    def from_record(cls, record: ExpectationRecord) -> ExpectationType:
        """作業項目レコードから作る。

        Args:
            record: 表の行。
        """
        payload = record.payload
        verification = payload.get("verification") or {}
        return cls(
            expectationId=str(record.expectation_id),
            status=record.status,
            actionType=record.action_type,
            target=record.target,
            createdAt=record.created_at,
            deadline=record.deadline,
            symbolicVerdict=verification.get("symbolic_passed"),
            rolloutVerdict=verification.get("rollout_passed"),
            method=verification.get("method"),
            effectCount=len(payload.get("expected_effects", ())),
            ontologyVersion=str(payload.get("ontology_version", "")),
        )


@strawberry.type
class HealthType:
    """API の疎通確認。

    Attributes:
        ontologyVersion: 稼働中のオントロジー版。
        storeReachable: 知識グラフに到達できるか。
    """

    ontologyVersion: str  # noqa: N815
    storeReachable: bool  # noqa: N815


@runtime_checkable
class BeliefReader(Protocol):
    """二時制クエリの契約（:class:`~sbp.knowledge.assertions.AssertionStore` が満たす）。

    具象クラスではなく Protocol を要求するのは、テストで外部 I/O をモックできるようにするため
    （テスト方針「外部 I/O は Protocol でモック」）。``isinstance`` で具象を要求すると、API の
    テストが Compose 必須になる。
    """

    def query(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        as_of: datetime | None = None,
        valid_at: datetime | None = None,
        modality: Modality | None = None,
        min_confidence: float | None = None,
    ) -> Sequence[AssertionRow]:
        """二時制クエリ。

        Args:
            subject: 主語 IRI。
            predicate: 述語 IRI。
            as_of: 取引時刻。
            valid_at: 有効時刻。
            modality: 様相。
            min_confidence: 確信度の下限。
        """
        ...


class BeliefSource:
    """信念の読み出し元（``knowledge`` の二時制 API を包む）。

    API 層が SPARQL を組み立てないための境界である（SPARQL 文字列は ``knowledge`` の内側に
    閉じる、という knowledge ルール）。
    """

    def __init__(self, store: BeliefReader, *, ontology_version: str) -> None:
        """Args:
        store: 二時制クエリを提供するもの。
        ontology_version: 稼働中のオントロジー版。
        """
        self._store = store
        self._ontology_version = ontology_version

    @property
    def ontology_version(self) -> str:
        """稼働中のオントロジー版。"""
        return self._ontology_version

    def beliefs(
        self,
        *,
        subject: str | None,
        predicate: str | None,
        as_of: datetime | None,
        valid_at: datetime | None,
        modality: Modality,
        min_confidence: float,
    ) -> Sequence[AssertionRow]:
        """二時制クエリを実行する。

        Args:
            subject: 主語 IRI。
            predicate: 述語 IRI。
            as_of: 取引時刻（「いつ時点でそう信じていたか」）。
            valid_at: 有効時刻（「いつの話か」）。
            modality: 様相。
            min_confidence: 確信度の下限。
        """
        return self._store.query(
            subject=subject,
            predicate=predicate,
            as_of=as_of,
            valid_at=valid_at,
            modality=modality,
            min_confidence=min_confidence,
        )

    def reachable(self) -> bool:
        """ストアに到達できるか。"""
        try:
            self.beliefs(
                subject=None,
                predicate=None,
                as_of=None,
                valid_at=None,
                modality=Modality.INFERRED,
                min_confidence=1.1,  # 何も返らない条件で疎通だけ確認する
            )
        except Exception:
            return False
        return True


@strawberry.type
class ConceptProposalType:
    """概念提案 1 件（M8-19、ADR-0015）。

    **判断材料を数値で返す。** ``supportCount``（支持する標本数）と ``coverage``（既存概念での
    被覆率）は提案した時点の値で、設計書 §11.2 の curation 検査が使う——「標本 10 件未満は
    偶然のクラスタと区別できない」「被覆率 50% 超なら既存概念の調整で足りる」。

    **潜在の重心は返さない**（ADR-0015）。潜在索引のもので、ここに置くと再接地で食い違う。

    Attributes:
        proposalId: 識別子。
        name: ローカル名。
        definition: 一文の定義（成立条件）。
        superclass: 上位概念のローカル名。
        status: 採否（``proposed`` / ``accepted`` / ``revised`` / ``rejected``）。
        rejectedAlternatives: 既存概念で説明しようとして足りなかった点。**空では提案できない。**
        supportCount: 支持する標本数（提案時に導出した値）。
        coverage: 既存概念での被覆率（同）。
        signalCount: 根拠になった signal の数。
        proposedAt: 提案時刻（未記録なら ``null``）。
        curationNote: 採否の理由（未判断なら空）。
    """

    proposalId: str  # noqa: N815 — GraphQL は camelCase
    name: str
    definition: str
    superclass: str
    status: str
    rejectedAlternatives: list[str]  # noqa: N815
    supportCount: int  # noqa: N815
    coverage: float
    signalCount: int  # noqa: N815
    proposedAt: datetime | None  # noqa: N815
    curationNote: str  # noqa: N815

    @classmethod
    def from_record(cls, record: ConceptProposalRecord) -> ConceptProposalType:
        """作業項目レコードから作る。

        Args:
            record: 表の行。
        """
        payload = record.payload
        return cls(
            proposalId=str(record.proposal_id),
            name=record.name,
            definition=str(payload.get("definition", "")),
            superclass=record.superclass,
            status=record.status,
            rejectedAlternatives=[str(item) for item in payload.get("rejected_alternatives", ())],
            supportCount=record.support_count,
            coverage=record.coverage,
            signalCount=len(payload.get("signals", ())),
            proposedAt=record.proposed_at,
            curationNote=str(payload.get("curation_note", "")),
        )


@strawberry.type
class ContributionType:
    """信念 1 点の三源の寄与（対数尤度比の絶対値。生の大きさ。ADR-0020）。

    Attributes:
        observed: 物理観測（プローブ）の寄与。
        record: 業務記録の寄与。
        plan: 計画の寄与。
        dominant: 最も事後を動かした源（``observed`` / ``record`` / ``plan``）。全部 0 なら
            ``None``。
        prior: 事前確率。
        sourceEventId: 起因したイベントの識別子。
        excludedRecords: 監査隔離で除外した記録の数（不変条件 6）。
    """

    observed: float
    record: float
    plan: float
    dominant: str | None
    prior: float
    sourceEventId: str  # noqa: N815 — GraphQL は camelCase
    excludedRecords: int  # noqa: N815


@strawberry.type
class EvidencePointType:
    """信念の時系列の 1 点。

    Attributes:
        assertionId: アサーションの識別子。
        at: 信念の有効開始。
        probability: 較正済みの確率。
        origin: 出どころ（``update`` = 前向きフィルタ / ``coupling`` = 実体間の結合）。
        contribution: 三源の寄与。**``None`` は「記録されていない」**（寄与 0 ではない）。
    """

    assertionId: str  # noqa: N815
    at: datetime
    probability: float
    origin: str
    contribution: ContributionType | None


@strawberry.type
class PlannedSpanType:
    """区間と重なる計画。

    Attributes:
        start: 計画の始端。
        end: 計画の終端（継続中は ``None``）。
        adherence: 遵守率の事前。
    """

    start: datetime
    end: datetime | None
    adherence: float


@strawberry.type
class BeliefEvidenceType:
    """実体 × フルーエントの証拠の三源並置（設計書 §8.2、ADR-0020）。

    **どちらが正しいかは示さない。** 並べるだけで、判断は人がする。

    Attributes:
        entity: 実体の IRI。
        fluent: フルーエント型の IRI。
        start: 区間の始端。
        end: 区間の終端（含まない）。
        points: 信念の点（時刻順）。
        planned: 区間と重なる計画（開始順）。
        contributionsTruncatedAfter: 寄与の読み出しが上限で切れたとき、最後に読めた寄与の時刻。
            **これより後の点の「寄与なし」は「記録されていない」ではなく「読めていない」**
            （``None`` なら切れていない）。
    """

    entity: str
    fluent: str
    start: datetime
    end: datetime
    points: list[EvidencePointType]
    planned: list[PlannedSpanType]
    contributionsTruncatedAfter: datetime | None = None  # noqa: N815


@strawberry.type
class ActionTypeType:
    """アクション契約の要約（UI の承認フォームが読む。M8-22、ADR-0021）。

    Attributes:
        actionType: アクション型 IRI。
        targets: 対象の型 IRI。
        riskClass: リスク級（``low`` / ``medium`` / ``high``）。
        approval: 承認の要否（``always`` / ``if_severity_high`` / ``never``）。
        approvalRole: 承認者のロール IRI。
        preconditions: 面ごとの前提の説明（``symbolic`` / ``physical`` / ``normative``）。
        effects: 面ごとの期待する効果（説明と期限）。
        compensation: 補償アクション型 IRI（**表示するだけ**。実行の入口は無い。H-08）。
        rateLimit: 同一対象への反復の制限（無ければ ``None``）。
    """

    actionType: str  # noqa: N815 — GraphQL は camelCase
    targets: str
    riskClass: str  # noqa: N815
    approval: str
    approvalRole: str  # noqa: N815
    preconditions: list[PreconditionFaceType]
    effects: list[EffectFaceType]
    compensation: str
    rateLimit: RateLimitType | None  # noqa: N815


@strawberry.type
class PreconditionFaceType:
    """面ごとの前提。

    Attributes:
        face: 面（``symbolic`` / ``physical`` / ``normative``）。
        descriptions: 前提の説明。
    """

    face: str
    descriptions: list[str]


@strawberry.type
class EffectType:
    """期待する効果 1 件。

    Attributes:
        description: 効果の説明。
        within: 期限（ISO 8601 の期間）。
    """

    description: str
    within: str


@strawberry.type
class EffectFaceType:
    """面ごとの効果。

    Attributes:
        face: 面（``business`` / ``physical``）。
        effects: 期待する効果。
    """

    face: str
    effects: list[EffectType]


@strawberry.type
class RateLimitType:
    """同一対象への反復の制限。

    Attributes:
        window: 時間窓（ISO 8601 の期間）。
        max: 窓内に許す実行回数。
    """

    window: str
    max: int


@strawberry.type
class DecisionStatusType:
    """人の判断 1 件の状態（M8-22、ADR-0021）。

    **失敗した判断は自動では再試行されない。** 理由を読み、新しい判断として再投入する。

    Attributes:
        decisionId: 判断の識別子。
        kind: 種類（``resolution`` / ``curation`` / ``action`` / ``answer``）。
        subject: 対象（不一致 ID・提案 ID・実体 IRI）。
        decidedBy: 判断した主体の IRI。
        state: ``pending`` / ``applied`` / ``failed``。
        receivedAt: 受け付けた時刻。
        appliedAt: 適用できた時刻。
        failedAt: 失敗した時刻。
        error: 失敗の理由（人が読む。整形しない）。
        artifacts: 何を適用したかの要約（JSON 文字列）。
        attempts: 適用を試みた回数。
    """

    decisionId: str  # noqa: N815
    kind: str
    subject: str
    decidedBy: str  # noqa: N815
    state: str
    receivedAt: datetime  # noqa: N815
    appliedAt: datetime | None  # noqa: N815
    failedAt: datetime | None  # noqa: N815
    error: str | None
    artifacts: str
    attempts: int


@strawberry.type
class Query:
    """読み取り専用のクエリ根（設計書 §10.1）。"""

    @strawberry.field(description="アクション契約の要約（承認フォームが読む）")
    def actionTypes(  # noqa: N802 — GraphQL は camelCase
        self, info: strawberry.Info
    ) -> list[ActionTypeType]:
        """アクション契約の要約を返す（M8-22、ADR-0021）。

        契約が唯一の正で、UI に写しを持たせない。**要求者を必須にする**（既定は拒否）。

        Args:
            info: strawberry の文脈（``actions`` に読み出し関数を持つ）。

        Raises:
            AuthorizationError: 要求者が設定されていない場合。
            ValueError: 読み出しが設定されていない場合（空を返さない。所見 32）。
        """
        principal = info.context.get("principal")
        if principal is None:
            raise AuthorizationError(
                "ABAC が設定されていない。create_app に principal_resolver を渡すこと"
                "（既定は拒否、設計書 §11）"
            )
        reader = info.context.get("actions")
        if reader is None:
            raise ValueError(
                "アクション契約の読み出しが設定されていない（create_app に action_reader を渡す）"
            )
        summaries = summarize(reader())
        log.info("api.action_types_queried", principal=principal.subject, rows=len(summaries))
        return [
            ActionTypeType(
                actionType=item.action_type,
                targets=item.targets,
                riskClass=item.risk_class,
                approval=item.approval,
                approvalRole=item.approval_role,
                preconditions=[
                    PreconditionFaceType(face=face, descriptions=list(descriptions))
                    for face, descriptions in item.preconditions.items()
                ],
                effects=[
                    EffectFaceType(
                        face=face,
                        effects=[
                            EffectType(description=description, within=within)
                            for description, within in effects
                        ],
                    )
                    for face, effects in item.effects.items()
                ],
                compensation=item.compensation,
                rateLimit=(
                    None
                    if item.rate_limit is None
                    else RateLimitType(window=item.rate_limit[0], max=item.rate_limit[1])
                ),
            )
            for item in summaries
        ]

    @strawberry.field(description="人の判断の状態（適用済み・失敗と理由・待ち）を主語で引く")
    def decisions(
        self,
        info: strawberry.Info,
        subject: str,
        kinds: list[str] | None = None,
        limit: int = _DEFAULT_QUEUE_LIMIT,
    ) -> list[DecisionStatusType]:
        """判断の状態を返す（M8-22、ADR-0021）。

        Args:
            info: strawberry の文脈（``decisions`` に読み出し関数を持つ）。
            subject: 主語（不一致 ID・提案 ID・実体 IRI）。
            kinds: 種類で絞る（``resolution`` / ``curation`` / ``action`` / ``answer``）。
            limit: 返す件数の上限。

        Raises:
            AuthorizationError: 要求者が設定されていない場合。
            ValueError: ``limit`` が範囲外、未知の種類、または読み出しが未設定の場合。
        """
        principal = info.context.get("principal")
        if principal is None:
            raise AuthorizationError(
                "ABAC が設定されていない。create_app に principal_resolver を渡すこと"
                "（既定は拒否、設計書 §11）"
            )
        if not 1 <= limit <= _MAX_QUEUE_LIMIT:
            raise ValueError(f"limit は 1〜{_MAX_QUEUE_LIMIT}")
        reader = info.context.get("decisions")
        if reader is None:
            raise ValueError(
                "判断の読み出しが設定されていない（create_app に decision_reader を渡す）"
            )
        try:
            parsed = tuple(DecisionKind(kind) for kind in (kinds or ()))
        except ValueError as exc:
            raise ValueError(f"未知の判断の種類: {exc}") from exc
        rows = reader(subject=subject, kinds=parsed, limit=limit)
        log.info(
            "api.decisions_queried",
            principal=principal.subject,
            subject=subject,
            rows=len(rows),
        )
        return [
            DecisionStatusType(
                decisionId=row.decision_id,
                kind=row.kind.value,
                subject=row.subject,
                decidedBy=row.decided_by,
                state=row.state,
                receivedAt=row.received_at,
                appliedAt=row.applied_at,
                failedAt=row.failed_at,
                error=row.error,
                artifacts=json.dumps(row.artifacts, ensure_ascii=False, sort_keys=True),
                attempts=row.attempts,
            )
            for row in rows
        ]

    @strawberry.field(description="証拠の三源並置（観測・記録・計画の寄与の時系列と計画）")
    def beliefEvidence(  # noqa: N802 — GraphQL は camelCase
        self,
        info: strawberry.Info,
        entity: str,
        fluent: str,
        start: datetime,
        end: datetime,
    ) -> BeliefEvidenceType:
        """実体 × フルーエント × 区間の三源並置を返す（M8-17、ADR-0020）。

        **ABAC を通す。** 信念と同じ文（``inferred`` / ``planned``）を ``visible_rows`` で絞り、
        見えない文の寄与は点として出さない。寄与の行だけ読めても、文が見えなければ出さない。

        Args:
            info: strawberry の文脈（``beliefs`` と ``evidence`` を持つ）。
            entity: 実体の IRI。
            fluent: フルーエント型の IRI。
            start: 区間の始端（含む）。
            end: 区間の終端（含まない）。

        Raises:
            AuthorizationError: 要求者が設定されていない場合（既定は拒否）。
            ValueError: 区間が空、または寄与の読み出しが未設定の場合。
        """
        principal = info.context.get("principal")
        if principal is None:
            raise AuthorizationError(
                "ABAC が設定されていない。create_app に principal_resolver を渡すこと"
                "（既定は拒否、設計書 §11）"
            )
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start / end はタイムゾーン付き（不変条件 8。naive は受けない）")
        if end <= start:
            raise ValueError("区間は start < end（右開）")
        reader = info.context.get("evidence")
        if reader is None:
            raise ValueError(
                "寄与の読み出しが設定されていない（create_app に evidence_reader を渡す）"
            )
        source: BeliefSource = info.context["beliefs"]

        def visible(modality: Modality) -> tuple[AssertionRow, ...]:
            rows = source.beliefs(
                subject=entity,
                predicate=fluent,
                as_of=None,
                valid_at=None,
                modality=modality,
                min_confidence=_DEFAULT_MIN_CONFIDENCE,
            )
            return visible_rows(principal, rows)

        inferred = visible(Modality.INFERRED)
        planned = visible(Modality.PLANNED)
        contributions = reader(entity, fluent, start, end)
        truncated_after = contributions[-1].at if len(contributions) >= SERIES_LIMIT else None
        points, spans = build_evidence(inferred, planned, contributions, start=start, end=end)
        log.info(
            "api.belief_evidence_queried",
            principal=principal.subject,
            entity=entity,
            fluent=fluent,
            points=len(points),
            recorded=sum(1 for point in points if point.contribution is not None),
            planned=len(spans),
        )
        return BeliefEvidenceType(
            entity=entity,
            fluent=fluent,
            start=start,
            end=end,
            points=[
                EvidencePointType(
                    assertionId=str(point.assertion_id),
                    at=point.at,
                    probability=point.probability,
                    origin=point.origin,
                    contribution=(
                        None
                        if point.contribution is None
                        else ContributionType(
                            observed=point.contribution.observed,
                            record=point.contribution.record,
                            plan=point.contribution.plan,
                            dominant=point.contribution.dominant,
                            prior=point.contribution.prior,
                            sourceEventId=point.contribution.source_event_id,
                            excludedRecords=point.contribution.excluded_records,
                        )
                    ),
                )
                for point in points
            ],
            planned=[
                PlannedSpanType(start=span.start, end=span.end, adherence=span.adherence)
                for span in spans
            ],
            contributionsTruncatedAfter=truncated_after,
        )

    # GraphQL のリゾルバは引数がそのまま並ぶため位置引数が多くなる（PLR0917 は構造上避けられ
    # ない。キーワード専用にすると GraphQL の引数として公開されない）
    @strawberry.field(description="実体の信念を二時制・様相・確信度で絞って返す")
    def beliefs(  # noqa: PLR0917
        self,
        info: strawberry.Info,
        subject: str | None = None,
        predicate: str | None = None,
        asOf: datetime | None = None,  # noqa: N803 — GraphQL は camelCase
        validAt: datetime | None = None,  # noqa: N803
        modality: Modality = Modality.INFERRED,
        minConfidence: float = _DEFAULT_MIN_CONFIDENCE,  # noqa: N803
    ) -> list[BeliefType]:
        """信念を返す。

        ``modality`` の既定を ``inferred`` にしてあるのは、「基盤が推定した信念」を既定の答えと
        するためである。全様相を混ぜて返すと、計画（``planned``）と観測（``observed``）と推定が
        同じ列に並び、呼び出し側がそれを区別せずに使う。

        Args:
            info: strawberry の文脈（``BeliefSource`` を持つ）。
            subject: 主語 IRI。
            predicate: 述語 IRI。
            asOf: 取引時刻。
            validAt: 有効時刻。
            modality: 様相。
            minConfidence: 確信度の下限。
        """
        source: BeliefSource = info.context["beliefs"]
        principal = info.context.get("principal")
        if principal is None:
            # 既定は拒否（M7-06）。ABAC を設定せずに信念を読める経路を作らない——「開発中だから
            # 全部見せる」が本番に残ると、拠点間の情報分離も人に関する遮蔽も効かなくなる
            raise AuthorizationError(
                "ABAC が設定されていない。create_app に principal_resolver を渡すこと"
                "（既定は拒否、設計書 §11）"
            )
        rows = source.beliefs(
            subject=subject,
            predicate=predicate,
            as_of=asOf,
            valid_at=validAt,
            modality=Modality(modality.value),
            min_confidence=minConfidence,
        )
        permitted = visible_rows(principal, rows)
        log.info(
            "api.beliefs_queried",
            principal=principal.subject,
            subject=subject,
            predicate=predicate,
            modality=modality.value,
            as_of=None if asOf is None else asOf.isoformat(),
            valid_at=None if validAt is None else validAt.isoformat(),
            rows=len(permitted),
            filtered=len(rows) - len(permitted),
        )
        return [BeliefType.from_row(row) for row in permitted]

    @strawberry.field(description="不一致キューを深刻度の高い順に返す")
    def discrepancies(
        self,
        info: strawberry.Info,
        kind: int | None = None,
        includeTerminal: bool = False,  # noqa: N803 — GraphQL は camelCase
        limit: int = _DEFAULT_QUEUE_LIMIT,
    ) -> list[DiscrepancyType]:
        """不一致キューを返す（設計書 §8.2、ADR-0013）。

        **既定で終端状態を除く。** 解決済みを毎回返すと、キューの上位が履歴で埋まる。

        **ABAC を通す。** 不一致は実体の状態についての判断であり、信念と同じ機密度を持つ
        ——拠点の分離も人に関する遮蔽も、ここで外すと迂回路になる。要求者が無ければ拒否する。

        Args:
            info: strawberry の文脈（``discrepancies`` に読み出し関数を持つ）。
            kind: 五類型で絞る。
            includeTerminal: 終端状態（``Learned`` / ``Suppressed``）も含めるか。
            limit: 返す件数の上限。

        Raises:
            AuthorizationError: 要求者が設定されていない場合（既定は拒否）。
            ValueError: ``limit`` が範囲外、または ``kind`` が 1〜5 でない場合。
        """
        principal = info.context.get("principal")
        if principal is None:
            raise AuthorizationError(
                "ABAC が設定されていない。create_app に principal_resolver を渡すこと"
                "（既定は拒否、設計書 §11）"
            )
        if not 1 <= limit <= _MAX_QUEUE_LIMIT:
            raise ValueError(f"limit は 1〜{_MAX_QUEUE_LIMIT}")
        if kind is not None and not 1 <= kind <= 5:
            raise ValueError("kind は 1〜5（五類型）")
        reader = info.context.get("discrepancies")
        if reader is None:
            raise ValueError(
                "不一致の読み出しが設定されていない（create_app に discrepancy_reader を渡す）"
            )
        records = reader(kind=kind, include_terminal=includeTerminal, limit=limit)
        log.info(
            "api.discrepancies_queried",
            principal=principal.subject,
            kind=kind,
            include_terminal=includeTerminal,
            rows=len(records),
        )
        return [DiscrepancyType.from_record(record) for record in records]

    @strawberry.field(description="期待ボードを期限の近い順に返す")
    def expectations(
        self,
        info: strawberry.Info,
        target: str | None = None,
        includeTerminal: bool = False,  # noqa: N803 — GraphQL は camelCase
        limit: int = _DEFAULT_QUEUE_LIMIT,
    ) -> list[ExpectationType]:
        """期待ボードを返す（設計書 §8.5、M8-18）。

        **既定で終端状態を除く。** 検証済みの履歴でボードが埋まらないようにする。

        **ABAC を通す。** 期待は実体の状態についての予測であり、信念と同じ機密度を持つ。

        Args:
            info: strawberry の文脈（``expectations`` に読み出し関数を持つ）。
            target: 対象実体で絞る。
            includeTerminal: 終端状態（``Learned``）も含めるか。
            limit: 返す件数の上限。

        Raises:
            AuthorizationError: 要求者が設定されていない場合（既定は拒否）。
            ValueError: ``limit`` が範囲外、または読み出しが未設定の場合。
        """
        principal = info.context.get("principal")
        if principal is None:
            raise AuthorizationError(
                "ABAC が設定されていない。create_app に principal_resolver を渡すこと"
                "（既定は拒否、設計書 §11）"
            )
        if not 1 <= limit <= _MAX_QUEUE_LIMIT:
            raise ValueError(f"limit は 1〜{_MAX_QUEUE_LIMIT}")
        reader = info.context.get("expectations")
        if reader is None:
            raise ValueError(
                "期待の読み出しが設定されていない（create_app に expectation_reader を渡す）"
            )
        records = reader(target=target, include_terminal=includeTerminal, limit=limit)
        log.info(
            "api.expectations_queried",
            principal=principal.subject,
            target=target,
            include_terminal=includeTerminal,
            rows=len(records),
        )
        return [ExpectationType.from_record(record) for record in records]

    @strawberry.field(description="curation 待ちの概念提案を支持の多い順に返す")
    def proposals(
        self,
        info: strawberry.Info,
        includeCurated: bool = False,  # noqa: N803 — GraphQL は camelCase
        limit: int = _DEFAULT_QUEUE_LIMIT,
    ) -> list[ConceptProposalType]:
        """概念提案の curation 待ち行列を返す（設計書 §8.6 / §11.2、M8-19）。

        **支持の多い順に返す。** 偶然のクラスタと区別できない提案を先頭に置くと、人の判断の
        時間が弱い提案に使われる。

        **既定で採否が入ったものを除く。** 判断済みの履歴で画面が埋まらないようにする。

        **ABAC を通す。** 提案は潜在のクラスタ構造についての情報であり、語彙の拡張提案である
        ——どちらも機密度を持つ。

        Args:
            info: strawberry の文脈（``proposals`` に読み出し関数を持つ）。
            includeCurated: 採否が入ったものも含めるか。
            limit: 返す件数の上限。

        Raises:
            AuthorizationError: 要求者が設定されていない場合（既定は拒否）。
            ValueError: ``limit`` が範囲外、または読み出しが未設定の場合。
        """
        principal = info.context.get("principal")
        if principal is None:
            raise AuthorizationError(
                "ABAC が設定されていない。create_app に principal_resolver を渡すこと"
                "（既定は拒否、設計書 §11）"
            )
        if not 1 <= limit <= _MAX_QUEUE_LIMIT:
            raise ValueError(f"limit は 1〜{_MAX_QUEUE_LIMIT}")
        reader = info.context.get("proposals")
        if reader is None:
            raise ValueError(
                "概念提案の読み出しが設定されていない（create_app に proposal_reader を渡す）"
            )
        records = reader(include_curated=includeCurated, limit=limit)
        log.info(
            "api.proposals_queried",
            principal=principal.subject,
            include_curated=includeCurated,
            rows=len(records),
        )
        return [ConceptProposalType.from_record(record) for record in records]

    @strawberry.field(description="API とストアの疎通")
    def health(self, info: strawberry.Info) -> HealthType:
        """疎通確認を返す。

        Args:
            info: strawberry の文脈。
        """
        source: BeliefSource = info.context["beliefs"]
        return HealthType(
            ontologyVersion=source.ontology_version, storeReachable=source.reachable()
        )


def build_schema() -> strawberry.Schema:
    """GraphQL スキーマを作る。

    mutation を持たないのが要点である（設計書 §9.2 の承認フローを迂回させない）。
    """
    return strawberry.Schema(query=Query)


AsOf = Annotated[datetime | None, "取引時刻（この時点で信じていたこと）"]
ValidAt = Annotated[datetime | None, "有効時刻（いつの話か）"]
