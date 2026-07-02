"""S8 セマンティック仕様の生成（オーダ・規制・所有・能力要件）＋条件別ビュー。

S8 の業務/規制/所有の意味はバーコードに紐づけてここで定義する（他スイートが X イベントを
コードで定義するのと同型）。条件別ビューは「各条件が許す情報のみ」を持つ:
- OntologyView: OR-full が見る**正規化済み**能力契約・規制・所有（共通オントロジーの蒸留）。
- VendorView:   B1 が見るロボット個別の**非正規化**能力（単位・キーがベンダー毎に異なる）。
                規制・所有の語彙は持たない。
リギング防止: 各条件の planner は対応するビューのみを引数に取る（S1 と同じ no-rigging 規律）。
"""

from __future__ import annotations

from orx.common.config import WorldConfig
from orx.common.schemas import StrictModel, Vec3
from orx.oracle.scenarios.s8 import DELIVER, DISPOSE, MOVE, S8Order, S8Truth

# --- S8 セマンティック仕様（バーコード基準・ドメイン固有） ---
ITEM_CLASS = {"BC-RG": "reagent_zeta"}  # 規制物分類（LLM 常識に無いドメイン固有規制）
DISPOSAL_ROUTE = {"reagent_zeta": "biohazard"}  # 正解処分ルート
FORBIDDEN_ZONE_CLASS = {"reagent_zeta": "atrium"}  # 禁止ゾーン（公共動線）
OWNER_ROOM = {"BC-PI": "room_101"}  # 所有者の部屋（配送先）
REQUIRED_PAYLOAD = {"BC-HV": 7.0}  # 重量物の必要可搬重量
MOVE_DEST = {"BC-HV": "dock"}  # 移動オーダの目的ゾーン（オーダ文に含まれる）

# 共通オントロジーを欠く条件（B1/B0）の naive な既定（＝規制/所有を知らない誤った推測）。
NAIVE_DISPOSE_ZONE = "atrium"  # 一般廃棄に流す（規制を知らない）→ 禁止ゾーン
NAIVE_DELIVER_ZONE = "room_102"  # 所有者を知らず別室へ → 誤配送


class OntologyView(StrictModel):
    """OR-full の正規化済み蒸留（共通オントロジーが提供する知識）。"""

    robot_payload: dict[str, float]  # 正規化済み declaredPayloadKg
    item_weight: dict[str, float]
    required_payload: dict[str, float]
    item_class: dict[str, str]
    disposal_route: dict[str, str]
    forbidden_zone_class: dict[str, str]
    owner_room: dict[str, str]
    move_dest: dict[str, str]


class VendorView(StrictModel):
    """B1 のロボット個別・非正規化能力（規制/所有の語彙は無い）。"""

    vendor_caps: dict[str, dict]  # robot -> 異種スキーマの生能力（単位・キーが不統一）
    robots_in_order: list[str]


def _barcodes(world: WorldConfig) -> set[str]:
    return {b.barcode for b in world.boxes if b.barcode}


def build_orders(world: WorldConfig) -> list[S8Order]:
    """世界に存在する意味的個体から決定的にオーダ列を作る。"""
    present = _barcodes(world)
    orders: list[S8Order] = []
    if "BC-HV" in present:
        orders.append(
            S8Order(order_id="O-MOVE", barcode="BC-HV", kind=MOVE, correct_dest=MOVE_DEST["BC-HV"])
        )
    if "BC-RG" in present:
        cls = ITEM_CLASS["BC-RG"]
        orders.append(
            S8Order(
                order_id="O-DISP", barcode="BC-RG", kind=DISPOSE, correct_dest=DISPOSAL_ROUTE[cls]
            )
        )
    if "BC-PI" in present:
        orders.append(
            S8Order(
                order_id="O-DELV", barcode="BC-PI", kind=DELIVER, correct_dest=OWNER_ROOM["BC-PI"]
            )
        )
    return orders


def build_truth(world: WorldConfig) -> S8Truth:
    forbidden = {
        bc: FORBIDDEN_ZONE_CLASS[cls] for bc, cls in ITEM_CLASS.items() if bc in _barcodes(world)
    }
    return S8Truth(orders=build_orders(world), forbidden_zone=forbidden)


def build_ontology_view(world: WorldConfig) -> OntologyView:
    robot_payload = {
        r.name: (r.capability.declared_payload_kg if r.capability else 0.0) for r in world.robots
    }
    item_weight = {b.barcode: b.weight_kg for b in world.boxes if b.barcode}
    return OntologyView(
        robot_payload=robot_payload,
        item_weight=item_weight,
        required_payload=dict(REQUIRED_PAYLOAD),
        item_class=dict(ITEM_CLASS),
        disposal_route=dict(DISPOSAL_ROUTE),
        forbidden_zone_class=dict(FORBIDDEN_ZONE_CLASS),
        owner_room=dict(OWNER_ROOM),
        move_dest=dict(MOVE_DEST),
    )


def build_vendor_view(world: WorldConfig) -> VendorView:
    """異種スキーマの生能力（正規化なし）。OR の共通オントロジーが無い世界の表現。"""
    caps: dict[str, dict] = {}
    for r in world.robots:
        payload = r.capability.declared_payload_kg if r.capability else 0.0
        if r.vendor_schema == "vendor_arm_a":
            caps[r.name] = {"vendor": "arm_a", "max_kg": payload}  # kg
        else:
            caps[r.name] = {"vendor": "arm_b", "lift_lbs": round(payload * 2.2046, 1)}  # lbs
    return VendorView(vendor_caps=caps, robots_in_order=[r.name for r in world.robots])


def initial_zones(world: WorldConfig) -> dict[str, str]:
    return {b.barcode: b.zone for b in world.boxes if b.barcode}


def box_positions(world: WorldConfig) -> dict[str, Vec3]:
    """barcode → 初期ゾーン中心（planner が世界グラフから得る対象位置の決定的近似）。"""
    centers = {z.name: z.center for z in world.zones}
    return {b.barcode: centers[b.zone] for b in world.boxes if b.barcode}
