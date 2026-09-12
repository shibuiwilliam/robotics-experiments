"""盲検境界の検査（`.claude/rules/experiments.md`）。

`grounding/` と `wm/` は `data/injections` を読んではならず、
`gtwm.sim.wms_mock` の注入関連シンボル（`InjectionConfig` / `apply_injections`）を
import してはならない。AST を静的解析し、import 文とソース中の直接参照を検査する
（実行時に import されるかどうかに関わらず、コード上に痕跡があれば検知する）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gtwm.utils.paths import repo_root

pytestmark = pytest.mark.unit

_FORBIDDEN_MODULES = {"gtwm.sim.wms_mock"}
_FORBIDDEN_NAMES = {"InjectionConfig", "apply_injections"}
_FORBIDDEN_PATH_SUBSTRING = "data/injections"
_WATCHED_PACKAGES = ["grounding", "wm"]


def _iter_py_files() -> list[Path]:
    src = repo_root() / "src" / "gtwm"
    files: list[Path] = []
    for pkg in _WATCHED_PACKAGES:
        files.extend((src / pkg).rglob("*.py"))
    return files


def _check_file(path: Path) -> list[str]:
    violations: list[str] = []
    source = path.read_text(encoding="utf-8")
    if _FORBIDDEN_PATH_SUBSTRING in source:
        violations.append(f"{path}: 'data/injections' への直接参照がある")

    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FORBIDDEN_MODULES:
                    violations.append(f"{path}: import {alias.name} は禁止（盲検境界違反）")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _FORBIDDEN_MODULES:
                violations.append(f"{path}: from {module} import ... は禁止（盲検境界違反）")
            for alias in node.names:
                if alias.name in _FORBIDDEN_NAMES:
                    violations.append(f"{path}: {alias.name} の import は禁止（注入関連シンボル）")
    return violations


def test_grounding_and_wm_do_not_import_injection_symbols() -> None:
    all_violations: list[str] = []
    for path in _iter_py_files():
        all_violations.extend(_check_file(path))
    assert not all_violations, "盲検境界違反:\n" + "\n".join(all_violations)


def test_watched_packages_actually_exist() -> None:
    # 誤って空ディレクトリを検査して「常に緑」になっていないことを保証する。
    assert len(_iter_py_files()) > 5
