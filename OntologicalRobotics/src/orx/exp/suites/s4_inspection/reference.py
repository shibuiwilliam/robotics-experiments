"""S4 条件別リファレンスソルバ（ADR-014: 機構のオン/オフをシグネチャで分離）。

二つの直交機構を持つ:
- 同一性解決(anchoring): 台帳個体↔知覚個体の ID無し対応付け。
- 信念調停(belief): 矛盾する異常観測を来歴・確信度で調停。

- OR-full      : 同一性ON（位置×署名）＋ 信念ON（確信度加重）。
- OR-no-identity: 同一性OFF → 台帳と観測を結べない（状態クエリ不能）。
- OR-no-belief  : 同一性ON ＋ 信念OFF（単純多数決・確信度無視）→ 矛盾を解けず見逃し。
- OR-sym        : 同一性ON だが署名なし（位置のみ）＋ 信念ON → 位置曖昧で誤対応。
"""

from __future__ import annotations

from orx.exp.suites.s4_inspection.grounding import cos, neg_distance
from orx.exp.suites.s4_inspection.model import S4LedgerEntry, S4Observation

CONDITIONS = ["OR-full", "OR-no-identity", "OR-no-belief", "OR-sym"]

_W_POS = 1.0
_W_SIG = 2.0


def anchor(
    condition: str, obs: S4Observation, ledger: list[S4LedgerEntry]
) -> str | None:
    """知覚個体を台帳資産へ対応付ける（ID無し）。OR-no-identity は None。"""
    if condition == "OR-no-identity":
        return None
    if condition == "OR-sym":
        # 位置（記号/時空間）のみ。署名が無く位置曖昧で誤対応しうる
        return max(ledger, key=lambda e: neg_distance(obs.pos, e.ledger_pos)).asset_id
    # OR-full / OR-no-belief: 位置×署名の融合
    return max(
        ledger,
        key=lambda e: _W_POS * neg_distance(obs.pos, e.ledger_pos)
        + _W_SIG * cos(obs.signature, e.ref_signature),
    ).asset_id


def reconcile(condition: str, readings: list[tuple[bool, float]]) -> bool:
    """グループ化された異常観測から最終異常状態を結論する。"""
    if not readings:
        return False  # 観測が結べていない → 結論不能（正常既定）
    if condition == "OR-no-belief":
        # 確信度・来歴を無視した単純多数決。同数は正常既定（矛盾を解けない）
        anomaly = sum(1 for r, _ in readings if r)
        normal = len(readings) - anomaly
        return anomaly > normal
    # 信念調停: 確信度加重投票（来歴・確信度）
    pro = sum(c for r, c in readings if r)
    con = sum(c for r, c in readings if not r)
    return pro > con
