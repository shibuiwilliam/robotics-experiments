"""WHAT-IF コンパイラ：クエリ中のオントロジー個体を潜在スロット/行動変数に解決する。

ontology.md「EPCIS と WHAT-IF」：「コンパイラは個体→潜在スロット／行動変数の解決に
失敗したら例外を投げ、シミュレーション代替を提案するメッセージを返す」。
"""

from __future__ import annotations

from dataclasses import dataclass

from gtwm.kg.whatif.parser import Intervention, Offset, Triple, WhatIfQuery
from gtwm.sim.env import ZONE_NAMES
from gtwm.sim.registry import category_of, known_gt_ids

# 行動空間チャネル定義（gap #3 の修正、旧 ACTION_DIM_FOR_INTERVENTION=一様dim0の解消）。
# `wm/dynamics.py` の `Dynamics`/`DynamicsHead` は action_dim（configs/wm/*.yaml で
# 固定値4）次元の抽象ベクトルを受け取るだけで意味を知らないため、「どのプロパティが
# どの次元に対応するか」という契約はここ（コンパイラ）で一元管理する
# （`wm/dynamics.py` の docstring にもこの対応表への参照を書く）。
# 各チャネルは EXP-07（poc_plan.md 6.2 の3介入：コンベア速度・担当人数・一時置き場
# 位置）が実際に使うプロパティ名と一致させてある。次元3は将来の介入種別のために
# 未使用のまま予約する（0で固定）。
ACTION_CHANNELS: dict[str, int] = {
    "speed": 0,  # 設備の相対速度倍率（例：コンベア速度）
    "active": 1,  # 稼働状態・人数の増減（例：作業者の稼働/非稼働）
    "staging_offset": 2,  # 一時置き場・動線のオフセット
}
ACTION_DIM_RESERVED = 3  # 将来の介入種別のために未使用のまま予約

# "current" の基準値。gt:Equipment_*/gt:Worker_* の数値プロパティの現在値を保持する
# プロパティストアがまだ KG に無い（現状 KG は位置・型・状態のみ）ため、プロパティ
# ごとに意味的に妥当なプレースホルダー基準値を使う：倍率チャネル（speed）は基準1.0
# （現在値に対する倍率として解釈できる）、加減算チャネル（active/staging_offset）は
# 基準0.0（現在値からの増減量として解釈できる）。`* current` 等の相対介入はこの
# 基準に対する演算として計算する。
DEFAULT_CURRENT_VALUE_BY_PROPERTY: dict[str, float] = {
    "speed": 1.0,
    "active": 0.0,
    "staging_offset": 0.0,
}
_KNOWN_PROPERTIES = sorted(ACTION_CHANNELS)


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


def _resolve_action_channel(property_name: str) -> int:
    """プロパティ名を行動ベクトルの次元に解決する。未知のプロパティは介入不能。"""
    dim = ACTION_CHANNELS.get(property_name)
    if dim is None:
        raise WhatIfCompileError(
            f"プロパティ '{property_name}' は行動空間のどのチャネルにも対応付けられて"
            f"いません（既知のプロパティ：{_KNOWN_PROPERTIES}）。介入不能です。"
            "代替案：既知のプロパティに置き換えるか、"
            "`kg/whatif/compiler.py` の ACTION_CHANNELS に新しいチャネルを追加し、"
            "`wm/dynamics.py` の action_dim を拡張してから再実行してください。"
        )
    return dim


def _compile_intervention(do: Intervention) -> CompiledIntervention:
    _resolve_entity(do.entity)  # 解決失敗なら WhatIfCompileError を送出
    action_dim = _resolve_action_channel(do.property)
    current = DEFAULT_CURRENT_VALUE_BY_PROPERTY[do.property]
    if do.kind == "absent":
        value = 0.0
    elif do.kind == "literal":
        assert do.value is not None
        value = float(do.value)
    elif do.kind == "mul_current":
        assert do.value is not None
        value = current * float(do.value)
    elif do.kind == "add_current":
        assert do.value is not None
        value = current + float(do.value)
    elif do.kind == "sub_current":
        assert do.value is not None
        value = current - float(do.value)
    else:  # pragma: no cover - 網羅性チェック（parser.py が生成する種別と一致させる）
        raise WhatIfCompileError(f"未知の介入種別: {do.kind}")
    return CompiledIntervention(
        entity_gt_id=do.entity,
        property=do.property,
        action_dim=action_dim,
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
    "ACTION_CHANNELS",
    "ACTION_DIM_RESERVED",
    "DEFAULT_CURRENT_VALUE_BY_PROPERTY",
]
