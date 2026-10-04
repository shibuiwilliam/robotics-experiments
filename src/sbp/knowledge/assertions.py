"""二時制アサーション API（設計書 §6.4、knowledge ルール、ADR-0005）。

公開関数は :meth:`AssertionStore.assert_` / :meth:`AssertionStore.retract` /
:meth:`AssertionStore.query` の 3 つだけ。SPARQL 文字列はこのモジュールに閉じる。

**不変条件 2（追記専用）の実装**: 訂正は旧文の ``tx_to`` を書き込んで閉じ、新文を追加する。
トリプルの DELETE は ``tx_to`` の追加（INSERT DATA）としてのみ現れ、主張そのものを消す経路は
存在しない（物理削除の API を作らない）。

**表現**（ADR-0005）: アサーション 1 件は reifier IRI 1 つ + ``rdf:reifies`` による triple term +
メタデータのトリプル群。開いている区間は ``validTo`` / ``txTo`` のトリプルを置かないことで表す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import UUID

from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief, CategoricalBelief
from sbp.core.errors import AppendOnlyViolation, AssertionNotFoundError, KnowledgeError
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import ensure_utc
from sbp.knowledge.graphs import belief_graph, scenario_graph

if TYPE_CHECKING:
    from sbp.core.belief import Belief
    from sbp.knowledge.store import GraphStore

__all__ = ["AssertionRow", "AssertionStore"]

log = get_logger(__name__)

_CORE: Final = "https://sbp.example/core#"
_RDF: Final = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_XSD: Final = "http://www.w3.org/2001/XMLSchema#"
_ASSERTION_NS: Final = "https://sbp.example/assertion/"

_PREFIXES: Final = f"""PREFIX sbp: <{_CORE}>
PREFIX rdf: <{_RDF}>
PREFIX xsd: <{_XSD}>
"""

_MODALITY_INDIVIDUAL: Final[dict[Modality, str]] = {
    Modality.OBSERVED: "Observed",
    Modality.INFERRED: "Inferred",
    Modality.PREDICTED: "Predicted",
    Modality.PLANNED: "Planned",
    Modality.NORMATIVE: "Normative",
    Modality.HYPOTHETICAL: "Hypothetical",
}
_INDIVIDUAL_TO_MODALITY: Final[dict[str, Modality]] = {
    f"{_CORE}{name}": modality for modality, name in _MODALITY_INDIVIDUAL.items()
}


@dataclass(frozen=True, slots=True)
class AssertionRow:
    """クエリ結果の 1 件。

    ``ontology_version`` を必ず含める（knowledge ルール: 呼び出し側が版を無視して比較しない
    ようにするため）。

    Attributes:
        assertion_id: アサーション識別子。
        subject: 主語 IRI。
        predicate: 述語 IRI。
        object_: 目的語（IRI またはリテラルの文字列表現）。
        valid_from: 有効開始。
        valid_to: 有効終了（継続中は ``None``）。
        tx_from: 記録開始。
        tx_to: 記録終了（現在の信念なら ``None``）。
        modality: 様相。
        confidence: 較正済み確信度（真偽フルーエントの成立確率）。
        provenance: 来歴バンドル IRI。
        ontology_version: この文を作ったときのオントロジー版。
        graph: 格納されている名前付きグラフ。
    """

    assertion_id: UUID
    subject: str
    predicate: str
    object_: str
    valid_from: datetime
    valid_to: datetime | None
    tx_from: datetime
    tx_to: datetime | None
    modality: Modality
    confidence: float
    provenance: str
    ontology_version: str
    graph: str

    @property
    def is_current(self) -> bool:
        """記録時間が開いている（現在の信念である）かどうか。"""
        return self.tx_to is None


def _iri(value: str) -> str:
    return f"<{value}>"


def _dt_literal(value: datetime) -> str:
    return f'"{ensure_utc(value).isoformat().replace("+00:00", "Z")}"^^xsd:dateTime'


def _double_literal(value: float) -> str:
    return f'"{value!r}"^^xsd:double'


def _string_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _duration_literal(value: timedelta) -> str:
    total = int(value.total_seconds())
    return f'"PT{total}S"^^xsd:duration'


def _object_term(value: object) -> str:
    """アサーションの目的語を SPARQL の項に変換する。"""
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^xsd:boolean'
    if isinstance(value, int | float):
        return _double_literal(float(value))
    text = str(value)
    if text.startswith(("http://", "https://", "urn:")):
        return _iri(text)
    return _string_literal(text)


def _confidence_of(belief: Belief) -> float:
    """信念から代表確信度を取り出す。

    真偽は成立確率、カテゴリは最大確率、数値は 1.0（確信度の概念が当てはまらないため、
    分布の詳細は証拠側と来歴に残す）。
    """
    if isinstance(belief, BooleanBelief):
        return belief.p
    if isinstance(belief, CategoricalBelief):
        return max(belief.pmf.values())
    # NumericBelief。確信度という概念が当てはまらないので 1.0 を置き、分布は証拠と来歴に残す。
    return 1.0


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


class AssertionStore:
    """二時制アサーションの読み書き。

    公開 API はこのクラスの 3 メソッドだけ。``reasoning`` 層はここを通してのみ永続化する
    （CLAUDE.md の層ルール）。
    """

    def __init__(self, store: GraphStore, *, site_id: str) -> None:
        """Args:
        store: グラフストア（組み込みまたは HTTP）。
        site_id: 拠点識別子（名前付きグラフの構成要素）。
        """
        self._store = store
        self._site_id = site_id

    # ---- 書き込み ----

    def assert_(self, assertion: Assertion) -> UUID:
        """アサーションを 1 件追加する（追記のみ）。

        Args:
            assertion: 追加する信念文。``hypothetical`` ならシナリオグラフへ隔離される。

        Returns:
            追加したアサーションの識別子。

        Raises:
            AppendOnlyViolation: 同じ ``assertion_id`` の文が既にある場合（上書きを防ぐ）。
        """
        if self._exists(assertion.assertion_id):
            raise AppendOnlyViolation(
                f"assertion {assertion.assertion_id} already exists; corrections must close "
                "tx_to on the old statement and add a new one (invariant 2)"
            )
        graph = self._graph_for(assertion)
        reifier = f"{_ASSERTION_NS}{assertion.assertion_id}"
        triples = self._metadata_triples(assertion)
        body = " ;\n        ".join(triples)
        sparql = (
            f"{_PREFIXES}\n"
            f"INSERT DATA {{\n"
            f"  GRAPH {_iri(graph)} {{\n"
            f"    {_iri(reifier)} rdf:reifies "
            f"<<( {_iri(assertion.subject)} {_iri(assertion.predicate)} "
            f"{_object_term(assertion.object)} )>> ;\n"
            f"        {body} .\n"
            f"  }}\n"
            f"}}"
        )
        self._store.update(sparql)
        log.debug(
            "knowledge.asserted",
            assertion_id=str(assertion.assertion_id),
            entity=assertion.subject,
            predicate=assertion.predicate,
            modality=assertion.modality.value,
            graph=graph,
        )
        return assertion.assertion_id

    def retract(self, assertion_id: UUID, at: datetime) -> None:
        """記録時間を閉じる（撤回）。文そのものは消さない（不変条件 2）。

        Args:
            assertion_id: 対象アサーションの識別子。
            at: ``tx_to`` に書き込む時刻。``tx_from`` より後でなければならない。

        Raises:
            AssertionNotFoundError: 該当する文が存在しない場合。
            AppendOnlyViolation: 既に閉じている場合、または ``at`` が ``tx_from`` 以前の場合。
        """
        closing = ensure_utc(at)
        current = self._metadata_of(assertion_id)
        if current is None:
            raise AssertionNotFoundError(f"assertion {assertion_id} does not exist")
        tx_from, tx_to = current
        if tx_to is not None:
            raise AppendOnlyViolation(
                f"assertion {assertion_id} is already closed at {tx_to.isoformat()}; "
                "a closed statement is never reopened (invariant 2)"
            )
        if closing <= tx_from:
            raise AppendOnlyViolation(
                f"tx_to ({closing.isoformat()}) must be strictly after "
                f"tx_from ({tx_from.isoformat()})"
            )
        reifier = f"{_ASSERTION_NS}{assertion_id}"
        sparql = (
            f"{_PREFIXES}\n"
            f"INSERT {{ GRAPH ?g {{ {_iri(reifier)} sbp:txTo {_dt_literal(closing)} }} }}\n"
            f"WHERE {{ GRAPH ?g {{ {_iri(reifier)} sbp:txFrom ?txf }} }}"
        )
        self._store.update(sparql)
        log.debug("knowledge.retracted", assertion_id=str(assertion_id), tx_to=closing.isoformat())

    # ---- 読み出し ----

    def query(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        as_of: datetime | None = None,
        valid_at: datetime | None = None,
        modality: Modality | None = None,
        min_confidence: float | None = None,
        scenario: str | None = None,
        include_closed: bool = False,
        all_sites: bool = False,
    ) -> list[AssertionRow]:
        """二時制クエリ。

        Args:
            subject: 主語 IRI で絞る。
            predicate: 述語 IRI で絞る。
            as_of: 「この時刻に基盤がそう信じていた」文に絞る
                （``tx_from <= as_of < tx_to``）。
                指定しなければ現在の信念（``tx_to`` が無い文）だけを返す。
            valid_at: 「この時刻に現実にそうであった」文に絞る
                （``valid_from <= valid_at < valid_to``）。
            modality: 様相で絞る。
            min_confidence: この確信度以上の文に絞る。
            scenario: シナリオ識別子。指定するとそのシナリオグラフを見る。指定しなければ
                主信念グラフだけを見る（``hypothetical`` は混ざらない。不変条件 3）。
            include_closed: 真にすると ``tx_to`` が閉じた文も返す（精度追跡・監査用）。
                ``as_of`` を指定した場合はそちらの条件が優先される。
            all_sites: 既定ではこのストアの拠点のグラフだけを見る（設計書 §6.3 のパーティション）。
                真にすると全拠点を横断する。中央のフェデレーション層だけが使う。

        Returns:
            条件に合う文のリスト。各行は ``ontology_version`` を必ず持つ。
        """
        filters: list[str] = []
        if subject is not None:
            filters.append(f"FILTER (?s = {_iri(subject)})")
        if predicate is not None:
            filters.append(f"FILTER (?p = {_iri(predicate)})")
        if modality is not None:
            filters.append(f"FILTER (?modality = {_iri(_CORE + _MODALITY_INDIVIDUAL[modality])})")
        if min_confidence is not None:
            filters.append(f"FILTER (?confidence >= {_double_literal(min_confidence)})")

        if as_of is not None:
            stamp = _dt_literal(as_of)
            filters.append(f"FILTER (?txFrom <= {stamp} && (!BOUND(?txTo) || ?txTo > {stamp}))")
        elif not include_closed:
            filters.append("FILTER (!BOUND(?txTo))")

        if valid_at is not None:
            stamp = _dt_literal(valid_at)
            filters.append(
                f"FILTER (?validFrom <= {stamp} && (!BOUND(?validTo) || ?validTo > {stamp}))"
            )

        # シナリオ指定があればそのグラフだけを見る。無ければ主信念グラフだけを見て、
        # シナリオグラフ（hypothetical の隔離先）を混ぜない（不変条件 3）。
        if scenario is not None:
            graph_clause = f"GRAPH {_iri(scenario_graph(scenario))} {{"
            graph_filters = ""
        else:
            graph_clause = "GRAPH ?g {"
            graph_filters = "  FILTER (!STRSTARTS(STR(?g), 'urn:sbp:scenario:'))\n"
            if not all_sites:
                # 拠点スコープ。グラフ IRI は urn:sbp:g:<modality>:<site>:<version>。
                graph_filters += f"  FILTER (CONTAINS(STR(?g), ':{self._site_id}:'))\n"

        sparql = (
            f"{_PREFIXES}\n"
            "SELECT ?a ?s ?p ?o ?validFrom ?validTo ?txFrom ?txTo ?modality ?confidence\n"
            "       ?provenance ?ontologyVersion ?g\n"
            "WHERE {\n"
            f"  {graph_clause}\n"
            "    ?a rdf:reifies <<( ?s ?p ?o )>> ;\n"
            "       sbp:validFrom ?validFrom ;\n"
            "       sbp:txFrom ?txFrom ;\n"
            "       sbp:modality ?modality ;\n"
            "       sbp:confidence ?confidence ;\n"
            "       sbp:provenance ?provenance ;\n"
            "       sbp:ontologyVersion ?ontologyVersion .\n"
            "    OPTIONAL { ?a sbp:validTo ?validTo }\n"
            "    OPTIONAL { ?a sbp:txTo ?txTo }\n"
            "  }\n"
            f"{graph_filters}" + "".join(f"  {f}\n" for f in filters) + "}\nORDER BY ?txFrom ?a"
        )
        rows = self._store.query(sparql)
        return [self._to_row(row) for row in rows]

    # ---- 内部 ----

    def _graph_for(self, assertion: Assertion) -> str:
        if assertion.modality is Modality.HYPOTHETICAL:
            if assertion.scenario is None:  # pragma: no cover — pydantic が先に弾く
                raise KnowledgeError("hypothetical assertion without scenario")
            return scenario_graph(assertion.scenario)
        return belief_graph(
            modality=assertion.modality,
            site_id=self._site_id,
            ontology_version=assertion.ontology_version,
        )

    def _metadata_triples(self, assertion: Assertion) -> list[str]:
        triples = [
            f"sbp:validFrom {_dt_literal(assertion.valid_from)}",
            f"sbp:txFrom {_dt_literal(assertion.tx_from)}",
            f"sbp:modality {_iri(_CORE + _MODALITY_INDIVIDUAL[assertion.modality])}",
            f"sbp:confidence {_double_literal(_confidence_of(assertion.belief))}",
            f"sbp:provenance {_iri(assertion.provenance)}",
            f"sbp:ontologyVersion {_string_literal(assertion.ontology_version)}",
        ]
        if assertion.valid_to is not None:
            triples.append(f"sbp:validTo {_dt_literal(assertion.valid_to)}")
        if assertion.tx_to is not None:
            triples.append(f"sbp:txTo {_dt_literal(assertion.tx_to)}")
        if assertion.horizon_origin is not None:
            triples.append(f"sbp:horizonOrigin {_dt_literal(assertion.horizon_origin)}")
        if assertion.lead_time is not None:
            triples.append(f"sbp:leadTime {_duration_literal(assertion.lead_time)}")
        if assertion.source_reliability is not None:
            triples.append(f"sbp:sourceReliability {_iri(assertion.source_reliability)}")
        if assertion.situation is not None:
            triples.append(f"sbp:inSituation {_iri(assertion.situation)}")
        if assertion.scenario is not None:
            triples.append(f"sbp:scenario {_iri(scenario_graph(assertion.scenario))}")
        return triples

    def _exists(self, assertion_id: UUID) -> bool:
        reifier = f"{_ASSERTION_NS}{assertion_id}"
        rows = self._store.query(
            f"{_PREFIXES}\nASK {{ GRAPH ?g {{ {_iri(reifier)} sbp:txFrom ?txf }} }}"
        )
        return rows == [{"__ask__": "true"}]

    def _metadata_of(self, assertion_id: UUID) -> tuple[datetime, datetime | None] | None:
        """``(tx_from, tx_to)`` を返す。存在しなければ ``None``。"""
        reifier = f"{_ASSERTION_NS}{assertion_id}"
        rows = self._store.query(
            f"{_PREFIXES}\n"
            "SELECT ?txFrom ?txTo WHERE {\n"
            f"  GRAPH ?g {{ {_iri(reifier)} sbp:txFrom ?txFrom . "
            f"OPTIONAL {{ {_iri(reifier)} sbp:txTo ?txTo }} }}\n"
            "}"
        )
        if not rows:
            return None
        tx_from = _parse_dt(rows[0]["txFrom"])
        if tx_from is None:  # pragma: no cover — txFrom は必須
            raise KnowledgeError(f"assertion {assertion_id} has no txFrom")
        return tx_from, _parse_dt(rows[0]["txTo"])

    def _to_row(self, row: dict[str, str | None]) -> AssertionRow:
        reifier = row["a"] or ""
        valid_from = _parse_dt(row["validFrom"])
        tx_from = _parse_dt(row["txFrom"])
        if valid_from is None or tx_from is None:  # pragma: no cover — 必須述語
            raise KnowledgeError(f"assertion row missing required temporal fields: {row!r}")
        modality_iri = row["modality"] or ""
        try:
            modality = _INDIVIDUAL_TO_MODALITY[modality_iri]
        except KeyError as exc:
            raise KnowledgeError(f"unknown modality individual: {modality_iri!r}") from exc
        return AssertionRow(
            assertion_id=UUID(reifier.removeprefix(_ASSERTION_NS)),
            subject=row["s"] or "",
            predicate=row["p"] or "",
            object_=row["o"] or "",
            valid_from=valid_from,
            valid_to=_parse_dt(row["validTo"]),
            tx_from=tx_from,
            tx_to=_parse_dt(row["txTo"]),
            modality=modality,
            confidence=float(row["confidence"] or "0"),
            provenance=row["provenance"] or "",
            ontology_version=row["ontologyVersion"] or "",
            graph=row.get("g") or "",
        )


def current_beliefs(
    store: AssertionStore,
    *,
    entity: str,
    at: datetime | None = None,
    modality: Modality = Modality.INFERRED,
) -> dict[str, float]:
    """実体について成立しているフルーエントと確信度を引く（M8-21）。

    真偽フルーエントの信念は ``(実体, フルーエント型, True)`` という形で入っている
    （:meth:`BeliefUpdate.to_assertion`）。アクションの前提検証は
    「フルーエント型 IRI → 確信度」の表を要求するので、ここで畳む。

    **同じフルーエントが複数あれば最も確信度が高いものを採る。** 重なるのは区間が分かれて
    いるときで、``valid_at`` を指定すれば 1 件に絞れる。指定しない呼び出しで黙って先頭を
    採ると、どれが返るかが格納順に依存する。

    **様相は既定で ``inferred`` に絞る。** 前提の物理面は「基盤が推定した状態」を見るべきで、
    計画（``planned``）を混ぜると「計画どおりなら前提を満たす」ことになり、安全ガードが
    計画の追認になる（設計書 §9.3）。

    Args:
        store: アサーションストア。
        entity: 対象実体の IRI。
        at: ``valid_at``（この時刻に成立していた文に絞る）。``None`` なら区間を絞らない。
        modality: 様相で絞る。

    Returns:
        フルーエント型 IRI → 確信度。目的語が ``True`` でない文は含めない
        （フルーエントの成立を表していないため）。
    """
    rows = store.query(subject=entity, valid_at=at, modality=modality)
    beliefs: dict[str, float] = {}
    for row in rows:
        if row.object_ not in _TRUE_LITERALS:
            continue
        current = beliefs.get(row.predicate)
        if current is None or row.confidence > current:
            beliefs[row.predicate] = row.confidence
    return beliefs


_TRUE_LITERALS = frozenset({"true", "True"})
"""真偽フルーエントの目的語の表現（ストアの literal 表現に依る）。"""
