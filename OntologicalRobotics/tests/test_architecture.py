"""アーキテクチャ不変条件テスト (PROJECT.md §5.2 / CLAUDE.md §3)。

import-linter の契約（pyproject.toml [tool.importlinter]）を実行し、
モジュール依存方向と oracle/ORコア分離が守られていることを検証する。
契約違反はこのテストの失敗として現れる。
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_import_contracts() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint_imports"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import-linter 契約違反:\n{result.stdout}\n{result.stderr}"
    )
