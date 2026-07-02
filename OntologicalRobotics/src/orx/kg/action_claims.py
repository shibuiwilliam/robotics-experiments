"""C6 — アクション書戻し Claim の構築（来歴付き・assert_claim 経由で書く）。

アクション実行の結果を世界グラフへ書き戻す Claim を組み立てる純関数群。
- effect: 対象個体の現在ゾーン（`orx-st:inZone` 関数的述語。最新高確信が勝つ＝アンドゥは
  後続の inZone 再主張で自然に上書きされる — 不変条件3 と同型の可逆設計、`owl:sameAs` 不使用）。
- custody: `orx-norm:CustodyStep`（誰が・どのゾーンを・いつ通したか）の監査ノード。
- action-execution: `orx-cap:ActionExecution`（状態・対象・実行ロボット・時刻・補償リンク）。

本モジュールは `orx.common` のみ import する（kg→common は許可）。assertion は呼び出し側が
`WorldGraph.assert_claim` で行う（書込 API の一元化, CLAUDE.md §3.2）。id は呼び出し側が
seeded RNG から供給する（`new_id`）ことでリプレイ同一性を保つ。
"""

from __future__ import annotations

from collections.abc import Callable

from orx.common import iri
from orx.common.schemas import Claim, Term

IdGen = Callable[[], str]

_XSD_INTEGER = "http://www.w3.org/2001/XMLSchema#integer"


def _zone_term(zone_name: str) -> Term:
    return Term(kind="iri", value=iri.entity("zone", zone_name))


def _string(value: str) -> Term:
    return Term(kind="literal", value=str(value), datatype=iri.XSD_STRING)


def _double(value: float) -> Term:
    return Term(kind="literal", value=str(float(value)), datatype=iri.XSD_DOUBLE)


def _integer(value: int) -> Term:
    return Term(kind="literal", value=str(int(value)), datatype=_XSD_INTEGER)


def _iri(value: str) -> Term:
    return Term(kind="iri", value=value)


def effect_claim(
    new_id: IdGen,
    object_iri: str,
    zone_name: str,
    asserted_by: str,
    observed_at: float,
    confidence: float = 1.0,
    valid_until: float | None = None,
) -> Claim:
    """アクション効果＝対象個体の新しい現在ゾーン（`orx-st:inZone`・関数的）。"""
    return Claim(
        claim_id=new_id(),
        subject=object_iri,
        predicate=iri.st("inZone"),
        object=_zone_term(zone_name),
        asserted_by=asserted_by,
        confidence=confidence,
        observed_at=observed_at,
        valid_until=valid_until,
    )


def custody_step_claims(
    new_id: IdGen,
    object_iri: str,
    zone_name: str,
    step_index: int,
    asserted_by: str,
    observed_at: float,
    confidence: float = 1.0,
) -> list[Claim]:
    """custody 通行ステップ（監査連鎖の1ノード）。"""
    step_iri = iri.entity("custody", new_id())

    def claim(predicate: str, obj: Term) -> Claim:
        return Claim(
            claim_id=new_id(),
            subject=step_iri,
            predicate=predicate,
            object=obj,
            asserted_by=asserted_by,
            confidence=confidence,
            observed_at=observed_at,
        )

    return [
        claim(iri.RDF_TYPE, _iri(iri.norm("CustodyStep"))),
        claim(iri.norm("custodyOf"), _iri(object_iri)),
        claim(iri.norm("atZone"), _string(zone_name)),
        claim(iri.norm("stepIndex"), _integer(step_index)),
    ]


def action_execution_claims(
    new_id: IdGen,
    robot_iri: str,
    object_iri: str,
    to_zone: str,
    status: str,
    observed_at: float,
    asserted_by: str,
    confidence: float = 1.0,
    compensates_iri: str | None = None,
) -> tuple[str, list[Claim]]:
    """アクション実行ノード（`orx-cap:ActionExecution`）。返り値 (exec_iri, claims)。"""
    exec_iri = iri.entity("action", new_id())

    def claim(predicate: str, obj: Term) -> Claim:
        return Claim(
            claim_id=new_id(),
            subject=exec_iri,
            predicate=predicate,
            object=obj,
            asserted_by=asserted_by,
            confidence=confidence,
            observed_at=observed_at,
        )

    claims = [
        claim(iri.RDF_TYPE, _iri(iri.cap("ActionExecution"))),
        claim(iri.cap("executedByRobot"), _iri(robot_iri)),
        claim(iri.cap("actedOn"), _iri(object_iri)),
        claim(iri.cap("toZone"), _string(to_zone)),
        claim(iri.cap("actionStatus"), _string(status)),
        claim(iri.cap("atTime"), _double(observed_at)),
    ]
    if compensates_iri is not None:
        claims.append(claim(iri.cap("compensates"), _iri(compensates_iri)))
    return exec_iri, claims
