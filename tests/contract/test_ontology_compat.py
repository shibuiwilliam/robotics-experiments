"""契約テスト: オントロジーと生成物（``vocab.py`` / ``schemas/``）の互換性。

ontology ルールの「MINOR までは既存プローブ・クエリ・``schemas/`` の後方互換を
``tests/contract/test_ontology_compat.py`` で証明する」に対応する。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from rdflib.namespace import DCTERMS, OWL
from scripts._ontology import load_ontology

from sbp.core import vocab
from sbp.core.ids import validate_iri

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"
ONTOLOGY_DIR = REPO_ROOT / "ontology"


class TestEventTypeSchemas:
    def test_every_event_type_has_a_schema(self) -> None:
        """オントロジーの全事象型に対応するスキーマが生成されている。"""
        generated = {
            p.stem.removesuffix(".schema") for p in (SCHEMAS_DIR / "events").glob("*.json")
        }
        assert len(generated) == len(vocab.EVENT_TYPES)

    def test_event_schemas_are_valid_and_pin_the_type(self) -> None:
        """各スキーマは妥当で、``type`` を当該事象型の IRI に固定している。"""
        for path in sorted((SCHEMAS_DIR / "events").glob("*.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            pinned = schema["properties"]["type"]["const"]
            assert pinned in vocab.EVENT_TYPES, f"{path.name} pins unknown event type {pinned}"


class TestVocabulary:
    def test_vocab_exposes_namespaces(self) -> None:
        """``vocab.core`` / ``vocab.ent`` から参照できる（CLAUDE.md 命名規約）。"""
        assert vocab.core.Situation.startswith(vocab.CORE_NAMESPACE)
        assert vocab.ent.Running.startswith(vocab.ENT_NAMESPACE)

    def test_design_document_core_concepts_exist(self) -> None:
        """設計書 §3.3 が挙げる L1 接続概念がすべて語彙にある。"""
        for name in (
            "Situation",
            "Discrepancy",
            "Expectation",
            "IdentityClaim",
            "Track",
            "Anchor",
            "Probe",
            "WorldModelVersion",
            "OntologyVersion",
            "DataSource",
        ):
            assert hasattr(vocab.core, name), f"core concept {name} is missing from vocab"

    def test_equipment_maintenance_fluents_and_events_exist(self) -> None:
        """ADR-0002 の初期ドメインと設計書 §3.8 の例が語彙にある。"""
        for name in ("Running", "UnderMaintenance", "Faulted", "VibrationAnomaly"):
            assert getattr(vocab.ent, name) in vocab.FLUENT_TYPES
        for name in ("StartCmd", "StopCmd", "Fault", "MaintStart", "MaintEnd"):
            assert getattr(vocab.ent, name) in vocab.EVENT_TYPES

    def test_all_iris_covers_fluent_and_event_types(self) -> None:
        assert vocab.FLUENT_TYPES <= vocab.ALL_IRIS
        assert vocab.EVENT_TYPES <= vocab.ALL_IRIS

    def test_vocab_iris_are_valid(self) -> None:
        """語彙の全 IRI が ``validate_iri`` を通る（不変条件 5 の経路が一貫している）。"""

        for iri in vocab.ALL_IRIS:
            validate_iri(iri)

    def test_ontology_version_matches_file(self) -> None:
        """``vocab.ONTOLOGY_VERSION`` が ``ontology/VERSION`` と一致する。"""
        expected = (REPO_ROOT / "ontology" / "VERSION").read_text(encoding="utf-8").strip()
        assert expected == vocab.ONTOLOGY_VERSION


class TestSemverCompatibility:
    """版管理の規約（ontology ルール・CLAUDE.md）が守られていることの検査。"""

    def test_version_is_semver(self) -> None:
        version = (ONTOLOGY_DIR / "VERSION").read_text(encoding="utf-8").strip()
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"VERSION is not semver: {version!r}"

    def test_changelog_documents_current_version(self) -> None:
        """現行版の節が CHANGELOG にあり、semver 判定が書かれている。"""
        version = (ONTOLOGY_DIR / "VERSION").read_text(encoding="utf-8").strip()
        changelog = (ONTOLOGY_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## {version}" in changelog
        assert "semver 判定" in changelog

    def test_generated_artifacts_declare_their_source_version(self) -> None:
        """生成物が生成元のオントロジー版を保持している（再接地の判断に必要）。"""
        assert (ONTOLOGY_DIR / "VERSION").read_text(
            encoding="utf-8"
        ).strip() == vocab.ONTOLOGY_VERSION
        envelope = json.loads(
            (SCHEMAS_DIR / "event-envelope.schema.json").read_text(encoding="utf-8")
        )
        assert vocab.ONTOLOGY_VERSION in envelope["description"]

    def test_no_deprecated_term_without_replacement(self) -> None:
        """非推奨には後継指定を必須にする（ontology ルール）。"""

        graph = load_ontology()
        for term in graph.subjects(OWL.deprecated, None):
            replacements = list(graph.objects(term, DCTERMS.isReplacedBy))
            assert replacements, f"{term} is deprecated without dcterms:isReplacedBy"
