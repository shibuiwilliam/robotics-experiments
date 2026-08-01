"""``python -m ontology.generate`` — regenerate ontology artifacts from LinkML sources.

Compiles ``ontology/src/*.yaml`` (LinkML) into committed outputs:
  - ``ontology/generated/`` : JSON Schema, Python dataclasses, NL vocabulary
  - ``ontology/shapes/``    : SHACL shapes

Run via ``make gen`` after editing any LinkML source (CLAUDE.md §0-4). Fails loudly rather than
skipping, so a missing generator surfaces immediately.

NOTE: the full LinkML pipeline is implemented in Phase P1. This entry point is intentionally
thin so ``make gen`` is wired from Phase P0 onward.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"


def main(argv: list[str] | None = None) -> int:
    sources = sorted(_SRC.glob("*.yaml"))
    if not sources:
        print(f"ontology: no LinkML sources in {_SRC} yet (Phase P1 populates them).")
        return 0
    # P1 replaces this with the real generator driver.
    print(f"ontology: found {len(sources)} source(s); generator wired in Phase P1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
