"""WMSレコード → 世界グラフ主張（仮想グラフ写像）。

業務レコードは情報的実体として、来歴 = wms エージェント・確信度1.0・無期限で
主張化する。書込自体は exp 層が orx.kg の公開APIで行う（依存方向の遵守）。
"""

from __future__ import annotations

from orx.business.db import LotData, WmsRecord
from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.common.seeding import SeedTree, deterministic_id

_WMS_AGENT = iri.entity("agent", "wms")


def order_iri(order_id: str) -> str:
    return iri.entity("order", order_id)


def sku_iri(sku: str) -> str:
    return iri.entity("sku", sku)


def instruction_iri(instruction_id: str) -> str:
    return iri.entity("shipinst", instruction_id)


def lot_iri(lot_id: str) -> str:
    return iri.entity("lot", lot_id)


def recall_iri(recall_id: str) -> str:
    return iri.entity("recall", recall_id)


def wms_claims(record: WmsRecord, seeds: SeedTree, observed_at: float = 0.0) -> list[Claim]:
    rng = seeds.child("wms-lift").rng()

    def claim(subject: str, predicate: str, obj: Term, conf: float = 1.0) -> Claim:
        return Claim(
            claim_id=deterministic_id(rng),
            subject=subject,
            predicate=predicate,
            object=obj,
            asserted_by=_WMS_AGENT,
            confidence=conf,
            observed_at=observed_at,
            valid_until=None,
        )

    claims: list[Claim] = []
    for sku in record.skus:
        s = sku_iri(str(sku["sku"]))
        claims.append(claim(s, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("Sku"))))
        claims.append(claim(s, iri.biz("skuName"), Term(kind="literal", value=str(sku["name"]))))
        claims.append(
            claim(
                s,
                iri.biz("weightKg"),
                Term(kind="literal", value=repr(float(sku["weight_kg"])), datatype=iri.XSD_DOUBLE),
            )
        )
        claims.append(
            claim(
                s,
                iri.biz("fragile"),
                Term(
                    kind="literal",
                    value="true" if sku["fragile"] else "false",
                    datatype="http://www.w3.org/2001/XMLSchema#boolean",
                ),
            )
        )
    for order in record.orders:
        o = order_iri(str(order["order_id"]))
        claims.append(claim(o, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("Order"))))
        claims.append(
            claim(o, iri.biz("ordersSku"), Term(kind="iri", value=sku_iri(str(order["sku"]))))
        )
        claims.append(claim(o, iri.biz("status"), Term(kind="literal", value=str(order["status"]))))
        claims.append(
            claim(o, iri.biz("destination"), Term(kind="literal", value=str(order["destination"])))
        )
    for inst in record.instructions:
        s = instruction_iri(inst["instruction_id"])
        claims.append(
            claim(s, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("ShippingInstruction")))
        )
        claims.append(
            claim(s, iri.biz("forOrder"), Term(kind="iri", value=order_iri(inst["order_id"])))
        )
        claims.append(claim(s, iri.biz("hasBarcode"), Term(kind="literal", value=inst["barcode"])))
    return claims


def lot_claims(
    record: WmsRecord, lot_data: LotData, seeds: SeedTree, observed_at: float = 0.0
) -> list[Claim]:
    """S1: ロット帰属（個装→ロット）と回収指示を主張化する（来歴=wms, 確信度1.0）。

    アイデンティティ・スレッド: ShippingInstruction --memberOfLot--> Lot。
    回収逆引きは Lot ← memberOfLot ← instruction --hasBarcode--> 物理個体 で閉じる。
    """
    rng = seeds.child("lot-lift").rng()
    barcode_to_inst = {i["barcode"]: i["instruction_id"] for i in record.instructions}

    def claim(subject: str, predicate: str, obj: Term) -> Claim:
        return Claim(
            claim_id=deterministic_id(rng),
            subject=subject,
            predicate=predicate,
            object=obj,
            asserted_by=_WMS_AGENT,
            confidence=1.0,
            observed_at=observed_at,
            valid_until=None,
        )

    claims: list[Claim] = []
    for lot_id in sorted(set(lot_data.members.values())):
        claims.append(claim(lot_iri(lot_id), iri.RDF_TYPE, Term(kind="iri", value=iri.biz("Lot"))))
    for barcode, lot_id in sorted(lot_data.members.items()):
        inst_id = barcode_to_inst.get(barcode)
        if inst_id is None:
            continue
        claims.append(
            claim(
                instruction_iri(inst_id),
                iri.biz("memberOfLot"),
                Term(kind="iri", value=lot_iri(lot_id)),
            )
        )
    rec = recall_iri(lot_data.recall_id)
    claims.append(claim(rec, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("RecallOrder"))))
    claims.append(
        claim(rec, iri.biz("targetsLot"), Term(kind="iri", value=lot_iri(lot_data.recall_lot)))
    )
    return claims
