"""C3 — SOP文書コーパス（Markdown）生成器。

各SKUについて状況別（通常取扱/破損時/保管）の手順書を決定的に生成する。
文書はオントロジーで個体化され（orx-biz:Document, appliesTo）、T7の
オントロジー誘導検索 vs 素のベクトルRAG の比較材料になる。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import Claim, StrictModel, Term
from orx.common.seeding import SeedTree, deterministic_id

SITUATIONS = {
    "handling": "通常取扱",
    "damage": "破損時対応",
    "storage": "保管条件",
}

_SOP_AGENT = iri.entity("agent", "sop-registry")


class SopDoc(StrictModel):
    doc_id: str
    title: str
    body: str
    applies_to_sku: str
    situation: str  # handling | damage | storage


def doc_iri(doc_id: str) -> str:
    return iri.entity("doc", doc_id)


def generate_sops(skus: list[dict[str, object]], seeds: SeedTree) -> list[SopDoc]:
    """SKU×状況の手順書を決定的に生成する。"""
    rng = seeds.child("sop").rng()
    docs: list[SopDoc] = []
    for sku in skus:
        sku_id = str(sku["sku"])
        name = str(sku["name"])
        fragile = bool(sku["fragile"])
        weight = float(sku["weight_kg"])  # type: ignore[arg-type]
        for situation, label in SITUATIONS.items():
            doc_id = f"SOP-{sku_id}-{situation}"
            steps = int(rng.integers(3, 6))
            body_lines = [f"# {name} {label}手順", ""]
            if situation == "damage" and fragile:
                body_lines.append("注意: 本品目は壊れやすい。破片の飛散に注意する。")
            if situation == "handling" and weight > 4.0:
                body_lines.append("注意: 重量物。二人作業または補助具を使用する。")
            for i in range(1, steps + 1):
                body_lines.append(f"{i}. 手順ステップ {i}（{name} / {label}）")
            docs.append(
                SopDoc(
                    doc_id=doc_id,
                    title=f"{name} {label}手順",
                    body="\n".join(body_lines),
                    applies_to_sku=sku_id,
                    situation=situation,
                )
            )
    return docs


def sop_claims(docs: list[SopDoc], seeds: SeedTree, observed_at: float = 0.0) -> list[Claim]:
    """SOP文書をオントロジーで個体化する（appliesTo＋situation）。"""
    rng = seeds.child("sop-lift").rng()

    def claim(subject: str, predicate: str, obj: Term) -> Claim:
        return Claim(
            claim_id=deterministic_id(rng),
            subject=subject,
            predicate=predicate,
            object=obj,
            asserted_by=_SOP_AGENT,
            confidence=1.0,
            observed_at=observed_at,
            valid_until=None,
        )

    claims: list[Claim] = []
    for doc in docs:
        d = doc_iri(doc.doc_id)
        claims.append(claim(d, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("Document"))))
        claims.append(
            claim(
                d,
                iri.biz("appliesTo"),
                Term(kind="iri", value=iri.entity("sku", doc.applies_to_sku)),
            )
        )
        claims.append(claim(d, iri.biz("situation"), Term(kind="literal", value=doc.situation)))
        claims.append(claim(d, iri.biz("docTitle"), Term(kind="literal", value=doc.title)))
    return claims
