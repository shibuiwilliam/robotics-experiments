"""ABAC（属性ベースアクセス制御、設計書 §11、M7-06）。

> エージェントの権限はロールと ActionType 単位。エージェントの主張は様相 inferred・来歴 agent
> としてのみ登録可能（設計書 §10.2）。

**権限の軸は 3 つ**（概念型 × 様相 × 拠点）。1 つでも落とすと穴が開く。

- **概念型**: 「稼働状態は見てよいが、人の位置は見てはならない」——概念ごとに機密度が違う。
- **様相**: 「観測は見てよいが仮説シナリオは見てはならない」——``hypothetical`` は検討中の案で
  あり、見えると「決まった」と誤解される。
- **拠点**: 「siteA の担当者が siteB の信念を見ない」——拠点間の情報分離は契約上の要求になりうる。

**既定は拒否。** 許可の列挙に無いものは通さない。「とりあえず全部見せて後で絞る」は、絞り忘れが
そのまま本番に残る。

**人に関する概念は別扱いにする**（不変条件 9、設計書 §11.1）。``Person`` に紐づく概念は、
ロールが明示的に許可されていても**外観や個人特定に繋がる属性を返さない**。ここだけは「許可が
あれば見せる」にしない——許可の設定ミスが個人情報の漏洩になる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sbp.core.errors import SbpError
from sbp.core.logging import get_logger
from sbp.core.modality import Modality

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = [
    "AccessDecision",
    "AuthorizationError",
    "Permission",
    "Principal",
    "PrivacyClass",
    "authorize",
    "require_authorized",
    "site_of_graph",
    "visible_rows",
]

log = get_logger(__name__)


class AuthorizationError(SbpError):
    """認可の拒否。"""


class PrivacyClass(StrEnum):
    """概念の機密度（設計書 §11.1）。"""

    OPERATIONAL = "operational"
    """設備・物品の状態。業務上の通常の情報。"""
    COMMERCIAL = "commercial"
    """在庫・原価など商業的に機密な情報。"""
    PERSONAL = "personal"
    """人に関わる情報。**許可があっても個人特定に繋がる属性は返さない**（不変条件 9）。"""

    @property
    def is_personal(self) -> bool:
        """人に関わるか。"""
        return self is PrivacyClass.PERSONAL


@dataclass(frozen=True, slots=True)
class Permission:
    """許可 1 件（概念型 × 様相 × 拠点）。

    Attributes:
        concept_types: 許可する概念型 IRI。``*`` は「この拠点のすべての概念」。
        modalities: 許可する様相。
        sites: 許可する拠点 ID。
    """

    concept_types: frozenset[str]
    modalities: frozenset[Modality]
    sites: frozenset[str]

    def __post_init__(self) -> None:
        """空の許可を作れないことを検証する。"""
        if not self.concept_types or not self.modalities or not self.sites:
            raise AuthorizationError(
                "a permission must name at least one concept type, modality and site; an empty "
                "axis would either deny everything (useless) or be read as a wildcard (dangerous)"
            )

    def covers(self, *, concept_type: str, modality: Modality, site: str) -> bool:
        """この 3 つ組を許可しているか。

        Args:
            concept_type: 概念型 IRI。
            modality: 様相。
            site: 拠点 ID。
        """
        concept_ok = concept_type in self.concept_types or "*" in self.concept_types
        return concept_ok and modality in self.modalities and site in self.sites


@dataclass(frozen=True, slots=True)
class Principal:
    """要求者（人・エージェント）。

    Attributes:
        subject: 主体の IRI。
        roles: ロール IRI。
        permissions: 許可の集合。
        is_agent: エージェントか（人ではない）。
    """

    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: tuple[Permission, ...] = ()
    is_agent: bool = False

    def __post_init__(self) -> None:
        """主体の IRI が空でないことを検証する。"""
        if not self.subject:
            raise AuthorizationError("a principal must have a subject IRI (for provenance)")


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """認可の結果。

    Attributes:
        allowed: 許可したか。
        reason: 理由（拒否のときは必ず書く。監査で使う）。
        redacted_fields: 返さないことにしたフィールド（個人情報の遮蔽）。
    """

    allowed: bool
    reason: str
    redacted_fields: tuple[str, ...] = ()

    @property
    def is_redacted(self) -> bool:
        """一部を遮蔽したか。"""
        return bool(self.redacted_fields)


_PERSONAL_FIELDS = ("appearance_embedding", "face", "name", "employee_id", "gait")
"""人の同定に繋がるフィールド。**許可の有無に関わらず返さない**（不変条件 9）。"""


def authorize(
    principal: Principal,
    *,
    concept_type: str,
    modality: Modality,
    site: str,
    privacy: PrivacyClass = PrivacyClass.OPERATIONAL,
    requested_fields: Iterable[str] = (),
) -> AccessDecision:
    """3 軸で認可する（設計書 §11）。

    **既定は拒否。** 許可の列挙に該当がなければ通さない。

    Args:
        principal: 要求者。
        concept_type: 概念型 IRI。
        modality: 様相。
        site: 拠点 ID。
        privacy: 概念の機密度。
        requested_fields: 要求されたフィールド。

    Returns:
        認可の結果。
    """
    matched = any(
        permission.covers(concept_type=concept_type, modality=modality, site=site)
        for permission in principal.permissions
    )
    if not matched:
        decision = AccessDecision(
            allowed=False,
            reason=(
                f"{principal.subject} に {concept_type} / {modality.value} / {site} の許可が無い"
                "（既定は拒否）"
            ),
        )
        log.info(
            "api.access_denied",
            principal=principal.subject,
            concept_type=concept_type,
            modality=modality.value,
            site=site,
        )
        return decision

    # エージェントは hypothetical を読めない。検討中の案を「決まった」と扱う経路を作らない
    if principal.is_agent and modality is Modality.HYPOTHETICAL:
        log.info("api.agent_denied_hypothetical", principal=principal.subject, site=site)
        return AccessDecision(
            allowed=False,
            reason=(
                "エージェントは hypothetical を読めない。シナリオは検討中の案であり、"
                "読めると「決まったこと」として扱われる（不変条件 3 の隔離の趣旨）"
            ),
        )

    # 人に関わる概念は、**許可があっても**個人特定に繋がるフィールドを返さない（不変条件 9）
    redacted: tuple[str, ...] = ()
    if privacy.is_personal:
        redacted = tuple(
            field_name for field_name in requested_fields if field_name in _PERSONAL_FIELDS
        )
        if redacted:
            log.info(
                "api.personal_fields_redacted",
                principal=principal.subject,
                concept_type=concept_type,
                redacted=list(redacted),
            )

    return AccessDecision(
        allowed=True,
        reason=(
            f"{principal.subject} は {concept_type} / {modality.value} / {site} を許可されている"
        ),
        redacted_fields=redacted,
    )


def require_authorized(
    principal: Principal,
    *,
    concept_type: str,
    modality: Modality,
    site: str,
    privacy: PrivacyClass = PrivacyClass.OPERATIONAL,
    requested_fields: Iterable[str] = (),
) -> AccessDecision:
    """拒否されたら例外にする（API のハンドラから呼ぶ）。

    Args:
        principal: 要求者。
        concept_type: 概念型 IRI。
        modality: 様相。
        site: 拠点 ID。
        privacy: 機密度。
        requested_fields: 要求されたフィールド。

    Returns:
        許可された場合の決定（遮蔽したフィールドを含む）。

    Raises:
        AuthorizationError: 拒否された場合。
    """
    decision = authorize(
        principal,
        concept_type=concept_type,
        modality=modality,
        site=site,
        privacy=privacy,
        requested_fields=requested_fields,
    )
    if not decision.allowed:
        raise AuthorizationError(decision.reason)
    return decision


_GRAPH_PREFIX = "urn:sbp:g:"
"""主信念のグラフ名（``urn:sbp:g:<modality>:<site>:<ontology_version>``、knowledge ルール）。"""

_SCENARIO_PREFIX = "urn:sbp:scenario:"
"""シナリオの隔離グラフ（``urn:sbp:scenario:<scenario_id>``）。"""


def site_of_graph(graph: str) -> str | None:
    """名前付きグラフから拠点を取り出す。

    判らなければ ``None`` を返し、**呼び出し側は拒否する**（既定は拒否）。拠点が判らない文を
    「どの拠点でもない＝誰でも見てよい」と解釈すると、グラフ名の規約が変わった日に全拠点へ
    漏れる。

    Args:
        graph: 名前付きグラフの IRI。

    Returns:
        拠点 ID、判らなければ ``None``。
    """
    if graph.startswith(_SCENARIO_PREFIX):
        # シナリオは拠点を持たない。拠点軸では判定できないので上位で別に扱う
        return None
    if not graph.startswith(_GRAPH_PREFIX):
        return None
    parts = graph[len(_GRAPH_PREFIX) :].split(":")
    return parts[1] if len(parts) >= 2 and parts[1] else None


def visible_rows[RowT: object](
    principal: Principal,
    rows: Iterable[RowT],
    *,
    privacy_of: Callable[[RowT], PrivacyClass] | None = None,
) -> tuple[RowT, ...]:
    """見てよい行だけを残す（行ごとに 3 軸で判定する）。

    **クエリの後で絞る。** 事前にクエリを書き換えて絞る方式だと、絞り込みの漏れがそのまま
    越権読み出しになる。ここで通した行だけが外に出る、という 1 箇所に集める。

    行は ``subject`` / ``predicate`` / ``object_`` / ``modality`` / ``graph`` を持つこと
    （``AssertionRow``）。概念型の軸は**述語と目的語の両方**で照合する——フルーエントは
    目的語に型として現れ、関係は述語に現れるため、片方だけでは許可を書けない。

    Args:
        principal: 要求者。
        rows: ``AssertionRow`` の列。
        privacy_of: 行の機密度を決める関数（``None`` なら全て ``OPERATIONAL``）。

    Returns:
        見てよい行。
    """
    allowed: list[RowT] = []
    denied = 0
    for row in rows:
        graph = str(getattr(row, "graph", ""))
        site = site_of_graph(graph)
        if site is None:
            denied += 1
            continue
        modality = getattr(row, "modality", Modality.INFERRED)
        privacy = PrivacyClass.OPERATIONAL if privacy_of is None else privacy_of(row)
        candidates = (str(getattr(row, "predicate", "")), str(getattr(row, "object_", "")))
        if any(
            authorize(
                principal,
                concept_type=candidate,
                modality=modality,
                site=site,
                privacy=privacy,
            ).allowed
            for candidate in candidates
            if candidate
        ):
            allowed.append(row)
        else:
            denied += 1
    if denied:
        log.info("api.rows_filtered", principal=principal.subject, denied=denied, kept=len(allowed))
    return tuple(allowed)
