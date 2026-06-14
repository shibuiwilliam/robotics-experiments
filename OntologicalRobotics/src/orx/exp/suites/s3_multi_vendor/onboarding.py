"""S3 オンボーディング・コスト（H1）— ベンダーD参入の統合コスト（決定的）。

共通オントロジー（ハブ＆スポーク）があると、新ベンダーのスキーマ・フィールドのうち
既知の意味型に当たるものは自動写像でき、手修正は残差のみ。B1（ハブ無し）は全行手書き。
T5 の「人手修正行数」指標を業務文脈で再表現（射程: 表現上限/ceiling）。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel


class OnboardingCost(StrictModel):
    vendor_fields: int
    auto_mapped: int  # OR-full がハブ写像で自動解決したフィールド数
    or_full_manual_lines: int  # 残差（手修正）
    b1_manual_lines: int  # ハブ無し → 全フィールド手書き


def onboarding_cost(vendor_fields: list[str], known_semantic_fields: list[str]) -> OnboardingCost:
    known = set(known_semantic_fields)
    auto = [f for f in vendor_fields if f in known]
    manual = [f for f in vendor_fields if f not in known]
    return OnboardingCost(
        vendor_fields=len(vendor_fields),
        auto_mapped=len(auto),
        or_full_manual_lines=len(manual),
        b1_manual_lines=len(vendor_fields),
    )
