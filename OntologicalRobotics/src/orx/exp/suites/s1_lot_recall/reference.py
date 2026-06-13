"""S1 条件別リファレンスソルバ（ADR-014: 情報層での反証）。

各ソルバは**その条件が許す情報のみ**を引数に取る（リギング防止を型・引数で担保）:
- B0: 最新の生観測ダンプ＋業務lotテーブル（世界グラフ・identity threadを引数に取らない）。
- B1: ロボット個別の知覚イベント（lift済み・**横断融合なし**）＋lotテーブル。
- OR-full: 世界グラフ（anchoringで横断融合済み）＋identity thread（グラフ内のlot主張）。

回収列挙クエリ「ロットLに属する物理個体とその現在ゾーン」を各条件が解く。
返り値は {barcode: 現在ゾーン名 or None}（None=対象と判定したが位置不明）。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.config import ZoneConfig
from orx.common.geometry import zone_of
from orx.common.schemas import PerceptionEvent, RawObservation, StrictModel, Vec3
from orx.kg.world_graph import CURRENT_GRAPH, WorldGraph


class RecallAnswer(StrictModel):
    """回収対象の列挙結果。targets[barcode] = 報告した現在ゾーン名（None=位置不明）。"""

    targets: dict[str, str | None]


# ----------------------------------------------------------------- B0（生ダンプ）


def _arm_a_barcoded(payload: dict, zones: list[ZoneConfig]) -> dict[str, str | None]:
    """vendor_arm_a 形式の最新ペイロードから (barcode -> zone) を読む（メートル）。

    生JSONを読むだけ（共通オントロジー・正規化層は使わない）。バーコードを読めるのは
    arm_a のみで、最新フレームに無い個体は構造的に取得できない。
    """
    out: dict[str, str | None] = {}
    for det in payload.get("dets", []):
        bc = det.get("bc")
        if bc is None:
            continue
        p = det.get("p", {})
        pos: Vec3 = (float(p["x"]), float(p["y"]), float(p["z"]))
        out[str(bc)] = zone_of(zones, pos)
    return out


def solve_b0(
    latest_raw: dict[str, RawObservation],
    zones: list[ZoneConfig],
    lot_members: dict[str, str],
    recall_lot: str,
) -> RecallAnswer:
    """B0: 最新生観測ダンプのみ。搬送済みでバーコード不可読の個体は構造的に欠落。"""
    readable: dict[str, str | None] = {}
    for obs in latest_raw.values():
        if obs.vendor_schema == "vendor_arm_a":
            readable.update(_arm_a_barcoded(obs.payload, zones))
    return RecallAnswer(
        targets={bc: z for bc, z in readable.items() if lot_members.get(bc) == recall_lot}
    )


# --------------------------------------------------------- B1（個別スキーマ・非融合）


def solve_b1(
    events: list[PerceptionEvent],
    zones: list[ZoneConfig],
    lot_members: dict[str, str],
    recall_lot: str,
) -> RecallAnswer:
    """B1: ロボット個別の last-known（横断融合なし）。

    バーコードを直接読めた最後の観測位置を保持する（ロボット内の記憶は許す）。
    横断同一性が無いため、搬送でバーコード不可読になった個体は **古い位置のまま**
    （別ロボットの現観測と同一個体だと結べない）。
    """
    last_seen: dict[str, tuple[float, str | None]] = {}  # barcode -> (time, zone)
    for ev in sorted(events, key=lambda e: e.sim_time):
        for det in ev.detections:
            if det.symbol_id is None:
                continue
            prev = last_seen.get(det.symbol_id)
            if prev is None or ev.sim_time >= prev[0]:
                last_seen[det.symbol_id] = (ev.sim_time, zone_of(zones, det.position))
    return RecallAnswer(
        targets={
            bc: zone for bc, (_t, zone) in last_seen.items() if lot_members.get(bc) == recall_lot
        }
    )


# -------------------------------------------------------- OR-full（世界グラフ）


def solve_or(graph: WorldGraph, recall_lot: str, at_time: float) -> RecallAnswer:
    """OR-full: 世界グラフ＋identity thread で回収逆引き。

    Lot ← memberOfLot ← instruction --hasBarcode--> 物理個体 --inZone--> 現在ゾーン。
    搬送済み個体も anchoring の横断同一性で現在地が解決される。
    """
    graph.refresh_current_graph(at_time)
    rows = graph.query(
        f"""
        PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX orx-upper: <https://orx.local/onto/upper#>
        PREFIX orx-st: <https://orx.local/onto/st#>
        PREFIX orx-biz: <https://orx.local/onto/biz#>
        SELECT ?bc ?zone WHERE {{
          GRAPH <{CURRENT_GRAPH}> {{
            ?si orx-biz:memberOfLot <{iri.entity("lot", recall_lot)}> ;
                orx-biz:hasBarcode ?bc .
            OPTIONAL {{
              ?e orx-upper:hasIdentifier ?bc ; orx-st:inZone ?zone .
            }}
          }}
        }}
        """
    )
    targets: dict[str, str | None] = {}
    for row in rows:
        bc = row["bc"]
        zone_iri = row.get("zone")
        zone = iri.parse_entity(zone_iri)[1] if zone_iri else None
        # 同一バーコードに複数行が来た場合、ゾーン確定を優先
        if bc not in targets or targets[bc] is None:
            targets[bc] = zone
    return RecallAnswer(targets=targets)
