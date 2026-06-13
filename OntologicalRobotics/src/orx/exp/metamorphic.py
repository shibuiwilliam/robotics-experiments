"""メタモルフィック変換（PROJECT.md §7.4 / IMPROVEMENT.md H-4）。

意味保存変換に対する回答不変性を検査し、「オントロジー構造への依拠」と「文字列表層
一致による見かけの成功」を切り分ける。ここでは決定的経路（リファレンスソルバ・CQ）
向けの識別子リネームを提供する（LLM層のメタモルフィックは live で別途）。
"""

from __future__ import annotations

from orx.common.config import WorldConfig


def rename_world_identifiers(world: WorldConfig, suffix: str) -> WorldConfig:
    """全バーコード・全ロットIDに同一サフィックスを付す順序保存リネーム。

    同一サフィックスのためソート順は保存され、シード依存の選択（choose_recall_lot 等）は
    対応する個体を選ぶ。識別子は任意ラベルなので、構造に依拠する系の回答は不変であるべき。
    表層文字列（"BC-" 前置等）に依存していればここで壊れる。
    """
    if not suffix:
        raise ValueError("suffix は非空である必要があります")
    boxes = [
        box.model_copy(
            update={
                "barcode": (box.barcode + suffix) if box.barcode else None,
                "lot": (box.lot + suffix) if box.lot else None,
            }
        )
        for box in world.boxes
    ]
    return world.model_copy(update={"boxes": boxes})
