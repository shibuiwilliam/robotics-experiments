"""シークレット走査（不変条件 12、M7-06）。

> シークレットは ``.env``（gitignore 済み）のみ。コード・テスト・ログ・fixture に出さない。

``make check`` に入れる。**コミット前に落とすのが要点である**——一度コミットしたシークレットは
履歴から消えず、無効化して配り直す以外に回収手段が無い。

走査の対象は**追跡済み ＋ gitignore されていない未追跡**ファイル
（``git ls-files --cached --others --exclude-standard``）。

**未追跡を含めるのが要点である。** 当初は追跡済みだけを見ていたが、それでは
``git add`` される前のファイルが見えない。実際にそれで漏らした——``.envrc``（direnv、
実際の API キー入り）が未追跡のまま置かれ、``make check`` は緑、その後の ``git add -A`` で
staging に入り、そのままコミットされた。**走査が見ていない場所にファイルがある間は、緑は
「漏れていない」を意味しない。**

gitignore されたファイルは除く（``--exclude-standard``）。``.env`` を読まないのはそのためで、
**``.env`` に見える名前が gitignore から漏れていればここで報告される**。

誤検知について: 鍵の形をした文字列は fixture や docstring にも現れうる。``# noqa: secret`` を
行末に書けば除外するが、**理由を同じ行に書くこと**を求める（除外の理由が残らないと、次の人が
「前からあるから良いのだろう」と増やす）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 「鍵らしさ」で引く。プロバイダ固有の接頭辞は、形だけの正規表現より誤検知が少ない
PATTERNS: dict[str, re.Pattern[str]] = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "JSON Web Token": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\."),
    "assigned secret": re.compile(
        r"(?i)\b(?:api_?key|secret|password|passwd|token|credential)\s*[=:]\s*"
        r"['\"][^'\"\s]{12,}['\"]"
    ),
}

# 走査しないもの。生成物とバイナリは対象外（人が書く場所ではない）
SKIP_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2", ".lock", ".pyc"}
)
SKIP_PREFIXES = ("schemas/", "src/sbp/core/vocab.py", "ontology/imports/")

# ローカル開発の既定値は秘密ではない。**これを広げすぎないこと**——「ローカルだから」で
# 本物を通す穴になる。ホスト名が localhost でないものは除外しない
LOCAL_PLACEHOLDERS = re.compile(
    r"(?i)(?:localhost|127\.0\.0\.1|changeme|example|your[-_]|\$\{|<[a-z_]+>|xxx+|\.\.\.)"
)


@dataclass(frozen=True, slots=True)
class Finding:
    """検出 1 件。"""

    path: str
    line_no: int
    kind: str
    excerpt: str

    def render(self) -> str:
        """1 行で表示する。**値そのものは出さない**（ログに漏らすと走査の意味が無い）。"""
        return f"{self.path}:{self.line_no}: {self.kind}（先頭 8 文字 {self.excerpt!r}…）"


def scannable_files() -> list[str]:
    """走査対象のファイル（追跡済み + gitignore されていない未追跡）。

    ``--others`` で未追跡を、``--exclude-standard`` で gitignore 済みの除外を効かせる。
    **未追跡を含めないと、``git add`` される前のファイルを見逃す**（モジュールの docstring 参照）。
    """
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    # 追跡済みと未追跡で同じ名前が二度出ることはないが、念のため重複を落とす
    return sorted({name for name in result.stdout.split("\0") if name})


def should_scan(name: str) -> bool:
    """走査対象か。"""
    if name.startswith(SKIP_PREFIXES):
        return False
    return Path(name).suffix.lower() not in SKIP_SUFFIXES


def scan_line(path: str, line_no: int, line: str) -> list[Finding]:
    """1 行を走査する。"""
    if "noqa: secret" in line:
        return []
    findings: list[Finding] = []
    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(line):
            value = match.group(0)
            if LOCAL_PLACEHOLDERS.search(value):
                continue
            findings.append(Finding(path, line_no, kind, value[:8]))
    return findings


def scan() -> list[Finding]:
    """追跡対象の全ファイルを走査する。"""
    findings: list[Finding] = []
    for name in scannable_files():
        # .env そのものが追跡されていたら、中身を読む前にそれを報告する（gitignore の書き漏れ）
        base = Path(name).name
        if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
            findings.append(Finding(name, 0, "gitignore されていない .env ファイル", base))
            continue
        if base == ".envrc" or base.startswith(".envrc."):
            findings.append(Finding(name, 0, "gitignore されていない .envrc ファイル", base))
            continue
        if not should_scan(name):
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            findings.extend(scan_line(name, line_no, line))
    return findings


def main() -> int:
    """走査して結果を返す。

    Returns:
        終了コード（検出があれば 1）。
    """
    findings = scan()
    if not findings:
        sys.stdout.write(
            f"✅ secret scan: {len(scannable_files())} 件（追跡済み + 未追跡）に疑わしい値なし\n"
        )
        return 0
    sys.stderr.write(f"❌ secret scan: {len(findings)} 件検出（不変条件 12）\n")
    for finding in findings:
        sys.stderr.write(f"  {finding.render()}\n")
    sys.stderr.write(
        "\n値そのものは表示していない。本物なら: (1) 鍵を無効化して配り直す "
        "(2) .env に移す (3) 履歴から除去する。誤検知なら行末に "
        "`# noqa: secret （理由）` を付ける。\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
