"""ODRL 相当のポリシー検査（ADR-0002）：目的限定・保持期間・再共有禁止。

poc_plan.md 5.5「ポリシー：ODRLで『目的限定（到着予定の共有は入荷計画のみに使用）』
『保持期間』『再共有禁止』を記述し、EDCコネクタで強制」を、最小 HTTP コネクタ側で
検査するロジックとして実装する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


class PolicyViolation(Exception):
    """ポリシー違反（例：宣言された目的が許可リストに無い）。"""


@dataclass(frozen=True)
class OdrlPolicy:
    """1件の交換対象（例：到着予定）に紐づくポリシー。"""

    permitted_purposes: frozenset[str]
    retention_days: int
    allow_resharing: bool = False

    def __post_init__(self) -> None:
        if not self.permitted_purposes:
            raise ValueError("permitted_purposes は最低1件必要です")
        if self.retention_days <= 0:
            raise ValueError("retention_days は正の整数である必要があります")


def check_purpose(policy: OdrlPolicy, declared_purpose: str) -> None:
    """目的限定を検査する。許可リストに無ければ `PolicyViolation` を送出する。"""
    if declared_purpose not in policy.permitted_purposes:
        raise PolicyViolation(
            f"目的限定違反：宣言された目的 '{declared_purpose}' は許可リスト "
            f"{sorted(policy.permitted_purposes)} に含まれない"
        )


def check_resharing(policy: OdrlPolicy, requester_will_reshare: bool) -> None:
    """再共有禁止を検査する。ポリシーが禁止しているのに要求側が再共有を宣言した場合は違反。"""
    if requester_will_reshare and not policy.allow_resharing:
        raise PolicyViolation("再共有禁止違反：このポリシーは再共有を許可していない")


def retention_cutoff(policy: OdrlPolicy, now: datetime) -> datetime:
    """保持期間を過ぎたデータを除外するためのカットオフ時刻を返す。

    `now - retention_days` より古い（valid_from/generated_at が古い）交換対象は
    保持期間切れとして応答から除外する（コネクタ側の呼び出しで使う）。
    """
    return now - timedelta(days=policy.retention_days)
