"""観測手段のレジストリの単体テスト（M8-05、ADR-0011）。

重点:

1. **宣言したフルーエントがオントロジーに実在する**（不変条件 5）— タイプミスが
   「その概念は既定手段で観測する」という静かな挙動にならない。
2. **既定手段が必須** — 無いと宣言の無いフルーエントが 1 件も変換されず、スケジューラが
   常に 0 件を返す。
3. **既定は人への質問にしない** — 頻度上限に当たって何も選ばれない周期が出る。
4. **リポジトリの実宣言が読める** — 設定ファイルが壊れていることに単体テストで気付く。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from sbp.core import vocab
from sbp.core.errors import ConfigError
from sbp.reasoning.means import MeansRegistry
from sbp.reasoning.scheduler import QueryKind

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]

_VALID = """
default:
  kind: sampling_rate
  reliability: 0.95
  cost: 0.1
means:
  - fluent: ent:Running
    kind: sampling_rate
    reliability: 0.97
    cost: 0.1
  - fluent: ent:UnderMaintenance
    kind: human_question
    reliability: 0.99
    cost: 3.0
"""


@pytest.fixture
def declared(tmp_path: Path) -> Iterator[Path]:
    """最小の宣言を 1 ファイル置く。"""
    (tmp_path / "test.yaml").write_text(_VALID, encoding="utf-8")
    yield tmp_path


def _write(directory: Path, body: str, *, name: str = "test.yaml") -> Path:
    (directory / name).write_text(body, encoding="utf-8")
    return directory


def test_declared_means_are_used(declared: Path) -> None:
    """宣言した手段が引ける。"""
    registry = MeansRegistry.from_directory(declared)

    running = registry.for_fluent(vocab.ent.Running)
    assert running.kind is QueryKind.SAMPLING_RATE
    assert running.reliability == 0.97
    assert registry.for_fluent(vocab.ent.UnderMaintenance).kind is QueryKind.HUMAN_QUESTION


def test_curies_and_full_iris_both_work(declared: Path) -> None:
    """CURIE でも完全 IRI でも同じ手段が引ける。"""
    registry = MeansRegistry.from_directory(declared)

    assert registry.for_fluent("ent:Running") == registry.for_fluent(vocab.ent.Running)


def test_undeclared_fluents_get_the_default(declared: Path) -> None:
    """宣言の無いフルーエントには既定手段が当たる。

    当たらないと要求が 1 件も変換されず、スケジューラが常に 0 件を返す。
    """
    registry = MeansRegistry.from_directory(declared)

    assert registry.for_fluent(vocab.ent.Inspected) == registry.default


def test_the_default_does_not_ask_a_human(declared: Path) -> None:
    """既定手段は人の手を取らない。

    既定にすると頻度上限に当たって何も選ばれない周期が出るうえ、「自動で人に聞き続ける」
    挙動は望ましくない。
    """
    registry = MeansRegistry.from_directory(declared)

    assert not registry.default.kind.asks_a_human


def test_a_fluent_not_in_the_ontology_is_refused(tmp_path: Path) -> None:
    """オントロジーに無いフルーエントの宣言を拒否する（不変条件 5）。"""
    _write(
        tmp_path,
        "default:\n  kind: sampling_rate\n  reliability: 0.9\n  cost: 0.1\n"
        "means:\n  - fluent: ent:Runnning\n    kind: sampling_rate\n"
        "    reliability: 0.9\n    cost: 0.1\n",
    )

    with pytest.raises(ConfigError, match="not a FluentType"):
        MeansRegistry.from_directory(tmp_path)


def test_a_non_fluent_class_is_refused(tmp_path: Path) -> None:
    """フルーエント型でない概念（設備クラスなど）も拒否する。"""
    _write(
        tmp_path,
        "default:\n  kind: sampling_rate\n  reliability: 0.9\n  cost: 0.1\n"
        "means:\n  - fluent: sbp:Asset\n    kind: sampling_rate\n"
        "    reliability: 0.9\n    cost: 0.1\n",
    )

    with pytest.raises(ConfigError, match="not a FluentType"):
        MeansRegistry.from_directory(tmp_path)


def test_a_missing_default_is_refused(tmp_path: Path) -> None:
    """既定手段が無い宣言を拒否する。"""
    _write(
        tmp_path,
        "means:\n  - fluent: ent:Running\n    kind: sampling_rate\n"
        "    reliability: 0.9\n    cost: 0.1\n",
    )

    with pytest.raises(ConfigError, match="no default observation means"):
        MeansRegistry.from_directory(tmp_path)


def test_two_defaults_are_refused(tmp_path: Path) -> None:
    """2 つのファイルが既定を宣言したら拒否する（どちらが効くかが任意になる）。"""
    body = "default:\n  kind: sampling_rate\n  reliability: 0.9\n  cost: 0.1\n"
    _write(tmp_path, body, name="a.yaml")
    _write(tmp_path, body, name="b.yaml")

    with pytest.raises(ConfigError, match="two files declare a default"):
        MeansRegistry.from_directory(tmp_path)


def test_a_duplicate_fluent_is_refused(tmp_path: Path) -> None:
    """同じフルーエントを 2 回宣言したら拒否する。"""
    _write(
        tmp_path,
        "default:\n  kind: sampling_rate\n  reliability: 0.9\n  cost: 0.1\n"
        "means:\n"
        "  - fluent: ent:Running\n    kind: sampling_rate\n    reliability: 0.9\n    cost: 0.1\n"
        "  - fluent: ent:Running\n    kind: human_question\n    reliability: 0.99\n   "
        " cost: 3.0\n",
    )

    with pytest.raises(ConfigError, match="duplicate observation means"):
        MeansRegistry.from_directory(tmp_path)


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    """``QueryKind`` に無い種類を拒否する。"""
    _write(
        tmp_path,
        "default:\n  kind: telepathy\n  reliability: 0.9\n  cost: 0.1\n",
    )

    with pytest.raises(ConfigError, match="unknown observation kind"):
        MeansRegistry.from_directory(tmp_path)


@pytest.mark.parametrize("reliability", ["0.0", "1.5", "-0.1"])
def test_reliability_outside_the_range_is_refused(tmp_path: Path, reliability: str) -> None:
    """信頼度は (0, 1] の外を拒否する。

    0 の手段は「測っても何も分からない」で情報利得が定義できず、1 を超えるのは意味を持たない。
    """
    _write(
        tmp_path,
        f"default:\n  kind: sampling_rate\n  reliability: {reliability}\n  cost: 0.1\n",
    )

    with pytest.raises(ConfigError, match="reliability must be in"):
        MeansRegistry.from_directory(tmp_path)


def test_a_negative_cost_is_refused(tmp_path: Path) -> None:
    """負のコストを拒否する（やるほど得になる観測は作らない）。"""
    _write(
        tmp_path,
        "default:\n  kind: sampling_rate\n  reliability: 0.9\n  cost: -1.0\n",
    )

    with pytest.raises(ConfigError, match="cost must not be negative"):
        MeansRegistry.from_directory(tmp_path)


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    """宣言が 1 つも無ければ拒否する。

    黙って既定だけで動かすと、「手段を宣言したつもり」の設定漏れに気付けない。
    """
    with pytest.raises(ConfigError, match="no observation means declared"):
        MeansRegistry.from_directory(tmp_path)


def test_the_repository_declaration_loads() -> None:
    """リポジトリの実宣言（``observation/``）が読める。

    設定ファイルが壊れたことに単体テストで気付く（統合テストや起動まで待たない）。
    """
    registry = MeansRegistry.from_directory(REPO_ROOT / "observation")

    assert vocab.ent.Running in registry.declared
    assert vocab.ent.UnderMaintenance in registry.declared
    assert not registry.default.kind.asks_a_human
    # 宣言されている全フルーエントがオントロジーの FluentType である（読み込み時に検査済みだが
    # ここでも確かめておく——検査を外したことに気付けるようにする）
    assert set(registry.declared) <= vocab.FLUENT_TYPES
