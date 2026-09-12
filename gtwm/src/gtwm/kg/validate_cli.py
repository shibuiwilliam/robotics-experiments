"""`gtwm kg validate`：ttl の構文、SHACL の自己整合、queries/*.rq の構文を検査する。

「SHACL の自己整合」は、各 shapes ファイルが (1) 正しい Turtle として構文解析でき、
(2) pyshacl が空のデータグラフに対して例外なく実行できる（sh:sparql 内の SPARQL を含め
形状定義自体が構造的に妥当である）ことをもって判定する、実用的な定義とする。
"""

from __future__ import annotations

from pathlib import Path

import rdflib
from pyshacl import validate as shacl_validate

ONTOLOGY_ROOT = Path(__file__).resolve().parents[3] / "ontology"
QUERIES_DIR = Path(__file__).parent / "queries"


def _find_ttl_files() -> list[Path]:
    files = [ONTOLOGY_ROOT / "gt-core.ttl"]
    files.extend(sorted((ONTOLOGY_ROOT / "shapes").glob("*.ttl")))
    return files


def validate_all() -> list[str]:
    """検査を実行し、問題があればメッセージのリストを返す（空なら全て緑）。"""
    issues: list[str] = []

    for ttl_path in _find_ttl_files():
        try:
            rdflib.Graph().parse(ttl_path, format="turtle")
        except Exception as exc:  # noqa: BLE001 — 構文エラーを利用者に見せるため広く捕捉する
            issues.append(f"[ttl構文] {ttl_path}: {exc}")

    for shape_path in sorted((ONTOLOGY_ROOT / "shapes").glob("*.ttl")):
        try:
            shapes_graph = rdflib.Graph()
            shapes_graph.parse(shape_path, format="turtle")
            shacl_validate(
                rdflib.Graph(),
                shacl_graph=shapes_graph,
                advanced=True,
                allow_infos=True,
                allow_warnings=True,
            )
        except Exception as exc:  # noqa: BLE001
            issues.append(f"[SHACL自己整合] {shape_path}: {exc}")

    for rq_path in sorted(QUERIES_DIR.glob("*.rq")):
        try:
            rdflib.plugins.sparql.prepareQuery(rq_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            issues.append(f"[SPARQL構文] {rq_path}: {exc}")

    return issues


__all__ = ["validate_all"]
