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

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Protocol, runtime_checkable

import strawberry

from sbp.api.authorization import AuthorizationError, visible_rows
from sbp.core.logging import get_logger
from sbp.core.modality import Modality

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.knowledge.assertions import AssertionRow
else:  # Protocol の注釈を実行時にも解決できるようにする
    Sequence = __import__("collections.abc", fromlist=["Sequence"]).Sequence
    AssertionRow = object

__all__ = ["BeliefReader", "BeliefSource", "BeliefType", "Query", "build_schema"]

log = get_logger(__name__)

_DEFAULT_MIN_CONFIDENCE = 0.0
"""確信度の下限の既定。0 は「全部返す」——ただし呼び出し側が閾値を意識できるよう引数で見せる。"""


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
class Query:
    """読み取り専用のクエリ根（設計書 §10.1）。"""

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
