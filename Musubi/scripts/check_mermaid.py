"""Lightweight, dependency-free Mermaid syntax check for Markdown (``make docs-check``).

Scans ``*.md`` for ```mermaid fenced blocks and validates:
  - the block is non-empty,
  - the first non-comment line names a known diagram type,
  - brackets/parens/braces are balanced.

Not a full Mermaid parser — a fast guard against broken diagrams committed to docs.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DIAGRAM_TYPES = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "journey",
    "gantt",
    "pie",
    "mindmap",
    "timeline",
    "quadrantChart",
    "gitGraph",
    "C4Context",
)

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "build", "dist", "ontology/generated"}


def _iter_md(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.md"):
        rel = p.relative_to(root).as_posix()
        if any(rel.startswith(s) or f"/{s}/" in f"/{rel}" for s in _SKIP_DIRS):
            continue
        out.append(p)
    return sorted(out)


def _check_block(lines: list[str]) -> str | None:
    body = [ln for ln in lines if ln.strip()]
    if not body:
        return "empty mermaid block"
    first = next((ln.strip() for ln in body if not ln.strip().startswith("%%")), "")
    if not any(first.startswith(t) for t in _DIAGRAM_TYPES):
        return f"unknown diagram type: {first!r}"
    text = "\n".join(lines)
    for open_c, close_c in (("(", ")"), ("[", "]"), ("{", "}")):
        if text.count(open_c) != text.count(close_c):
            return f"unbalanced {open_c}{close_c}"
    return None


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    in_block = False
    block: list[str] = []
    start_line = 0
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not in_block and stripped.startswith("```mermaid"):
            in_block, block, start_line = True, [], i
            continue
        if in_block and stripped.startswith("```"):
            problem = _check_block(block)
            if problem:
                errors.append(f"{path}:{start_line}: {problem}")
            in_block = False
            continue
        if in_block:
            block.append(raw)
    if in_block:
        errors.append(f"{path}:{start_line}: unterminated mermaid block")
    return errors


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv[0]).resolve() if argv else Path.cwd()
    all_errors: list[str] = []
    files = _iter_md(root)
    for f in files:
        all_errors.extend(check_file(f))
    if all_errors:
        for e in all_errors:
            print(f"MERMAID ERROR: {e}")
        print(f"\n{len(all_errors)} mermaid error(s) across {len(files)} file(s).")
        return 1
    print(f"docs-check: {len(files)} markdown file(s) OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
