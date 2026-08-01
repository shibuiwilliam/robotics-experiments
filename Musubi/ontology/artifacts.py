"""Access + validation layer over the generated ontology artifacts.

The rest of Musubi reaches the ontology THROUGH this module — never by re-reading files or
re-deriving schemas. Provides:

  - the JSON Schema (whole + per-class), for structured-output validation,
  - the JSON-LD @context, for the semantic envelope,
  - JSON-LD -> RDF conversion and SHACL validation (generated shapes + world_ok acceptance).

Everything is cached; artifacts are produced by ``make gen`` (CLAUDE.md §0-4).
"""

from __future__ import annotations

import json
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

_GEN = Path(__file__).resolve().parent / "generated"
_SHAPES = Path(__file__).resolve().parent / "shapes"

JSON_SCHEMA_PATH = _GEN / "musubi.schema.json"
CONTEXT_PATH = _GEN / "context.jsonld"
SHACL_PATH = _GEN / "musubi.shacl.ttl"
WORLD_OK_PATH = _SHAPES / "world_ok.ttl"

#: Namespace of every generated ontology term.
MSB = "https://musubi.dev/ontology/musubi/"


class ArtifactsMissing(RuntimeError):
    """Raised when a generated artifact is absent — run ``make gen``."""


def _require(path: Path) -> Path:
    if not path.exists():
        raise ArtifactsMissing(f"missing ontology artifact {path}; run `make gen`")
    return path


@lru_cache(maxsize=1)
def json_schema() -> dict[str, Any]:
    """The whole generated JSON Schema (all classes under ``$defs``)."""
    return json.loads(_require(JSON_SCHEMA_PATH).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def jsonld_context() -> dict[str, Any]:
    """The generated JSON-LD @context (prefix map + class/slot/enum terms)."""
    return json.loads(_require(CONTEXT_PATH).read_text(encoding="utf-8"))


@cache
def class_schema(class_name: str) -> dict[str, Any]:
    """A standalone JSON Schema validating a single class instance (``$ref`` into ``$defs``)."""
    whole = json_schema()
    defs = whole.get("$defs", whole.get("definitions", {}))
    if class_name not in defs:
        raise KeyError(f"class {class_name!r} not in generated JSON Schema $defs")
    return {"$ref": f"#/$defs/{class_name}", "$defs": defs}


def validate_instance(instance: dict[str, Any], class_name: str) -> None:
    """Validate ``instance`` against the generated schema for ``class_name``.

    Raises ``jsonschema.ValidationError`` on mismatch (structured-output guard, CLAUDE.md §8).
    """
    import jsonschema

    jsonschema.validate(instance, class_schema(class_name))


def instance_is_valid(instance: dict[str, Any], class_name: str) -> bool:
    """Boolean form of :func:`validate_instance`."""
    import jsonschema

    try:
        validate_instance(instance, class_name)
        return True
    except jsonschema.ValidationError:
        return False


def to_rdf(jsonld_doc: dict[str, Any] | list[dict[str, Any]]) -> Any:
    """Parse a JSON-LD document (nodes with @id/@type) into an rdflib Graph.

    The generated @context is injected when absent so callers pass plain node dicts.
    """
    from rdflib import Graph

    if isinstance(jsonld_doc, list):
        doc: dict[str, Any] = {"@context": jsonld_context()["@context"], "@graph": jsonld_doc}
    else:
        doc = dict(jsonld_doc)
        doc.setdefault("@context", jsonld_context()["@context"])
    graph = Graph()
    graph.parse(data=json.dumps(doc), format="json-ld")
    return graph


@lru_cache(maxsize=1)
def _shapes_text() -> str:
    """Generated schema-level SHACL + hand-authored world_ok acceptance shapes, combined."""
    from rdflib import Graph

    g = Graph()
    g.parse(_require(SHACL_PATH), format="turtle")
    g.parse(_require(WORLD_OK_PATH), format="turtle")
    return g.serialize(format="turtle")


def validate_world(
    jsonld_doc: dict[str, Any] | list[dict[str, Any]],
    *,
    include_generated: bool = True,
) -> tuple[bool, str]:
    """SHACL-validate a world state (JSON-LD) against acceptance shapes.

    Returns ``(conforms, report_text)``. ``world_ok.ttl`` enforces the invariant floors
    (privacy floor, claim sanity, reversibility). Used by scenario oracles.
    """
    from pyshacl import validate as shacl_validate
    from rdflib import Graph

    data_graph = to_rdf(jsonld_doc)
    if include_generated:
        shapes_graph = Graph()
        shapes_graph.parse(data=_shapes_text(), format="turtle")
    else:
        shapes_graph = Graph()
        shapes_graph.parse(_require(WORLD_OK_PATH), format="turtle")
    conforms, _results_graph, report_text = shacl_validate(
        data_graph, shacl_graph=shapes_graph, inference="none", advanced=True
    )
    return bool(conforms), str(report_text)
