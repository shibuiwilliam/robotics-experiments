"""Makefile の入口の単体テスト（X-12、``docs/PROGRESS.md`` 所見 111）。

``make -n``（実行せずにコマンドを表示する）で、compose に渡る引数だけを確かめる。

重点:

1. **profile を複数指定できる** — ``PROFILES=ui,observability`` が ``--profile ui --profile
   observability`` になること。``--profile ui,observability`` の 1 引数になると compose は
   それを 1 つの未知の profile として扱い、**基本 4 サービスだけが黙って起動する**。
2. **指定しなければ profile を付けない** — 既定は基本 4 サービス（CLAUDE.md のインフラ表）。
3. **リポジトリ直下の ``.env`` を compose に渡す**（X-14） — compose は既定で compose ファイルの
   ディレクトリ（``infra/``）の ``.env`` しか読まないので、``-f infra/compose.yaml`` だけでは
   直下の ``.env`` に書いたポート退避（``SBP_PORT_*``）やプラットフォームが黙って無視される。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _dry_run(*args: str, cwd: Path = ROOT) -> str:
    """``make -n`` の出力（実行はしない）。

    Args:
        args: make の引数。
        cwd: 実行するディレクトリ（``.env`` の有無を切り替えるため）。Makefile は常に
            ``ROOT`` のものを使う。
    """
    result = subprocess.run(
        ["make", "-n", "-f", str(ROOT / "Makefile"), *args],
        cwd=cwd,
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


class TestComposeEnvFile:
    """直下の ``.env`` が compose の変数展開に効くこと（X-14、所見 114）。"""

    def test_the_root_dotenv_is_passed_to_compose(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SBP_PORT_POSTGRES=15432\n", encoding="utf-8")
        line = _compose_up_line(_dry_run("up", cwd=tmp_path))
        assert "--env-file .env" in line

    def test_without_a_dotenv_no_env_file_is_passed(self, tmp_path: Path) -> None:
        """``.env`` が無いときに ``--env-file`` を付けると compose が起動前に落ちる。"""
        line = _compose_up_line(_dry_run("up", cwd=tmp_path))
        assert "--env-file" not in line
