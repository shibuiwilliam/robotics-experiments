"""Ontology — the single source of meaning.

LinkML sources in ``src/`` are compiled by ``python -m ontology.generate`` into ``generated/``
(JSON Schema, Python types, NL vocabulary) and ``shapes/`` (SHACL). Every module references the
same generated artifacts; types/schemas/vocabulary are never hand-written elsewhere
(CLAUDE.md §0-4). Generated outputs are committed for reproducibility.
"""

from __future__ import annotations
