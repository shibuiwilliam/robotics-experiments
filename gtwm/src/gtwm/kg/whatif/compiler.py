"""WHAT-IF コンパイラ：クエリ中のオントロジー個体を潜在スロット/行動変数に解決する。

ontology.md「EPCIS と WHAT-IF」：「コンパイラは個体→潜在スロット／行動変数の解決に
失敗したら例外を投げ、シミュレーション代替を提案するメッセージを返す」。
"""

from __future__ import annotations

from dataclasses import dataclass

from gtwm.kg.whatif.parser import Intervention, Offset, Triple, WhatIfQuery
from gtwm.sim.env import ZONE_NAMES
from gtwm.sim.registry import category_of, known_gt_ids

# 行動空間は現状エンティティ・プロパティ別にラベル付けされていない
# （`wm/dynamics.py` の契約は action_dim 次元の抽象ベクトルであり、「コンベア速度」
# 「担当人数」のような意味付けを持たない）。このセッション（07）の簡略化として、
# do() の介入は常に行動ベクトルの先頭次元（次元0）への一様上書きとして解釈する。
# 将来、行動空間をエンティティ×プロパティにマッピングする設計（session 09以降/ADR
# 候補）が必要であり、docs/status.md に明記する。
ACTION_DIM_FOR_INTERVENTION = 0

# "current" の基準値。gt:Equipment_* の数値プロパティ（速度等）の現在値を保持する
# プロパティストアがまだ KG に無い（現状 KG は位置・型・状態のみ）。正規化された
# 基準値 1.0 を "current" とみなすプレースホルダーとし、`* current` 等の相対介入は
# この基準に対する倍率・加減算として解釈する。
DEFAULT_CURRENT_VALUE = 1.0


class WhatIfCompileError(ValueError):
    """個体解決失敗時に送出する。メッセージにシミュレーション代替案を含める。"""


@dataclass
class CompiledIntervention:
    entity_gt_id: str
    property: str
    action_dim: int
    action_value: float


@dataclass
class CompiledFilter:
    key: str
    zone_index: int
    zone_gt_id: str


@dataclass
class CompiledQuery:
    vars: list[str]
    horizons: list[Offset]
    filters: list[CompiledFilter]
    interventions: list[CompiledIntervention]
    samples: int
    interval: float | None
    model_version: str | None


def _resolve_zone(value: str) -> tuple[int, str]:
    """`gt:Zone_<name>` をゾーンインデックスへ解決する。"""
    if not value.startswith("gt:Zone_"):
        raise WhatIfCompileError(
            f"'{value}' はゾーン個体として解決できません（`gt:Zone_<name>` 形式のみ対応）。"
            "代替案：この個体名をシミュレーションのゾーン一覧と照合するか、"
            "poc_plan.md 付録D のデータ辞書でこの個体の型を確認してください。"
        )
    name = value[len("gt:Zone_") :]
    if name not in ZONE_NAMES:
        raise WhatIfCompileError(
            f"'{value}' はこの倉庫レイアウトに存在しないゾーンです（既知のゾーン："
            f"{ZONE_NAMES}）。代替案：既知のゾーンに置き換えるか、"
            "`gtwm sim gen` でこのゾーンを含む新しいレイアウトを生成してください。"
        )
    return ZONE_NAMES.index(name), value


def _resolve_entity(entity_gt_id: str) -> str:
    """個体を registry.yaml で解決し、カテゴリ名（例: "equipment"）を返す。"""
    category = category_of(entity_gt_id)
    if category is None:
        sample = sorted(known_gt_ids())[:10]
        raise WhatIfCompileError(
            f"個体 '{entity_gt_id}' は sim/assets/registry.yaml に見つからず、"
            "潜在スロット/行動変数に解決できません。介入不能です。"
            "代替案：この個体を含む新しいシミュレーションエピソードを "
            "`gtwm sim gen` で生成し registry.yaml に登録してから再実行するか、"
            f"既知の個体（例: {sample} ...）に置き換えてください。"
        )
    return category


def _compile_intervention(do: Intervention) -> CompiledIntervention:
    _resolve_entity(do.entity)  # 解決失敗なら WhatIfCompileError を送出
    if do.kind == "absent":
        value = 0.0
    elif do.kind == "literal":
        assert do.value is not None
        value = float(do.value)
    elif do.kind == "mul_current":
        assert do.value is not None
        value = DEFAULT_CURRENT_VALUE * float(do.value)
    elif do.kind == "add_current":
        assert do.value is not None
        value = DEFAULT_CURRENT_VALUE + float(do.value)
    elif do.kind == "sub_current":
        assert do.value is not None
        value = DEFAULT_CURRENT_VALUE - float(do.value)
    else:  # pragma: no cover - 網羅性チェック（parser.py が生成する種別と一致させる）
        raise WhatIfCompileError(f"未知の介入種別: {do.kind}")
    return CompiledIntervention(
        entity_gt_id=do.entity,
        property=do.property,
        action_dim=ACTION_DIM_FOR_INTERVENTION,
        action_value=value,
    )


def _compile_filter(triple: Triple) -> CompiledFilter:
    zone_idx, zone_id = _resolve_zone(triple.value)
    return CompiledFilter(key=triple.key, zone_index=zone_idx, zone_gt_id=zone_id)


def compile_query(query: WhatIfQuery) -> CompiledQuery:
    """`WhatIfQuery` -> `CompiledQuery`。個体解決に失敗すれば `WhatIfCompileError`。"""
    return CompiledQuery(
        vars=query.vars,
        horizons=query.horizons,
        filters=[_compile_filter(t) for t in query.filters],
        interventions=[_compile_intervention(d) for d in query.interventions],
        samples=query.samples,
        interval=query.interval,
        model_version=query.model_version,
    )


__all__ = [
    "CompiledQuery",
    "CompiledIntervention",
    "CompiledFilter",
    "WhatIfCompileError",
    "compile_query",
    "ACTION_DIM_FOR_INTERVENTION",
    "DEFAULT_CURRENT_VALUE",
]
