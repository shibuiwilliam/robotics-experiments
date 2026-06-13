"""アーキテクチャ不変条件テスト (PROJECT.md §5.2 / CLAUDE.md §3)。

1. import-linter 契約（依存方向・oracle/ORコア分離）
2. ASTベースの検査（docstringは無視し、実コードのみ判定）:
   - pyoxigraph の import は orx.kg のみ
   - ORコア (anchoring/kg/agent) は真値（oracle_truth_ids / TruthState / truth()）に触れない
   - owl:sameAs 文字列は kg のガード以外に現れない
   - random.seed / np.random.seed の直接呼び出し禁止
   - 劣化ノブ (DegradationConfig) は sim/perception 側のみ
"""

import ast
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "orx"

OR_CORE = ["anchoring", "kg", "agent"]
NON_KG = ["common", "sim", "skills", "business", "perception",
          "anchoring", "agent", "oracle", "replay", "exp"]


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


def _modules(package: str) -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted((SRC / package).rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _non_docstring_strings(tree: ast.Module) -> Iterator[str]:
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                doc_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_nodes
        ):
            yield node.value


def _imported_names(tree: ast.Module) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module
            yield from (a.name for a in node.names)


def test_pyoxigraph_only_in_kg() -> None:
    for package in NON_KG:
        for path, tree in _modules(package):
            for name in _imported_names(tree):
                assert not name.startswith("pyoxigraph"), f"{path}: pyoxigraph を直接import"
    kg_imports = [n for _, t in _modules("kg") for n in _imported_names(t)]
    assert any(n.startswith("pyoxigraph") for n in kg_imports)


def test_truth_not_referenced_by_or_core() -> None:
    forbidden_names = {"TruthState", "TruthObject", "oracle_truth_ids"}
    for package in OR_CORE:
        for path, tree in _modules(package):
            for name in _imported_names(tree):
                assert name not in forbidden_names, f"{path}: 真値スキーマをimport"
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    assert node.attr != "oracle_truth_ids", (
                        f"{path}: oracle_truth_ids へのアクセス"
                    )
                    if node.attr == "truth" and isinstance(node.ctx, ast.Load):
                        raise AssertionError(f"{path}: 真値アクセサ .truth への参照")
                if isinstance(node, ast.Name):
                    assert node.id not in {"TruthState", "TruthObject"}, (
                        f"{path}: 真値スキーマ {node.id} を参照"
                    )


def test_oracle_scenarios_independent_of_or_core() -> None:
    """シナリオ真値導出（oracle.scenarios）はORコアを import しない（CLAUDE.md §3-4）。"""
    forbidden = ("orx.anchoring", "orx.kg", "orx.agent", "orx.replay", "orx.exp")
    scen_dir = SRC / "oracle" / "scenarios"
    assert scen_dir.exists()
    for path in sorted(scen_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            assert not any(name == f or name.startswith(f + ".") for f in forbidden), (
                f"{path}: ORコア {name} を import（評価の独立性違反）"
            )


def test_no_owl_sameas_outside_kg_guard() -> None:
    for package in NON_KG:
        for path, tree in _modules(package):
            for value in _non_docstring_strings(tree):
                assert "sameAs" not in value, f"{path}: owl:sameAs 文字列を使用"


def test_no_direct_global_seeding() -> None:
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "seed":
                continue
            target = node.func.value
            is_random = isinstance(target, ast.Name) and target.id == "random"
            is_np_random = isinstance(target, ast.Attribute) and target.attr == "random"
            assert not (is_random or is_np_random), f"{path}: グローバルseedの直接設定"


def test_degradation_knobs_only_in_sensor_layer() -> None:
    for package in ["anchoring", "kg", "agent", "oracle"]:
        for path, tree in _modules(package):
            for name in _imported_names(tree):
                assert name != "DegradationConfig", f"{path}: 劣化ノブを参照"
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    assert node.id != "DegradationConfig", f"{path}: 劣化ノブを参照"
