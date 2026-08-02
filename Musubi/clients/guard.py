"""Architecture guard — the single cloud choke point (CLAUDE.md §0-1, generalized).

All LLM SDKs (Gemini AND Anthropic) may be imported ONLY inside ``clients/``. This module scans the
source tree for violations; it is used by both the invariant test and the console ``doctor``.
Generalizing from "Gemini-only" to "any LLM provider" keeps the choke point intact while allowing
Claude to serve as an agent backend (see IMPROVEMENT.md / DECISIONS D-0012).
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: Top-level packages that must NOT import an LLM SDK directly (clients/ is the allowed home).
GUARDED_PACKAGES = (
    "ontology",
    "core",
    "sim",
    "perception",
    "agents",
    "external",
    "bench",
    "scoreboard",
    "config",
    "console",
)

#: Import prefixes that constitute a direct LLM-SDK dependency.
LLM_SDK_PREFIXES = ("google.genai", "google.adk", "anthropic")


def _imports_llm(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.startswith(LLM_SDK_PREFIXES) for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith(LLM_SDK_PREFIXES) or mod == "anthropic":
                return True
    return False


def llm_imports_outside_clients() -> list[str]:
    """Return repo-relative paths (outside ``clients/``) that import an LLM SDK. Empty == clean."""
    offenders: list[str] = []
    for pkg in GUARDED_PACKAGES:
        root = _ROOT / pkg
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "generated" in path.parts:
                continue
            if _imports_llm(path):
                offenders.append(str(path.relative_to(_ROOT)))
    return offenders
