"""検証（設計書 §3.9、§5.7、bridge-worldmodel ルール）。

**検証は拒否ではなく証拠生成**である（設計書 §3.9）。これが SBP の検証設計の中心にある考え方で、
通常のデータ検証とは目的が違う。

- ``sh:Violation``（構造制約）— 登録せず**隔離キュー**へ。構造が壊れたデータは信念にできない。
  ただし捨てずに人が確認する。
- ``sh:Warning``（整合制約）— **登録したうえで不一致候補**を生成する。排他フルーエントの
  同時成立などは「異常データ」ではなく製品である（設計原則 P5）。

``sh:severity`` は**シェイプの属性**であって制約ノードの属性ではない（SHACL 仕様 §2.1.3）。
``sh:sparql [ sh:severity ... ]`` と書くと無視され、既定の Violation になる。整合制約が
Violation になると、登録して不一致候補にすべきものが隔離され、検出したい状態が消える。

後者が要点である。「Running と UnderMaintenance が同時に 0.6」という状態は、データの誤りでは
なく**物理と業務が食い違っている証拠**かもしれない。それを拒否すると、検出すべきものを
消してしまう。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from sbp.core.errors import ShapeViolation
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "Disposition",
    "ValidationFinding",
    "ValidationOutcome",
    "validate_graph",
]

log = get_logger(__name__)

_SHACL_SEVERITY: Final[dict[str, str]] = {
    "http://www.w3.org/ns/shacl#Violation": "violation",
    "http://www.w3.org/ns/shacl#Warning": "warning",
    "http://www.w3.org/ns/shacl#Info": "info",
}


class Disposition(StrEnum):
    """検証結果に対する処置（設計書 §5.7 の表）。"""

    ACCEPT = "accept"
    """そのまま登録する。"""
    QUARANTINE = "quarantine"
    """登録せず隔離キューへ（構造制約違反）。人が確認する。捨てない。"""
    ACCEPT_WITH_DISCREPANCY = "accept_with_discrepancy"
    """登録したうえで不一致候補を生成する（整合制約違反）。"""


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """検証で見つかった 1 件。

    Attributes:
        severity: ``violation`` / ``warning`` / ``info``。
        focus_node: 違反の焦点ノード（IRI）。
        source_shape: 違反した形状の IRI。
        message: 人が読むメッセージ（形状の ``sh:message``）。
        path: 違反したプロパティパス（あれば）。
    """

    severity: str
    focus_node: str
    source_shape: str
    message: str
    path: str | None = None

    @property
    def disposition(self) -> Disposition:
        """この件に対する処置。"""
        if self.severity == "violation":
            return Disposition.QUARANTINE
        if self.severity == "warning":
            return Disposition.ACCEPT_WITH_DISCREPANCY
        return Disposition.ACCEPT


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """検証 1 回の結果。

    Attributes:
        conforms: 違反が無いか（Warning は含まない判定）。
        findings: 見つかった全件。
    """

    conforms: bool
    findings: tuple[ValidationFinding, ...]

    @property
    def disposition(self) -> Disposition:
        """全体としての処置。最も重いものを採る。"""
        if any(f.disposition is Disposition.QUARANTINE for f in self.findings):
            return Disposition.QUARANTINE
        if any(f.disposition is Disposition.ACCEPT_WITH_DISCREPANCY for f in self.findings):
            return Disposition.ACCEPT_WITH_DISCREPANCY
        return Disposition.ACCEPT

    @property
    def violations(self) -> tuple[ValidationFinding, ...]:
        """構造制約違反（隔離対象）。"""
        return tuple(f for f in self.findings if f.severity == "violation")

    @property
    def warnings(self) -> tuple[ValidationFinding, ...]:
        """整合制約違反（不一致候補の種）。"""
        return tuple(f for f in self.findings if f.severity == "warning")

    def raise_if_quarantined(self) -> None:
        """隔離対象なら例外を送出する。

        Raises:
            ShapeViolation: 構造制約違反がある場合。
        """
        if self.disposition is Disposition.QUARANTINE:
            messages = "; ".join(f"{f.source_shape}: {f.message}" for f in self.violations)
            raise ShapeViolation(
                f"structural constraints violated, data goes to the quarantine queue "
                f"(not discarded): {messages}"
            )


def validate_graph(
    data_turtle: str, shapes_turtle: str, *, inference: str = "none"
) -> ValidationOutcome:
    """SHACL でデータを検証する（pyshacl）。

    ``allow_warnings=True`` にするのが要点である。Warning を違反として扱うと、排他フルーエントの
    同時成立のような「検出したい状態」が登録を拒否されてしまう（設計書 §3.9）。

    Args:
        data_turtle: 検証するデータ（Turtle）。
        shapes_turtle: 形状（Turtle）。
        inference: pyshacl の推論設定（``none`` / ``rdfs`` / ``owlrl``）。

    Returns:
        検証結果。処置は :attr:`ValidationOutcome.disposition` で決まる。
    """
    from pyshacl import validate as shacl_validate  # noqa: PLC0415
    from rdflib import Graph  # noqa: PLC0415

    data = Graph()
    data.parse(data=data_turtle, format="turtle")
    shapes = Graph()
    shapes.parse(data=shapes_turtle, format="turtle")

    conforms, results_graph, _text = shacl_validate(
        data_graph=data,
        shacl_graph=shapes,
        inference=inference,
        advanced=True,
        abort_on_first=False,
        allow_warnings=True,  # Warning は登録を止めない（設計書 §3.9）
    )

    findings = tuple(_extract_findings(results_graph))
    log.debug(
        "bridge.validated",
        conforms=conforms,
        violations=sum(1 for f in findings if f.severity == "violation"),
        warnings=sum(1 for f in findings if f.severity == "warning"),
    )
    return ValidationOutcome(conforms=bool(conforms), findings=findings)


def _extract_findings(results_graph: object) -> Sequence[ValidationFinding]:
    """pyshacl の結果グラフから findings を取り出す。"""
    from rdflib import Graph  # noqa: PLC0415
    from rdflib.namespace import SH  # noqa: PLC0415

    if not isinstance(results_graph, Graph):
        return ()

    out: list[ValidationFinding] = []
    for result in results_graph.subjects(SH.resultSeverity, None):
        severity_iri = str(next(results_graph.objects(result, SH.resultSeverity), ""))
        focus = str(next(results_graph.objects(result, SH.focusNode), ""))
        shape = str(next(results_graph.objects(result, SH.sourceShape), ""))
        message = str(next(results_graph.objects(result, SH.resultMessage), ""))
        path_term = next(results_graph.objects(result, SH.resultPath), None)
        out.append(
            ValidationFinding(
                severity=_SHACL_SEVERITY.get(severity_iri, "info"),
                focus_node=focus,
                source_shape=shape,
                message=message,
                path=None if path_term is None else str(path_term),
            )
        )
    return out
