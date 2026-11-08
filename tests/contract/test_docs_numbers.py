"""文書の数値と実装の突き合わせ（X-07、所見 99・138）。

人が書いた数値は人が直すまで古い。**書いてある場所が決まっているものだけ**を機械で検査する
（STATUS の規模の表、README の ADR 数、HITL の状態別件数、ADR 参照の実在）。
**テスト件数は検査しない**——テストを足すたびに文書が落ちるのは本末転倒で、
件数は測った日と測り方を併記して書く。
"""

from __future__ import annotations

import importlib.util
import re
from collections import Counter
from pathlib import Path

from sbp.core.metrics import METRICS

ROOT = Path(__file__).resolve().parents[2]
STATUS = (ROOT / "docs/STATUS.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
HITL = (ROOT / "docs/HITL.md").read_text(encoding="utf-8")

_ADR_FILES = sorted(p for p in (ROOT / "docs/adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
_ADR_NUMBERS = [int(p.name[:4]) for p in _ADR_FILES if p.name[:4] != "0000"]
_MIGRATIONS = sorted((ROOT / "infra/sql/versions").glob("[0-9][0-9][0-9][0-9]_*.py"))


def _claim(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    assert match, f"文書に書いてあるはずの記述が見つからない: {pattern}"
    return int(match.group(1))


def test_adr_numbers_are_unique_and_contiguous() -> None:
    assert list(range(1, len(_ADR_NUMBERS) + 1)) == _ADR_NUMBERS, "ADR の番号が連続していない"


def test_the_adr_count_in_status_and_readme_matches_the_files() -> None:
    assert _claim(STATUS, r"\| ADR \| (\d+) 件") == len(_ADR_NUMBERS)
    assert _claim(README, r"MADR 形式、(\d+) 件") == len(_ADR_NUMBERS)


def test_migrations_form_one_contiguous_chain_and_status_counts_them() -> None:
    numbers = [int(p.name[:4]) for p in _MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1)), "マイグレーションの連番が飛んでいる"
    for path in _MIGRATIONS:
        text = path.read_text(encoding="utf-8")
        number = path.name[:4]
        previous = f"{int(number) - 1:04d}"
        assert re.search(rf'^revision = "{number}"', text, re.M), f"{path.name}: revision"
        expected = "None" if number == "0001" else f'"{previous}"'
        assert re.search(rf"^down_revision = {expected}", text, re.M), (
            f"{path.name}: 鎖が切れている"
        )
    assert _claim(STATUS, r"\| DB マイグレーション \| (\d+)") == len(_MIGRATIONS)


def test_status_counts_the_workers() -> None:
    workers = list((ROOT / "services").glob("*_worker.py"))
    assert _claim(STATUS, r"\| ワーカー \| (\d+)") == len(workers)


def _wired_count() -> int:
    path = ROOT / "tests/unit/test_metrics_wiring.py"
    spec = importlib.util.spec_from_file_location("_metrics_wiring_for_docs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return len(module.WIRED)


def test_status_counts_the_metrics_and_the_wired_ones() -> None:
    assert _claim(STATUS, r"6 領域 (\d+) 指標") == len(METRICS)
    assert _claim(STATUS, r"書き込み配線済みは (\d+)") == _wired_count()


def test_status_ontology_version_is_the_current_one() -> None:
    version = (ROOT / "ontology/VERSION").read_text(encoding="utf-8").strip()
    assert f"| オントロジー | {version}（" in STATUS


def _hitl_states() -> Counter[str]:
    states: Counter[str] = Counter()
    for line in HITL.splitlines():
        if not line.startswith("### H-"):
            continue
        spans = re.findall(r"`([^`]+)`", line)
        assert spans, f"状態の印が無い: {line}"
        states[spans[-1]] += 1
    return states


def test_status_hitl_tally_matches_hitl_md() -> None:
    states = _hitl_states()
    assert _claim(STATUS, r"`docs/HITL.md` に (\d+) 件") == sum(states.values())
    for label, key in (
        ("待ち", "待ち"),
        ("暫定", "暫定"),
        ("期限到来", "期限到来"),
        ("決定済み", "決定済み"),
        ("決定済み・実行待ち", "決定済み・実行待ち"),
        ("一部決定済み", "一部決定済み"),
    ):
        assert _claim(STATUS, rf"(?<![・済行])(?<!一部){label} (\d+)(?!\d)") == states[key], label


def test_every_adr_reference_in_the_docs_exists() -> None:
    roots = [ROOT / "docs", ROOT / "README.md", ROOT / "CLAUDE.md", ROOT / ".claude"]
    files: list[Path] = []
    for root in roots:
        files += [root] if root.is_file() else list(root.rglob("*.md"))
    known = set(_ADR_NUMBERS)
    missing: dict[str, set[str]] = {}
    for path in files:
        for number in re.findall(r"ADR-(\d{4})", path.read_text(encoding="utf-8")):
            if int(number) not in known and number != "0000":
                missing.setdefault(path.relative_to(ROOT).as_posix(), set()).add(number)
    assert not missing, f"存在しない ADR を参照している: {missing}"
