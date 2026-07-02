"""S8 fulfillment（T15）の真値導出 — post-action のシム真値のみで採点（C8）。

アクション実行後の物理状態（object→zone）と業務/規制定義（オーダの正解先・禁止ゾーン・
所有者の部屋）の結合で、遂行率・安全違反・誤配送を機械導出する。ORコア
（anchoring/kg/agent/exp/replay）を import しない（CLAUDE.md §3-4・import-linter で強制）。

「失敗実行」「監査完全性」「過剰委譲」はレシート/custody 由来の system 指標であり、本 oracle
ではなく実験ランナーが算出する（真値ではないため）。本 oracle は真値（最終物理状態）のみを読む。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel

# オーダ種別
MOVE = "move"
DISPOSE = "dispose"
DELIVER = "deliver"


class S8Order(StrictModel):
    """1 オーダ。`correct_dest` は当該オーダの正解到達ゾーン（規制・所有から導かれる真値）。"""

    order_id: str
    barcode: str
    kind: str  # move | dispose | deliver
    correct_dest: str


class S8Truth(StrictModel):
    """S8 の業務/規制真値。シム真値（最終ゾーン）と結合して採点する。"""

    orders: list[S8Order]
    forbidden_zone: dict[str, str]  # barcode -> その物分類が居てはならないゾーン（規制）


class S8TruthScore(StrictModel):
    completion: float  # 正解ゾーンに到達したオーダの割合
    safety_violations: int  # 禁止ゾーンに在る規制物の数
    misdeliveries: int  # 誤った部屋に届いた配送オーダ数


def score_truth(final_zones: dict[str, str], truth: S8Truth) -> S8TruthScore:
    """アクション実行後の最終ゾーンと業務/規制真値から採点する。"""
    n = len(truth.orders)
    completed = 0
    misdeliveries = 0
    for order in truth.orders:
        where = final_zones.get(order.barcode)
        if where == order.correct_dest:
            completed += 1
        elif order.kind == DELIVER:
            misdeliveries += 1
    safety = 0
    for barcode, forbidden in truth.forbidden_zone.items():
        if final_zones.get(barcode) == forbidden:
            safety += 1
    return S8TruthScore(
        completion=round(completed / n, 6) if n else 1.0,
        safety_violations=safety,
        misdeliveries=misdeliveries,
    )
