"""``make ontology-check`` — オントロジーの自己検証。

検査項目（CLAUDE.md「よく使うコマンド」）:

1. TTL 構文（rdflib でパースできる）
2. 必須注釈（``rdfs:label`` ja/en、``skos:definition``、``sbp:layer``、``sbp:owner``）
3. SHACL 形状の自己検証（形状ファイル自身が SHACL メタ形状に適合する）
4. 事象計算規則との対応（TTL の FluentType / EventType ↔ ``process/*.lp`` の宣言）
5. semver 互換（``VERSION`` が semver で、``CHANGELOG.md`` に当該版の節がある）
6. 生成物の差分（``vocab.py`` / ``schemas/`` が現在のオントロジーと一致している）

検査は失敗を列挙してから終了コードで返す（最初の 1 件で止めない）。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

from pyshacl import validate as shacl_validate
from rdflib import Graph
from rdflib.namespace import SH

from scripts._ontology import (
    ONTOLOGY_DIR,
    SBP,
    SCHEMAS_DIR,
    VOCAB_PATH,
    collect_terms,
    event_types,
    fluent_types,
    initiates_terminates,
    load_ontology,
    ontology_version,
)

_SEMVER: Final = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
_VALID_LAYERS: Final = frozenset({"L1", "L2", "L3", "L4"})
_REPO_ROOT: Final = ONTOLOGY_DIR.parent


class Report:
    """検査結果の収集。"""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def fail(self, check: str, message: str) -> None:
        """失敗を記録する。"""
        self.failures.append(f"[{check}] {message}")

    def note(self, message: str) -> None:
        """情報を記録する。"""
        self.notes.append(message)


def check_syntax(report: Report) -> Graph | None:
    """1. TTL 構文。"""
    try:
        graph = load_ontology()
    except Exception as exc:
        report.fail("syntax", f"failed to parse Turtle: {exc}")
        return None
    report.note(f"syntax: parsed {len(graph)} triples")
    return graph


def check_annotations(graph: Graph, report: Report) -> None:
    """2. 必須注釈。"""
    terms = collect_terms(graph)
    for term in terms:
        missing = [
            name
            for name, value in (
                ("rdfs:label@ja", term.label_ja),
                ("rdfs:label@en", term.label_en),
                ("skos:definition", term.definition),
                ("sbp:layer", term.layer),
                ("sbp:owner", term.owner),
            )
            if not value
        ]
        if missing:
            report.fail("annotations", f"{term.prefix}:{term.local_name} is missing {missing}")
        if term.layer and term.layer not in _VALID_LAYERS:
            report.fail(
                "annotations",
                f"{term.prefix}:{term.local_name} has invalid layer {term.layer!r} "
                f"(expected one of {sorted(_VALID_LAYERS)})",
            )
    report.note(f"annotations: checked {len(terms)} terms")


def check_shapes(report: Report) -> None:
    """3. SHACL 形状の自己検証。

    形状ファイルを「データ」として SHACL のメタ形状（pyshacl 同梱）で検証する。形状自体が
    壊れていれば実行時の検証がすべて無意味になるため、ここで落とす。
    """
    shape_files = sorted((ONTOLOGY_DIR / "shapes").glob("*.ttl"))
    if not shape_files:
        report.fail("shapes", "no SHACL shape files under ontology/shapes/")
        return
    data = Graph()
    for path in shape_files:
        data.parse(path, format="turtle")

    conforms, _results_graph, results_text = shacl_validate(
        data_graph=data,
        shacl_graph=None,
        meta_shacl=True,
        advanced=True,
        inference="none",
        abort_on_first=False,
        allow_warnings=True,
    )
    if not conforms:
        report.fail(
            "shapes", f"SHACL shapes do not conform to the SHACL meta-shapes:\n{results_text}"
        )
    else:
        report.note(f"shapes: {len(shape_files)} shape file(s) conform to SHACL meta-shapes")


def _lp_declarations(text: str, predicate: str) -> set[str]:
    return set(re.findall(rf"^{predicate}\(([a-z0-9_]+)\)\.", text, flags=re.MULTILINE))


def _lp_pairs(text: str, predicate: str) -> set[tuple[str, str]]:
    return set(
        re.findall(rf"^{predicate}\(([a-z0-9_]+),\s*([a-z0-9_]+)\)\.", text, flags=re.MULTILINE)
    )


def _snake(local_name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", local_name).lower()


def check_process_rules(graph: Graph, report: Report) -> None:
    """4. 事象計算規則との対応。

    TTL の FluentType / EventType と ``process/*.lp`` の宣言が双方向に一致することを検査する。
    片側だけに存在する型は、整合残差の計算も事象昇格の逆写像も壊すため失敗にする。
    """
    lp_files = sorted((ONTOLOGY_DIR / "process").glob("*.lp"))
    if not lp_files:
        report.fail("process", "no event-calculus files under ontology/process/")
        return
    text = "\n".join(path.read_text(encoding="utf-8") for path in lp_files)

    ttl_fluents = {_snake(t.local_name) for t in fluent_types(graph)}
    ttl_events = {_snake(t.local_name) for t in event_types(graph)}
    lp_fluents = _lp_declarations(text, "fluent_type")
    lp_events = _lp_declarations(text, "event_type")

    for label, in_ttl, in_lp in (
        ("fluent", ttl_fluents, lp_fluents),
        ("event", ttl_events, lp_events),
    ):
        for name in sorted(in_ttl - in_lp):
            report.fail("process", f"{label} type {name!r} is declared in TTL but not in *.lp")
        for name in sorted(in_lp - in_ttl):
            report.fail("process", f"{label} type {name!r} is declared in *.lp but not in TTL")

    ttl_initiates, ttl_terminates = initiates_terminates(graph)
    for predicate, ttl_pairs in (
        ("initiates", {(_snake(e), _snake(f)) for e, f in ttl_initiates}),
        ("terminates", {(_snake(e), _snake(f)) for e, f in ttl_terminates}),
    ):
        lp_pairs = _lp_pairs(text, predicate)
        for pair in sorted(ttl_pairs - lp_pairs):
            report.fail("process", f"{predicate}{pair} is in TTL but missing from *.lp")
        for pair in sorted(lp_pairs - ttl_pairs):
            report.fail("process", f"{predicate}{pair} is in *.lp but missing from TTL")

    # 排他フルーエントは .lp 側にも対称に宣言されていること。
    ttl_exclusive = {
        (_snake(str(a).rsplit("#", 1)[-1]), _snake(str(b).rsplit("#", 1)[-1]))
        for a, b in graph.subject_objects(SBP.mutuallyExclusiveWith)
    }
    lp_exclusive = _lp_pairs(text, "exclusive")
    for pair in sorted(ttl_exclusive - lp_exclusive):
        report.fail("process", f"exclusive{pair} is in TTL but missing from *.lp")

    # すべてのフルーエント型が少なくとも 1 つの事象型に変化させられること。
    # EventTypeShape（すべての事象型は何かを変化させる）の双対であり、片側だけ検査していると
    # 「変化させられないフルーエント」が通ってしまう。変化しないフルーエントは事象駆動の
    # 信念更新に乗らず、アクションの前提としても永久に初期値のままになる。
    changeable = {f for _, f in ttl_initiates} | {f for _, f in ttl_terminates}
    for term in fluent_types(graph):
        if term.local_name not in changeable:
            report.fail(
                "process",
                f"fluent type {term.local_name!r} is not initiated or terminated by any event "
                "type; it can never change, so it cannot take part in event-driven belief "
                "updates (dual of the EventTypeShape constraint)",
            )

    report.note(
        f"process: {len(ttl_fluents)} fluent types, {len(ttl_events)} event types, "
        f"{len(ttl_initiates)} initiates, {len(ttl_terminates)} terminates aligned; "
        f"{len(changeable)} fluent types are changeable by events"
    )


def check_shacl_warning_for_exclusives(graph: Graph, report: Report) -> None:
    """排他フルーエントに対応する ``sh:Warning`` 形状の存在を検査する（ontology ルール）。

    排他宣言があるのに衝突を検出する Warning 形状が無いと、「排他フルーエントが同時に成立したら
    不一致候補を生成する」（設計書 §3.9）という設計が実装されないまま通ってしまう。
    """
    if not any(graph.subject_objects(SBP.mutuallyExclusiveWith)):
        return

    shapes = Graph()
    for path in sorted((ONTOLOGY_DIR / "shapes").glob("*.ttl")):
        shapes.parse(path, format="turtle")

    # sh:severity sh:Warning を持つ制約が 1 つ以上あり、かつ mutuallyExclusiveWith を
    # 参照していること（排他衝突を見る形状であることの確認）。
    has_warning = any(shapes.triples((None, SH.severity, SH.Warning)))
    mentions_exclusive = any(
        str(SBP.mutuallyExclusiveWith) in str(select) for select in shapes.objects(None, SH.select)
    )
    if not has_warning:
        report.fail(
            "shapes",
            "mutuallyExclusiveWith is declared but no constraint carries sh:severity sh:Warning",
        )
    elif not mentions_exclusive:
        report.fail(
            "shapes",
            "no sh:Warning constraint references mutuallyExclusiveWith; "
            "exclusive-fluent collisions would never become discrepancy candidates",
        )
    else:
        report.note("shapes: exclusive-fluent collisions are covered by a sh:Warning constraint")


def check_version(report: Report) -> None:
    """5. semver と CHANGELOG。"""
    version = ontology_version()
    if not _SEMVER.match(version):
        report.fail("version", f"ontology/VERSION is not semver: {version!r}")
        return
    changelog = (ONTOLOGY_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {version}" not in changelog:
        report.fail(
            "version",
            f"ontology/CHANGELOG.md has no section for version {version} "
            "(every version change must record its semver judgement)",
        )
    # owl:versionInfo が VERSION とずれていると、グラフに載った版と生成物の版が食い違う。
    # アサーションの ontology_version は生成物（vocab.py）から来るので、ストアに投入された
    # オントロジーだけが古い版を名乗る状態になり、as-of の照合が狂う。
    for path in sorted(ONTOLOGY_DIR.glob("*/*.ttl")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "owl:versionInfo" not in line:
                continue
            declared = line.split('"')[1] if '"' in line else ""
            if declared != version:
                report.fail(
                    "version",
                    f"{path.relative_to(ONTOLOGY_DIR)} declares owl:versionInfo "
                    f"{declared!r} but ontology/VERSION is {version!r}",
                )
    report.note(f"version: {version}")


def _artifact_digest() -> dict[str, str]:
    """生成物の内容ハッシュを返す。

    git の作業ツリー状態（staged / untracked）に依存せず、「いま生成し直したら同じ内容になるか」
    だけを見る。新規クローンでも CI でも同じ判定になる。
    """
    digests: dict[str, str] = {}
    targets: list[Path] = [VOCAB_PATH, *sorted(SCHEMAS_DIR.rglob("*.json"))]
    for path in targets:
        if path.exists():
            digests[str(path.relative_to(_REPO_ROOT))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digests


def check_generated_artifacts(report: Report) -> None:
    """6. 生成物の鮮度。

    ``make gen`` を実行し直しても ``vocab.py`` / ``schemas/`` が変化しないことを確認する。
    生成物がオントロジーより古いまま commit されるのを防ぐ。
    """
    if not VOCAB_PATH.exists():
        report.fail("generated", f"{VOCAB_PATH.name} does not exist; run `make gen`")
        return
    if not (SCHEMAS_DIR / "event-envelope.schema.json").exists():
        report.fail(
            "generated", "schemas/event-envelope.schema.json does not exist; run `make gen`"
        )
        return

    before = _artifact_digest()
    for module in ("scripts.gen_vocab", "scripts.gen_schemas"):
        result = subprocess.run(
            [sys.executable, "-m", module],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            report.fail("generated", f"{module} failed: {result.stderr.strip()}")
            return
    after = _artifact_digest()

    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    if changed:
        report.fail(
            "generated",
            "generated artifacts were stale; `make gen` changed them. "
            f"Commit the regenerated files: {changed}",
        )
    else:
        report.note(
            f"generated: {len(after)} artifact(s) are in sync with ontology/ "
            "(regeneration produced identical content)"
        )


def main() -> int:
    """全検査を実行し、失敗があれば 1 を返す。"""
    report = Report()
    graph = check_syntax(report)
    if graph is not None:
        check_annotations(graph, report)
        check_shapes(report)
        check_process_rules(graph, report)
        check_shacl_warning_for_exclusives(graph, report)
    check_version(report)
    check_generated_artifacts(report)

    for note in report.notes:
        sys.stdout.write(f"  ok   {note}\n")
    for failure in report.failures:
        sys.stdout.write(f"  FAIL {failure}\n")
    if report.failures:
        sys.stdout.write(f"\nontology-check: {len(report.failures)} failure(s)\n")
        return 1
    sys.stdout.write("\nontology-check: all green\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
