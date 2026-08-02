"""Architecture-invariant tests (CLAUDE.md §0, §7). These are must-pass guards, enforced from
Phase P0 so violations surface the moment they are introduced.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGES = (
    "ontology",
    "core",
    "sim",
    "perception",
    "agents",
    "external",
    "bench",
    "scoreboard",
    "config",
)


def _python_files(pkg: str) -> list[Path]:
    root = _ROOT / pkg
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "generated" not in p.parts]


def test_no_llm_sdk_import_outside_clients() -> None:
    """Golden Rule #1 (generalized): NO LLM SDK — Gemini or Anthropic — may be imported outside
    clients/. The single cloud choke point now spans multiple providers (DECISIONS D-0012)."""
    from clients.guard import llm_imports_outside_clients

    offenders = llm_imports_outside_clients()
    assert not offenders, f"LLM SDK imported outside clients/: {offenders}"


def _naive_wallclock_calls(path: Path) -> list[str]:
    """AST-detect actual calls to time.time / datetime.now / datetime.utcnow (not prose)."""
    banned = {("time", "time"), ("datetime", "now"), ("datetime", "utcnow")}
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            base = node.func.value
            base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
            if (base_name, attr) in banned:
                found.append(f"{base_name}.{attr}()")
    return found


def test_no_naive_wallclock_on_production_paths() -> None:
    """Golden Rule #5: no bare time.time()/datetime.now() *calls* on production paths."""
    allow_dirs = {"scoreboard"}  # read-only observer may timestamp reports
    offenders: list[str] = []
    for pkg in _PACKAGES:
        if pkg in allow_dirs:
            continue
        for path in _python_files(pkg):
            for call in _naive_wallclock_calls(path):
                offenders.append(f"{path.relative_to(_ROOT)} :: {call}")
    assert not offenders, f"naive wall-clock on production path: {offenders}"
