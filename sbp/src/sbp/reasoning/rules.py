"""規則エンジン（設計書 §8.9）。Datalog / ASP の実行と確信度の伝播。

> 規則の実行は信念上で行うため、確信度の伝播規則（最小値／積／noisy-OR）を規則ごとに宣言する。

**規則文字列を Python で組み立てない**（CLAUDE.md: テンプレートは ``.lp`` 側に置く）。この
モジュールが生成するのは**事実**（``holds_at(running, p101, 3).`` のような基底原子）だけで、
規則は ``ontology/process/*.lp`` と ``ontology/norms/*.lp`` にある。規則をコードで組み立てると、
「どんな規則が走っているか」がオントロジーから読めなくなる。

**確信度の伝播を推測しない。** ASP の解集合は導出された原子を返すが、導出木は返さない。ここで
導出木を推測すると「たぶんこの事実が根拠だろう」で確信度を決めることになる。そうせずに、
``.lp`` 側に ``support(Head, Body)`` を**規則自身に申告させる**。規則の作者が根拠を宣言する
形にしておけば、来歴は推測ではなく宣言になる。

伝播の型は ``confidence_rule(RuleName, HeadPredicate, Propagation)`` で宣言する:

- ``min`` — 連言（body の全部が要る）。既定。
- ``product`` — 独立な連言。body が独立と言えるときだけ。
- ``noisy_or`` — 選言（複数の導出が同じ結論を支える）。

ASP の実行は CPU 的に重いので同期関数にし、呼び出し側が ``asyncio.to_thread`` で回す
（CLAUDE.md 非同期規約）。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import clingo

from sbp.core.errors import RuleError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "Derivation",
    "Fact",
    "NormRule",
    "Propagation",
    "RuleProgram",
    "RuleResult",
    "propagate_confidence",
]

log = get_logger(__name__)

_DEFAULT_MODELS = 1
"""求める解集合の数。事象計算は決定的なので 1 で足りる（複数解が出る規則は設計外）。"""


class Propagation(StrEnum):
    """確信度の伝播規則（設計書 §8.9）。"""

    MIN = "min"
    """連言: body の最小値。既定。"""
    PRODUCT = "product"
    """独立な連言: 積。"""
    NOISY_OR = "noisy_or"
    """選言: ``1 - Π(1 - c)``。"""


def propagate_confidence(values: Sequence[float], propagation: Propagation) -> float:
    """根拠の確信度から結論の確信度を出す。

    Args:
        values: 根拠の確信度。
        propagation: 伝播規則。

    Returns:
        結論の確信度。根拠が無ければ 0.0（根拠の無い結論に確信度を与えない）。

    Raises:
        RuleError: 確率でない値が混ざっている場合。
    """
    for value in values:
        if not 0.0 <= value <= 1.0:
            raise RuleError(f"confidence must be a probability in [0, 1], got {value}")
    if not values:
        return 0.0
    if propagation is Propagation.MIN:
        return min(values)
    if propagation is Propagation.PRODUCT:
        return math.prod(values)
    return 1.0 - math.prod(1.0 - value for value in values)


@dataclass(frozen=True, slots=True)
class Fact:
    """信念から作る基底事実（ASP への入力）。

    Attributes:
        predicate: 述語名（``.lp`` の述語と一致させる。スネークケース）。
        args: 引数。文字列は ASP の定数、``int`` は整数として渡す。
        confidence: この事実の確信度（``0`` なら渡さない）。
    """

    predicate: str
    args: tuple[str | int, ...]
    confidence: float = 1.0

    def __post_init__(self) -> None:
        """述語名と確信度を検証する。"""
        if not self.predicate or not self.predicate.islower():
            raise RuleError(f"predicate must be a lowercase ASP identifier, got {self.predicate!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise RuleError(f"fact confidence must be in [0, 1], got {self.confidence}")

    def to_symbol(self) -> clingo.Symbol:
        """clingo の記号に変換する（文字列連結で ASP を組み立てないため）。"""
        arguments = [
            clingo.Number(arg) if isinstance(arg, int) else clingo.Function(arg, [])
            for arg in self.args
        ]
        return clingo.Function(self.predicate, arguments)

    @property
    def key(self) -> str:
        """``support`` の照合に使う正規化キー。"""
        return str(self.to_symbol())


@dataclass(frozen=True, slots=True)
class NormRule:
    """``.lp`` が申告する規範規則（設計書 §7.4 の監査性要件）。

    Attributes:
        rule_ref: 規則名。
        source_document: 出典文書。
        source_clause: 出典の節。
        severity: 重大度（``advisory`` / ``important`` / ``critical``）。
    """

    rule_ref: str
    source_document: str
    source_clause: str
    severity: str


@dataclass(frozen=True, slots=True)
class Derivation:
    """導出された 1 原子とその確信度。

    Attributes:
        predicate: 述語名。
        args: 引数（文字列化したもの）。
        confidence: 伝播後の確信度。
        rule: 伝播規則を宣言した規則名（宣言が無ければ ``""``）。
        propagation: 使った伝播規則。
        support: 根拠になった事実のキー。
    """

    predicate: str
    args: tuple[str, ...]
    confidence: float
    rule: str
    propagation: Propagation
    support: tuple[str, ...] = ()

    @property
    def atom(self) -> str:
        """``predicate(arg, ...)`` 形式の文字列。"""
        return f"{self.predicate}({','.join(self.args)})" if self.args else self.predicate


@dataclass(frozen=True, slots=True)
class RuleResult:
    """規則エンジン 1 回の実行結果。

    Attributes:
        derivations: 導出された原子（確信度付き）。
        norm_rules: ``.lp`` が申告した規範規則。
        satisfiable: 解集合が得られたか。
        unsupported: ``support`` の宣言が無く確信度を付けられなかった原子。
    """

    derivations: tuple[Derivation, ...]
    norm_rules: tuple[NormRule, ...] = ()
    satisfiable: bool = True
    unsupported: tuple[str, ...] = ()

    def of(self, predicate: str, *, min_confidence: float = 0.0) -> tuple[Derivation, ...]:
        """述語で絞り込む。

        Args:
            predicate: 述語名。
            min_confidence: この値未満の確信度は捨てる。
        """
        return tuple(
            derivation
            for derivation in self.derivations
            if derivation.predicate == predicate and derivation.confidence >= min_confidence
        )


class RuleProgram:
    """``.lp`` の規則を読んで実行する。

    規則ファイルは読み取り専用に扱う。実行のたびに新しい ``clingo.Control`` を作るのは、
    前回の事実が残った状態で解くこと（状態の持ち越し）を構造的に防ぐためである。
    """

    def __init__(self, sources: Sequence[tuple[str, str]]) -> None:
        """Args:
        sources: ``(ファイル名, プログラム本文)`` の列。
        """
        if not sources:
            raise RuleError("a rule program needs at least one .lp source")
        self._sources = tuple(sources)

    @classmethod
    def from_files(cls, paths: Iterable[Path]) -> RuleProgram:
        """``.lp`` ファイルから読み込む。

        Args:
            paths: 規則ファイルのパス。

        Raises:
            RuleError: ファイルが無い場合。
        """
        sources: list[tuple[str, str]] = []
        for path in paths:
            if not path.is_file():
                raise RuleError(f"rule file not found: {path}")
            # 親ディレクトリ名を含めて一意にする。process/ と norms/ に同名のファイルが
            # 置かれるのは普通であり、名前が衝突すると後から読んだ方が前を上書きする
            sources.append((f"{path.parent.name}/{path.name}", path.read_text(encoding="utf-8")))
        log.debug("rules.loaded", files=[name for name, _ in sources])
        return cls(sources)

    @classmethod
    def from_directories(cls, *directories: Path) -> RuleProgram:
        """ディレクトリ内の ``*.lp`` をすべて読み込む。

        Args:
            directories: ``ontology/process/``、``ontology/norms/`` など。
        """
        paths: list[Path] = []
        for directory in directories:
            paths.extend(sorted(directory.glob("*.lp")))
        if not paths:
            listed = ", ".join(str(d) for d in directories)
            raise RuleError(f"no .lp files found under {listed}")
        return cls.from_files(paths)

    @property
    def source_names(self) -> tuple[str, ...]:
        """読み込んだ規則ファイル名（来歴に残す）。"""
        return tuple(name for name, _ in self._sources)

    def solve(self, facts: Sequence[Fact], *, steps: int = 0) -> RuleResult:
        """事実を入れて解き、確信度を伝播した導出を返す。

        CPU 負荷が高いので同期関数にしている。非同期文脈からは ``asyncio.to_thread`` で
        呼ぶこと（CLAUDE.md 非同期規約）。

        Args:
            facts: 信念から作った基底事実。確信度 0 のものは渡さない。
            steps: ``step(0..steps)`` を宣言する（慣性規則が必要とする論理ステップ）。
                0 なら宣言しない。

        Returns:
            導出された原子と確信度。

        Raises:
            RuleError: 規則の構文エラー、または解集合が得られない場合。
        """
        # atom-undefined の警告を黙らせる。happens/fluent_change のような入力述語は、呼び出し
        # ごとに渡す・渡さないが変わるのが正常であり、未定義は欠陥ではない
        control = clingo.Control([f"--models={_DEFAULT_MODELS}", "--warn=no-atom-undefined"])
        parts = [(_part_name(name), name) for name, _ in self._sources]
        for (part, name), (_, program) in zip(parts, self._sources, strict=True):
            try:
                control.add(part, [], program)
            except RuntimeError as exc:
                raise RuleError(f"failed to load rule program {name}: {exc}") from exc

        confidences = {fact.key: fact.confidence for fact in facts if fact.confidence > 0.0}
        extra = "".join(f"{fact.key}." for fact in facts if fact.confidence > 0.0)
        if steps > 0:
            extra += f"step(0..{steps})."
        if extra:
            try:
                control.add("facts", [], extra)
            except RuntimeError as exc:
                raise RuleError(f"failed to add facts: {exc}") from exc

        try:
            control.ground([(part, []) for part, _ in parts] + [("facts", [])])
        except RuntimeError as exc:
            raise RuleError(f"grounding failed: {exc}") from exc

        atoms: list[clingo.Symbol] = []
        with control.solve(yield_=True) as handle:  # pyright: ignore[reportGeneralTypeIssues]
            for model in handle:
                atoms = list(model.symbols(shown=True))
                break
            # satisfiable が None（求解未完了）なら「充足可能」と主張しない
            satisfiable = bool(atoms) or handle.get().satisfiable is True

        return _build_result(atoms, confidences=confidences, satisfiable=satisfiable)


def _part_name(source_name: str) -> str:
    """ファイル名を ASP のプログラム部名にする（識別子として安全な形に落とす）。"""
    sanitized = re.sub(r"[^0-9a-zA-Z]+", "_", source_name).strip("_").lower()
    return sanitized or "program"


def _build_result(
    atoms: Sequence[clingo.Symbol], *, confidences: dict[str, float], satisfiable: bool
) -> RuleResult:
    """解集合から導出・規範規則・確信度を組み立てる。"""
    supports: dict[str, list[str]] = {}
    propagations: dict[str, tuple[str, Propagation]] = {}
    norm_rules: list[NormRule] = []
    derived: dict[str, clingo.Symbol] = {}

    for atom in atoms:
        name = atom.name
        if name == "support" and len(atom.arguments) == 2:
            head, body = atom.arguments
            supports.setdefault(str(head), []).append(str(body))
            continue
        if name == "confidence_rule" and len(atom.arguments) == 3:
            rule, head_predicate, scheme = atom.arguments
            propagations[str(head_predicate)] = (
                str(rule),
                _parse_propagation(str(scheme), rule=str(rule)),
            )
            continue
        if name == "norm_rule" and len(atom.arguments) == 4:
            reference, document, clause, severity = atom.arguments
            norm_rules.append(
                NormRule(
                    rule_ref=str(reference),
                    source_document=str(document).strip('"'),
                    source_clause=str(clause).strip('"'),
                    severity=str(severity),
                )
            )
            continue
        derived[str(atom)] = atom

    derivations: list[Derivation] = []
    unsupported: list[str] = []
    for key, atom in sorted(derived.items()):
        rule, propagation = propagations.get(atom.name, ("", Propagation.MIN))
        body = supports.get(key, [])
        if key in confidences and not body:
            # 入力そのもの（信念から渡した事実）はその確信度を保つ
            derivations.append(
                Derivation(
                    predicate=atom.name,
                    args=tuple(str(argument) for argument in atom.arguments),
                    confidence=confidences[key],
                    rule=rule,
                    propagation=propagation,
                    support=(key,),
                )
            )
            continue
        if not body:
            unsupported.append(key)
            continue
        values = [confidences[reference] for reference in body if reference in confidences]
        derivations.append(
            Derivation(
                predicate=atom.name,
                args=tuple(str(argument) for argument in atom.arguments),
                confidence=propagate_confidence(values, propagation),
                rule=rule,
                propagation=propagation,
                support=tuple(body),
            )
        )

    if unsupported:
        log.info(
            "rules.derivations_without_declared_support",
            count=len(unsupported),
            sample=unsupported[:3],
        )
    log.info(
        "rules.solved",
        satisfiable=satisfiable,
        derivations=len(derivations),
        norm_rules=len(norm_rules),
    )
    return RuleResult(
        derivations=tuple(derivations),
        norm_rules=tuple(norm_rules),
        satisfiable=satisfiable,
        unsupported=tuple(unsupported),
    )


def _parse_propagation(value: str, *, rule: str) -> Propagation:
    """``.lp`` の宣言を伝播規則に変換する。"""
    try:
        return Propagation(value)
    except ValueError as exc:
        raise RuleError(
            f"rule {rule!r} declares unknown confidence propagation {value!r}; "
            f"allowed: {sorted(p.value for p in Propagation)}"
        ) from exc
