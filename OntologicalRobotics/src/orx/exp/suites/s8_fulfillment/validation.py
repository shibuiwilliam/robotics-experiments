"""S8 送信基準（submission criteria）— アクションの世界グラフ前提条件検証。

OR-full の検証器はオントロジー蒸留（OntologyView）に対し ①能力 ②規範 ③所有権 を検査し、
満たさなければ rejected（理由付き）を返す。ベースラインは検証用の知識を持たないため
`always_valid`（不正アクションを実行してしまう＝失敗様態の作り込み）。
"""

from __future__ import annotations

from orx.skills.action import ValidationResult
from orx.skills.server import SkillRequest

from .generator import OntologyView


def or_validator(view: OntologyView):
    """OR-full の送信基準検証器（能力・規範・所有権をグラフ蒸留に照合）。"""

    def validate(request: SkillRequest) -> ValidationResult:
        bc = request.target_barcode
        # ① 能力契約: 重量物は必要可搬重量を満たす機体のみ
        need = view.required_payload.get(bc)
        if need is not None:
            have = view.robot_payload.get(request.robot_id, 0.0)
            if have + 1e-9 < need:
                return ValidationResult(
                    ok=False,
                    reason=f"能力不足: {request.robot_id} の可搬 {have}kg < 必要 {need}kg",
                )
        # ② 規範: 規制物は禁止ゾーンへ送れない
        cls = view.item_class.get(bc)
        if cls is not None:
            forbidden = view.forbidden_zone_class.get(cls)
            if forbidden is not None and request.dest_zone == forbidden:
                return ValidationResult(ok=False, reason=f"規範違反: {cls} は {forbidden} 通過禁止")
        # ③ 所有権: 配送は所有者の部屋へのみ
        room = view.owner_room.get(bc)
        if room is not None and request.dest_zone != room:
            return ValidationResult(
                ok=False, reason=f"所有権: {bc} は所有者の部屋 {room} へのみ配送可"
            )
        return ValidationResult(ok=True)

    return validate
