"""``make ontology-check`` が実際に不整合を捕まえることを検証する。

検査が通ることだけを確認しても「何も検出しない検査」を見逃すため、既知の不整合を注入して
失敗することを確かめる。CLAUDE.md テスト方針の「不変条件が壊れたら落ちるテストを優先」に対応。
"""

from __future__ import annotations

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, SKOS
from scripts._ontology import (
    ENT,
    SBP,
    collect_terms,
    event_types,
    fluent_types,
    initiates_terminates,
    load_ontology,
)
from scripts.ontology_check import (
    Report,
    check_annotations,
    check_process_rules,
    check_shacl_warning_for_exclusives,
)


def _minimal_graph() -> Graph:
    """注釈が完備した最小のグラフ（これ単体では検査を通る）。"""
    graph = Graph()
    term = URIRef(ENT["Spinning"])
    graph.add((term, RDF.type, SBP.FluentType))
    graph.add((term, RDFS.label, Literal("回転中", lang="ja")))
    graph.add((term, RDFS.label, Literal("Spinning", lang="en")))
    graph.add((term, SKOS.definition, Literal("テスト用のフルーエント型。", lang="ja")))
    graph.add((term, SBP.layer, Literal("L2")))
    graph.add((term, SBP.owner, Literal("test-owner")))
    graph.add((term, SBP.subjectType, SBP.Asset))
    return graph


class TestAnnotationCheck:
    def test_complete_annotations_pass(self) -> None:
        report = Report()
        check_annotations(_minimal_graph(), report)
        assert report.failures == []

    def test_missing_japanese_label_fails(self) -> None:
        graph = _minimal_graph()
        graph.remove((URIRef(ENT["Spinning"]), RDFS.label, Literal("回転中", lang="ja")))
        report = Report()
        check_annotations(graph, report)
        assert any("rdfs:label@ja" in f for f in report.failures)

    def test_missing_definition_fails(self) -> None:
        graph = _minimal_graph()
        graph.remove((URIRef(ENT["Spinning"]), SKOS.definition, None))
        report = Report()
        check_annotations(graph, report)
        assert any("skos:definition" in f for f in report.failures)

    def test_missing_owner_fails(self) -> None:
        graph = _minimal_graph()
        graph.remove((URIRef(ENT["Spinning"]), SBP.owner, None))
        report = Report()
        check_annotations(graph, report)
        assert any("sbp:owner" in f for f in report.failures)

    def test_invalid_layer_fails(self) -> None:
        graph = _minimal_graph()
        graph.set((URIRef(ENT["Spinning"]), SBP.layer, Literal("L9")))
        report = Report()
        check_annotations(graph, report)
        assert any("invalid layer" in f for f in report.failures)


class TestProcessRuleAlignment:
    def test_real_ontology_is_aligned(self) -> None:
        """実際のオントロジーと .lp が一致している（回帰防止）。"""
        report = Report()
        check_process_rules(load_ontology(), report)
        assert report.failures == []

    def test_fluent_type_missing_from_lp_fails(self) -> None:
        """TTL にしか無いフルーエント型は検出される（整合残差の計算が壊れるため）。"""
        graph = load_ontology()
        orphan = URIRef(ENT["Overheating"])
        graph.add((orphan, RDF.type, SBP.FluentType))
        graph.add((orphan, RDFS.label, Literal("過熱", lang="ja")))
        graph.add((orphan, RDFS.label, Literal("Overheating", lang="en")))
        graph.add((orphan, SKOS.definition, Literal("テスト用。", lang="ja")))
        graph.add((orphan, SBP.layer, Literal("L2")))
        graph.add((orphan, SBP.owner, Literal("test-owner")))

        report = Report()
        check_process_rules(graph, report)
        assert any("overheating" in f and "not in *.lp" in f for f in report.failures)

    def test_initiates_missing_from_lp_fails(self) -> None:
        """TTL にしか無い initiates は検出される（事象昇格の逆写像が壊れるため）。"""
        graph = load_ontology()
        graph.add((URIRef(ENT["StartCmd"]), SBP.initiates, URIRef(ENT["Inspected"])))
        report = Report()
        check_process_rules(graph, report)
        assert any("initiates" in f and "missing from *.lp" in f for f in report.failures)


class TestExclusiveFluentWarning:
    def test_real_ontology_covers_exclusive_collisions(self) -> None:
        report = Report()
        check_shacl_warning_for_exclusives(load_ontology(), report)
        assert report.failures == []
        assert any("sh:Warning" in n for n in report.notes)

    def test_no_exclusive_declaration_skips_check(self) -> None:
        report = Report()
        check_shacl_warning_for_exclusives(Graph(), report)
        assert report.failures == []
        assert report.notes == []


class TestOntologyContents:
    """オントロジーが設計原則を満たしていることの検査。"""

    def test_type_count_within_minimal_ontology_range(self) -> None:
        """設計原則 P6: 初期は 20〜30 型（フルーエント型・事象型・ドメインクラス）。"""
        graph = load_ontology()
        domain_terms = [t for t in collect_terms(graph) if t.layer in ("L2", "L3")]
        assert 20 <= len(domain_terms) <= 40, (
            f"domain types = {len(domain_terms)}; minimal ontology should stay small (P6)"
        )

    def test_every_event_type_changes_some_fluent(self) -> None:
        """状態を何も変えない事象型は事象計算に乗らない（SHACL EventTypeShape と同趣旨）。"""
        graph = load_ontology()
        initiates, terminates = initiates_terminates(graph)
        changing = {e for e, _ in initiates} | {e for e, _ in terminates}
        for term in event_types(graph):
            assert term.local_name in changing, (
                f"{term.local_name} neither initiates nor terminates any fluent"
            )

    def test_exclusive_declarations_are_symmetric(self) -> None:
        """排他宣言は対称。片側だけでは衝突検出に穴ができる。"""
        graph = load_ontology()
        pairs = {(str(a), str(b)) for a, b in graph.subject_objects(SBP.mutuallyExclusiveWith)}
        for a, b in pairs:
            assert (b, a) in pairs, f"mutuallyExclusiveWith({a}, {b}) is not declared symmetrically"

    def test_every_fluent_type_declares_subject_type(self) -> None:
        graph = load_ontology()
        for term in fluent_types(graph):
            subject_types = list(graph.objects(URIRef(term.iri), SBP.subjectType))
            assert subject_types, f"{term.local_name} has no subjectType"
