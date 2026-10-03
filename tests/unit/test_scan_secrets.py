"""シークレット走査のテスト（不変条件 12、M7-06）。

**陽性対照を取るのが要点である**（``sbp.testing.privacy`` と同じ理屈）。``make check`` が緑に
なったとき、それが「漏れていない」のか「走査が壊れている」のかを区別できなければ意味が無い。
ここでは偽の鍵を食わせて検出されることを確かめる。

テストに書く値はすべて**形だけ本物に似せた偽の値**で、どのプロバイダでも通らない。
"""

from __future__ import annotations

import pytest
from scripts.scan_secrets import Finding, scan_line, should_scan, tracked_files

# 形式だけ本物に似せた偽の値（どれも実在しない）
_FAKE_ANTHROPIC = "sk-ant-" + "A" * 40
_FAKE_AWS = "AKIA" + "B" * 16
_FAKE_GITHUB = "ghp_" + "c" * 36
_FAKE_JWT = (
    # 走査の陽性対照に使う偽の JWT。署名部が偽なのでどこでも検証に通らない
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart"  # noqa: secret 偽の JWT
)


class TestDetection:
    """検出できること（陽性対照）。"""

    @pytest.mark.parametrize(
        ("line", "expected_kind"),
        [
            (f'key = "{_FAKE_ANTHROPIC}"', "Anthropic API key"),
            (f"AWS_ACCESS_KEY_ID={_FAKE_AWS}", "AWS access key id"),
            (f"token: {_FAKE_GITHUB}", "GitHub token"),
            (f'authorization = "Bearer {_FAKE_JWT}"', "JSON Web Token"),
            ("-----BEGIN RSA PRIVATE KEY-----", "private key block"),  # noqa: secret 偽の見出し行
            ('password = "hunter2hunter2hunter2"', "assigned secret"),  # noqa: secret 偽の値
        ],
    )
    def test_patterns_fire(self, line: str, expected_kind: str) -> None:
        """各種の鍵が検出される。"""
        findings = scan_line("some/file.py", 1, line)
        assert expected_kind in {finding.kind for finding in findings}

    def test_finding_does_not_print_the_value(self) -> None:
        """表示に値そのものを出さない（ログに漏らすと走査の意味が無い）。"""
        findings = scan_line("some/file.py", 3, f'key = "{_FAKE_ANTHROPIC}"')
        assert findings
        rendered = findings[0].render()
        assert _FAKE_ANTHROPIC not in rendered
        assert len(findings[0].excerpt) <= 8


class TestFalsePositives:
    """誤検知を抑える。"""

    @pytest.mark.parametrize(
        "line",
        [
            'SBP_PG_DSN = "postgresql://sbp:sbp@localhost:15432/sbp"',
            'ANTHROPIC_API_KEY = "sk-ant-your-key-here"',
            'password = "changeme-in-production"',
            'token = "${SBP_TOKEN}"',
            'secret = "<your-secret>"',
            'api_key = "xxxxxxxxxxxxxxxx"',
        ],
    )
    def test_local_placeholders_are_ignored(self, line: str) -> None:
        """ローカルの既定値・プレースホルダは通す。"""
        assert scan_line(".env.example", 1, line) == []

    def test_noqa_suppresses(self) -> None:
        """明示的な除外は効く（理由を同じ行に書く運用）。"""
        line = f'key = "{_FAKE_ANTHROPIC}"  # noqa: secret テスト用の偽の値'
        assert scan_line("tests/unit/x.py", 1, line) == []

    def test_short_assignments_are_ignored(self) -> None:
        """短い値は鍵ではない（12 文字未満は見ない）。"""
        assert scan_line("src/x.py", 1, 'password = "short"') == []


class TestScope:
    """走査の範囲。"""

    @pytest.mark.parametrize(
        ("name", "scanned"),
        [
            ("src/sbp/api/app.py", True),
            (".env.example", True),
            ("schemas/physical-event.json", False),
            ("src/sbp/core/vocab.py", False),
            ("ontology/imports/prov.ttl", False),
            ("docs/architecture.png", False),
            ("uv.lock", False),
        ],
    )
    def test_generated_and_binary_files_are_skipped(self, name: str, scanned: bool) -> None:
        """生成物とバイナリは対象外（人が鍵を書く場所ではない）。"""
        assert should_scan(name) is scanned

    def test_repository_is_actually_scanned(self) -> None:
        """追跡ファイルが実際に列挙できる（走査が空振りしていないこと）。"""
        files = tracked_files()
        assert len(files) > 50
        assert "CLAUDE.md" in files

    def test_env_itself_is_not_tracked(self) -> None:
        """``.env`` が追跡されていないこと（不変条件 12）。

        ここは走査ロジックではなくリポジトリの状態を見ている。gitignore の書き漏れは
        正規表現では防げない。
        """
        tracked = set(tracked_files())
        assert ".env" not in tracked
        offenders = [
            name
            for name in tracked
            if name.rsplit("/", 1)[-1].startswith(".env.")
            and name.rsplit("/", 1)[-1] != ".env.example"
        ]
        assert offenders == []


class TestFindingRendering:
    """検出結果の表示。"""

    def test_render_includes_location_and_kind(self) -> None:
        """場所と種類が分かる（直しに行ける情報を出す）。"""
        rendered = Finding("src/x.py", 42, "Anthropic API key", "sk-ant-A").render()
        assert "src/x.py:42" in rendered
        assert "Anthropic API key" in rendered
