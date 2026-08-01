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


def _imports_gemini(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(("google.genai", "google.adk")):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith(("google.genai", "google.adk")):
                return True
    return False


def test_no_gemini_import_outside_clients() -> None:
    """Golden Rule #1: Gemini SDKs may only be imported inside clients/."""
    offenders: list[str] = []
    for pkg in _PACKAGES:  # note: clients/ deliberately excluded
        for path in _python_files(pkg):
            if _imports_gemini(path):
                offenders.append(str(path.relative_to(_ROOT)))
    assert not offenders, f"Gemini imported outside clients/: {offenders}"


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
