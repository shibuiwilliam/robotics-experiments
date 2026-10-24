"""Makefile の入口の単体テスト（X-12、``docs/PROGRESS.md`` 所見 111）。

``make -n``（実行せずにコマンドを表示する）で、compose に渡る引数だけを確かめる。

重点:

1. **profile を複数指定できる** — ``PROFILES=ui,observability`` が ``--profile ui --profile
   observability`` になること。``--profile ui,observability`` の 1 引数になると compose は
   それを 1 つの未知の profile として扱い、**基本 4 サービスだけが黙って起動する**。
2. **指定しなければ profile を付けない** — 既定は基本 4 サービス（CLAUDE.md のインフラ表）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _dry_run(*args: str) -> str:
    """``make -n`` の出力（実行はしない）。"""
    result = subprocess.run(
        ["make", "-n", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _compose_up_line(output: str) -> str:
    """出力のうち ``docker compose ... up`` の行。"""
    lines = [line for line in output.splitlines() if "docker compose" in line and " up" in line]
    assert len(lines) == 1, output
    return lines[0]


class TestComposeProfiles:
    def test_several_profiles_become_several_flags(self) -> None:
        line = _compose_up_line(_dry_run("up", "PROFILES=ui,observability"))
        assert "--profile ui --profile observability" in line
        assert "ui,observability" not in line

    def test_a_single_profile(self) -> None:
        line = _compose_up_line(_dry_run("up", "PROFILES=observability"))
        assert "--profile observability" in line

    def test_no_profile_by_default(self) -> None:
        line = _compose_up_line(_dry_run("up"))
        assert "--profile" not in line

    @pytest.mark.parametrize("spec", ["ui, observability", "ui,,observability"])
    def test_blank_entries_are_dropped(self, spec: str) -> None:
        """空白や空要素で ``--profile`` だけが残ると compose の引数が壊れる。"""
        line = _compose_up_line(_dry_run("up", f"PROFILES={spec}"))
        assert "--profile ui --profile observability" in line
