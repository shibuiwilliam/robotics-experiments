"""`_git_commit()` の dirty 反映テスト（IMPROVEMENT.md R-3f）。

subprocess を差し替えて clean / dirty / 取得不能を決定的に検証する（リポジトリ状態に非依存）。
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from orx.exp import episode


def _fake_run(hash_out: str, porcelain_out: str):
    """git rev-parse / git status を模した subprocess.run スタブを返す。"""

    def runner(cmd, **kwargs):  # noqa: ANN001
        if "rev-parse" in cmd:
            return SimpleNamespace(stdout=hash_out, returncode=0)
        if "status" in cmd:
            return SimpleNamespace(stdout=porcelain_out, returncode=0)
        raise AssertionError(f"unexpected git command: {cmd}")

    return runner


def test_clean_tree_has_no_dirty_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run("abc1234\n", ""))
    assert episode._git_commit() == "abc1234"


def test_dirty_tree_gets_dirty_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run("abc1234\n", " M src/orx/x.py\n"))
    assert episode._git_commit() == "abc1234-dirty"


def test_missing_commit_returns_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run("", ""))
    assert episode._git_commit() == "unknown"


def test_oserror_returns_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert episode._git_commit() == "unknown"


def test_dirty_check_scoped_to_orx_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """dirty 判定は ORX ツリー（repo_root）に限定される（IMPROVEMENT.md D-1）。

    git リポジトリのトップレベルが ORX の親ディレクトリでも、兄弟プロジェクトの
    未コミット変更で ORX の結果が -dirty に汚染されてはならない。
    """
    from orx.common.paths import repo_root

    calls: list[tuple[list, dict]] = []

    def runner(cmd, **kwargs):  # noqa: ANN001
        calls.append((cmd, kwargs))
        if "rev-parse" in cmd:
            return SimpleNamespace(stdout="abc1234\n", returncode=0)
        if "status" in cmd:
            return SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr(subprocess, "run", runner)
    assert episode._git_commit() == "abc1234"
    status_cmd, status_kwargs = next((c, k) for c, k in calls if "status" in c)
    assert status_cmd[-2:] == ["--", "."]  # パス指定で ORX ツリーに限定
    assert status_kwargs.get("cwd") == repo_root()  # cwd も ORX ルート
