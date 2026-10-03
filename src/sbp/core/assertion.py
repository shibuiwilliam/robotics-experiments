"""アサーション（信念文）— 基盤が所有する唯一のもの（設計書 §3.5、付録 A.1）。

不変条件 1 により、全アサーションは
``(subject, predicate, object, valid_from, valid_to, tx_from, tx_to, modality, belief, provenance)``
と ``ontology_version`` / ``schema_version`` を欠かさない。

不変条件 2 により追記専用。訂正は旧文の ``tx_to`` を閉じて新文を追加する（:meth:`Assertion.close_tx`
は**新しいオブジェクトを返す**。既存文を書き換える API は作らない）。
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Self
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from sbp.core.belief import Belief
from sbp.core.ids import Iri
from sbp.core.modality import Modality
from sbp.core.time import Interval, UtcDatetime

__all__ = ["SCHEMA_VERSION", "Assertion", "SemVer"]

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


def _validate_semver(value: str) -> str:
    if not _SEMVER_PATTERN.match(value):
        raise ValueError(f"not a semver string: {value!r}")
    return value


SemVer = Annotated[str, AfterValidator(_validate_semver)]
"""semver 文字列型（``MAJOR.MINOR.PATCH``）。"""

SCHEMA_VERSION: SemVer = "1.0.0"
"""このコードが生成するアサーション・イベントのスキーマ版。"""


class Assertion(BaseModel):
    """二時制・様相・確信度・来歴を備えた信念文。

    有効時間（``valid_*``）は「現実にそうであった期間」、記録時間（``tx_*``）は「基盤がそう
    信じていた期間」。どちらも右開区間 ``[from, to)`` で、``to=None`` は継続中を表す。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: UUID = Field(default_factory=uuid4)
    """アサーションの識別子。訂正時は新 ID の文を追加する（同一 ID を書き換えない）。"""

    # ---- 主張本体 ----
    subject: Iri
    predicate: Iri
    object: Iri | str | float | bool
    """目的語。IRI またはリテラル。IRI の直書きは不変条件 5 違反。"""

    # ---- 有効時間（現実にそうであった期間）----
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None = None

    # ---- 記録時間（基盤がそう信じていた期間）----
    tx_from: UtcDatetime
    tx_to: UtcDatetime | None = None

    # ---- 身分と確信度 ----
    modality: Modality
    belief: Belief

    # ---- 予測のみ ----
    horizon_origin: UtcDatetime | None = None
    lead_time: timedelta | None = None

    # ---- 来歴とメタ ----
    provenance: Iri
    """PROV バンドルの IRI。生成活動・エージェント・モデル版・証拠集合を指す。"""
    source_reliability: Iri | None = None
    situation: Iri | None = None
    scenario: Iri | None = None
    """``hypothetical`` のときのみ必須。シナリオ名前付きグラフの識別子。"""

    ontology_version: SemVer
    schema_version: SemVer = SCHEMA_VERSION

    # ---- 検証 ----

    @model_validator(mode="after")
    def _temporal_intervals_valid(self) -> Self:
        """有効時間・記録時間が右開の非空区間であることを検証する（不変条件 8）。"""
        Interval(self.valid_from, self.valid_to)
        Interval(self.tx_from, self.tx_to)
        return self

    @model_validator(mode="after")
    def _horizon_only_for_predicted(self) -> Self:
        """``horizon_origin`` / ``lead_time`` が ``predicted`` のときのみ存在することを検証する。"""
        has_horizon = self.horizon_origin is not None or self.lead_time is not None
        if self.modality.requires_horizon:
            if self.horizon_origin is None or self.lead_time is None:
                raise ValueError(
                    "predicted assertions require both horizon_origin and lead_time (design §3.5)"
                )
            if self.lead_time <= timedelta(0):
                raise ValueError(f"lead_time must be positive, got {self.lead_time}")
        elif has_horizon:
            raise ValueError(
                f"horizon_origin / lead_time are only valid for modality=predicted, "
                f"got {self.modality}"
            )
        return self

    @model_validator(mode="after")
    def _scenario_matches_modality(self) -> Self:
        """``scenario`` を持つのは ``hypothetical`` だけであることを検証する（不変条件 3）。"""
        if self.modality.requires_scenario:
            if self.scenario is None:
                raise ValueError(
                    "hypothetical assertions must carry a scenario IRI so they stay isolated "
                    "from main belief (invariant 3)"
                )
        elif self.scenario is not None:
            raise ValueError(
                f"scenario is only valid for modality=hypothetical, got {self.modality}"
            )
        return self

    # ---- 派生プロパティ ----

    @property
    def valid_interval(self) -> Interval:
        """有効時間の区間。"""
        return Interval(self.valid_from, self.valid_to)

    @property
    def tx_interval(self) -> Interval:
        """記録時間の区間。"""
        return Interval(self.tx_from, self.tx_to)

    @property
    def is_current(self) -> bool:
        """記録時間が開いている（＝現在の信念である）かどうか。"""
        return self.tx_to is None

    def holds_at(self, instant: UtcDatetime) -> bool:
        """指定時刻に有効（valid-at）かどうか。"""
        return self.valid_interval.contains_instant(instant)

    def known_as_of(self, instant: UtcDatetime) -> bool:
        """指定時刻に基盤がそう信じていた（as-of）かどうか。"""
        return self.tx_interval.contains_instant(instant)

    # ---- 二時制の操作（いずれも新しいオブジェクトを返す）----

    def close_tx(self, at: UtcDatetime) -> Assertion:
        """記録時間を ``at`` で閉じた**新しい**アサーションを返す（不変条件 2）。

        訂正は「旧文の ``tx_to`` を閉じて新文を追加する」で表現する。この関数は閉じた旧文を
        返すだけで、永続化は ``sbp.knowledge`` の責務。

        Args:
            at: 記録時間を閉じる時刻。``tx_from`` より後でなければならない。
        """
        return self.model_copy(update={"tx_to": Interval(self.tx_from, at).end})

    def supersede(self, *, belief: Belief, at: UtcDatetime, provenance: Iri) -> Assertion:
        """同じ主張・同じ有効時間のまま確信度を更新した新文を返す。

        新しい ``assertion_id`` と ``tx_from`` を持つ別の文であり、旧文は呼び出し側が
        :meth:`close_tx` で閉じる（不変条件 2）。

        Args:
            belief: 新しい較正済み確信度。
            at: 新文の ``tx_from``。
            provenance: 新文の来歴バンドル IRI。
        """
        return self.model_copy(
            update={
                "assertion_id": uuid4(),
                "belief": belief,
                "tx_from": at,
                "tx_to": None,
                "provenance": provenance,
            }
        )
