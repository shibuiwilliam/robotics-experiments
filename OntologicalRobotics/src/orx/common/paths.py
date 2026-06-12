"""リポジトリ内の標準パス解決。

CLI・テストはリポジトリルートから実行される前提だが、サブディレクトリ実行にも
耐えるよう pyproject.toml を上方探索する。
"""

from __future__ import annotations

from pathlib import Path


def repo_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


def ontology_dir() -> Path:
    return repo_root() / "ontology"


def mappings_dir() -> Path:
    return ontology_dir() / "mappings"


def shapes_dir() -> Path:
    return ontology_dir() / "shapes"


def runs_root() -> Path:
    return repo_root() / "data" / "runs"


def reports_dir() -> Path:
    return repo_root() / "reports"


def cq_dir() -> Path:
    return repo_root() / "tasks" / "competency_questions"
