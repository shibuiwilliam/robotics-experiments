"""``ontology/`` から ``src/sbp/core/vocab.py`` を生成する（``make gen``）。

不変条件 5 により、コードは IRI を直書きせず必ず ``sbp.core.vocab`` から参照する。生成物は
手編集しても次の ``make gen`` で消える（``.claude/settings.json`` が直接編集を拒否する）。

Python からは ``vocab.ent.Running`` のように参照する（CLAUDE.md 命名規約）。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from scripts._ontology import (
    NAMESPACES,
    VOCAB_PATH,
    Term,
    collect_terms,
    load_ontology,
    ontology_version,
)

_HEADER = '''"""オントロジー語彙（生成物 — 手編集しない）。

``make gen`` が ``ontology/`` の TTL から生成する。不変条件 5 により、コードは IRI を直書きせず
必ずこのモジュールから参照する。

- ``vocab.core.<LocalName>`` — L1 中核（``https://sbp.example/core#``）
- ``vocab.ent.<LocalName>`` — ドメイン・企業層（``https://sbp.example/ent#``）

生成元オントロジー版: {version}
"""

from __future__ import annotations

from typing import Final

ONTOLOGY_VERSION: Final = "{version}"
"""この語彙を生成したオントロジーの版（アサーションの ``ontology_version`` に入れる）。"""

CORE_NAMESPACE: Final = "https://sbp.example/core#"
ENT_NAMESPACE: Final = "https://sbp.example/ent#"
'''

_NAMESPACE_CLASS = '''

class _{class_name}:
    """{doc}"""

    __slots__ = ()
'''

_FOOTER = '''

core: Final = _Core()
"""L1 中核語彙。"""

ent: Final = _Ent()
"""ドメイン・企業層の語彙。"""

ALL_IRIS: Final[frozenset[str]] = frozenset(
    {all_iris}
)
"""この語彙が定義する全 IRI。``validate_iri`` の既知語彙チェックに使う。"""

FLUENT_TYPES: Final[frozenset[str]] = frozenset(
    {fluent_iris}
)
"""フルーエント型の IRI（事象計算の holdsAt の対象）。"""

EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {event_iris}
)
"""事象型の IRI（JSON Schema の生成元）。"""
'''


def _render_attribute(term: Term) -> str:
    label = term.label_ja or term.label_en or term.local_name
    definition = (term.definition or "").replace("\n", " ")
    comment = f"{label}（{term.kind}, {term.layer or '-'}）"
    doc = f'    """{comment}\n\n    {definition}\n    """' if definition else f'    """{comment}"""'
    return f'    {term.local_name}: Final = "{term.iri}"\n{doc}\n'


def render(terms: list[Term], version: str) -> str:
    """vocab.py の内容を組み立てる。"""
    parts = [_HEADER.format(version=version)]
    for prefix in NAMESPACES:
        class_name = prefix.capitalize()
        docs = {
            "core": "L1 中核語彙（``https://sbp.example/core#``）。",
            "ent": "ドメイン・企業層の語彙（``https://sbp.example/ent#``）。",
        }
        parts.append(_NAMESPACE_CLASS.format(class_name=class_name, doc=docs[prefix]))
        members = [t for t in terms if t.prefix == prefix]
        if not members:
            parts.append("\n    pass\n")
            continue
        parts.append("\n")
        parts.extend(_render_attribute(t) + "\n" for t in members)

    def iri_set(predicate: Callable[[Term], bool]) -> str:
        selected = sorted(t.iri for t in terms if predicate(t))
        if not selected:
            return "()"
        inner = ",\n        ".join(f'"{iri}"' for iri in selected)
        return "(\n        " + inner + ",\n    )"

    parts.append(
        _FOOTER.format(
            all_iris=iri_set(lambda _: True),
            fluent_iris=iri_set(lambda t: t.kind == "fluent_type"),
            event_iris=iri_set(lambda t: t.kind == "event_type"),
        )
    )
    return "".join(parts)


def _normalize(path: Path) -> None:
    """生成したファイルを ruff で整形して出力を正規化する。

    整形を生成の一部にしないと、``make lint`` の整形で生成物が書き換わり、
    ``make ontology-check`` の鮮度チェックが毎回「古い」と報告する循環に陥る。
    """
    subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--quiet", str(path)],
        cwd=path.parents[4],
        capture_output=True,
        check=False,
    )


def main() -> int:
    """vocab.py を生成して差分の有無を標準出力に示す。"""
    graph = load_ontology()
    terms = collect_terms(graph)
    content = render(terms, ontology_version())
    previous = VOCAB_PATH.read_text(encoding="utf-8") if VOCAB_PATH.exists() else None
    VOCAB_PATH.write_text(content, encoding="utf-8")
    _normalize(VOCAB_PATH)
    changed = previous != VOCAB_PATH.read_text(encoding="utf-8")
    sys.stdout.write(
        f"gen_vocab: {len(terms)} terms -> {VOCAB_PATH.relative_to(VOCAB_PATH.parents[4])}"
        f" ({'changed' if changed else 'unchanged'})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
