"""規則エンジンの単体テスト（設計書 §8.9）。

重点:

1. **確信度の伝播は規則が宣言する** — min / product / noisy-OR を ``.lp`` 側から読む。
2. **規則文字列を Python で組み立てない** — 渡すのは基底事実だけ。
3. **規範規則は出典を申告する** — 文書・節・重大度が無い規則は監査に使えない。
4. **根拠の宣言が無い導出に確信度を付けない** — 導出木を推測しない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sbp.core.errors import RuleError
from sbp.reasoning.rules import (
    Fact,
    Propagation,
    RuleProgram,
    propagate_confidence,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PROCESS_DIR = REPO_ROOT / "ontology" / "process"
NORMS_DIR = REPO_ROOT / "ontology" / "norms"
PUMP = "p101"


@pytest.fixture(scope="module")
def program() -> RuleProgram:
    return RuleProgram.from_directories(PROCESS_DIR, NORMS_DIR)


class TestConfidencePropagation:
    def test_min_is_the_conjunction(self) -> None:
        assert propagate_confidence([0.9, 0.6, 0.8], Propagation.MIN) == pytest.approx(0.6)

    def test_product_is_for_independent_conjunction(self) -> None:
        assert propagate_confidence([0.9, 0.5], Propagation.PRODUCT) == pytest.approx(0.45)

    def test_noisy_or_is_the_disjunction(self) -> None:
        assert propagate_confidence([0.5, 0.5], Propagation.NOISY_OR) == pytest.approx(0.75)

    def test_no_support_means_no_confidence(self) -> None:
        """根拠の無い結論に確信度を与えない。"""
        assert propagate_confidence([], Propagation.MIN) == 0.0
        assert propagate_confidence([], Propagation.NOISY_OR) == 0.0

    def test_non_probabilities_are_rejected(self) -> None:
        with pytest.raises(RuleError, match="must be a probability"):
            propagate_confidence([1.2], Propagation.MIN)


class TestFactConstruction:
    def test_facts_become_clingo_symbols_not_strings(self) -> None:
        """文字列連結で ASP を組み立てない（記号 API を通す）。"""
        fact = Fact("holds_at", ("running", PUMP, 3), confidence=0.9)
        assert fact.key == "holds_at(running,p101,3)"

    def test_an_uppercase_predicate_is_rejected(self) -> None:
        """ASP では大文字始まりは変数。述語として渡せば黙って別物になる。"""
        with pytest.raises(RuleError, match="lowercase ASP identifier"):
            Fact("HoldsAt", ("running",))

    def test_confidence_must_be_a_probability(self) -> None:
        with pytest.raises(RuleError, match="fact confidence"):
            Fact("holds_at", ("running",), confidence=1.5)


class TestProgramLoading:
    def test_it_loads_both_process_and_norm_rules(self, program: RuleProgram) -> None:
        assert any(name.startswith("process/") for name in program.source_names)
        assert any(name.startswith("norms/") for name in program.source_names)

    def test_same_basename_in_two_directories_does_not_collide(self, program: RuleProgram) -> None:
        """process/ と norms/ に同名ファイルがあるのは普通。衝突したら後勝ちで規則が消える。"""
        assert len(set(program.source_names)) == len(program.source_names)
        assert len(program.source_names) >= 2

    def test_a_missing_file_fails_loudly(self) -> None:
        with pytest.raises(RuleError, match="rule file not found"):
            RuleProgram.from_files([PROCESS_DIR / "does-not-exist.lp"])

    def test_an_empty_directory_fails_loudly(self, tmp_path: Path) -> None:
        with pytest.raises(RuleError, match=r"no \.lp files found"):
            RuleProgram.from_directories(tmp_path)


class TestEventCalculusRuns:
    """M2 までは Python 側で事象計算を実装していたため、``.lp`` は実行されていなかった。"""

    def test_the_program_grounds(self, program: RuleProgram) -> None:
        """接地できること自体が要求である（安全でない変数を含む規則は接地できない）。"""
        result = program.solve([Fact("holds_at", ("running", PUMP, 3), confidence=0.9)], steps=5)
        assert result.satisfiable

    def test_inertia_carries_a_fluent_forward(self, program: RuleProgram) -> None:
        """事象が起きたら以降そのフルーエントは成立し続ける（慣性規則）。"""
        result = program.solve([Fact("happens", ("start_cmd", PUMP, 1), confidence=1.0)], steps=4)
        running = [d for d in result.of("holds_at") if d.args[0] == "running"]
        assert [int(d.args[2]) for d in running] == [2, 3, 4]

    def test_a_terminating_event_clips_the_fluent(self, program: RuleProgram) -> None:
        result = program.solve(
            [
                Fact("happens", ("start_cmd", PUMP, 1), confidence=1.0),
                Fact("happens", ("stop_cmd", PUMP, 3), confidence=1.0),
            ],
            steps=6,
        )
        running_steps = [int(d.args[2]) for d in result.of("holds_at") if d.args[0] == "running"]
        assert 2 in running_steps
        assert 3 in running_steps
        assert 5 not in running_steps

    def test_a_precondition_blocks_initiation(self, program: RuleProgram) -> None:
        """保全中は起動指令で稼働に入らない（設計書 §3.8 の条件付き initiates）。"""
        result = program.solve(
            [
                Fact("holds_at", ("under_maintenance", PUMP, 1), confidence=1.0),
                Fact("happens", ("start_cmd", PUMP, 1), confidence=1.0),
            ],
            steps=4,
        )
        initiated = [d for d in result.of("initiated") if d.args[0] == "running"]
        assert initiated == []


class TestExclusiveViolation:
    def test_a_collision_is_derived_not_rejected(self, program: RuleProgram) -> None:
        """排他の衝突は矛盾として落とさず、不一致候補として導出する（設計書 §3.9）。"""
        result = program.solve(
            [
                Fact("holds_at", ("running", PUMP, 3), confidence=0.9),
                Fact("holds_at", ("under_maintenance", PUMP, 3), confidence=0.6),
            ],
            steps=5,
        )
        violations = result.of("exclusive_violation")
        assert len(violations) == 1
        # min 伝播（.lp が宣言）: 両方が要るので弱い方に揃う
        assert violations[0].propagation is Propagation.MIN
        assert violations[0].confidence == pytest.approx(0.6)
        assert len(violations[0].support) == 2

    def test_the_rule_name_is_recorded(self, program: RuleProgram) -> None:
        result = program.solve(
            [
                Fact("holds_at", ("running", PUMP, 3), confidence=0.9),
                Fact("holds_at", ("under_maintenance", PUMP, 3), confidence=0.6),
            ],
            steps=5,
        )
        assert result.of("exclusive_violation")[0].rule == "exclusive_violation"


class TestEventLiftingInverse:
    def test_both_candidate_events_are_enumerated(self, program: RuleProgram) -> None:
        """稼働が真→偽になったとき、停止指令と故障の両方が候補になる（設計書 §5.3）。"""
        result = program.solve(
            [Fact("fluent_change", ("running", PUMP, 4, "true", "false"), confidence=0.85)],
            steps=6,
        )
        candidates = {d.args[0] for d in result.of("candidate_event")}
        assert candidates == {"stop_cmd", "fault"}

    def test_candidates_use_noisy_or(self, program: RuleProgram) -> None:
        result = program.solve(
            [Fact("fluent_change", ("running", PUMP, 4, "true", "false"), confidence=0.85)],
            steps=6,
        )
        candidate = result.of("candidate_event")[0]
        assert candidate.propagation is Propagation.NOISY_OR
        assert candidate.confidence == pytest.approx(0.85)


class TestNormRules:
    def test_norm_rules_declare_their_source(self, program: RuleProgram) -> None:
        """根拠文書まで遡れない規範違反は監査に使えない（設計書 §7.4）。"""
        result = program.solve([], steps=2)
        by_reference = {rule.rule_ref: rule for rule in result.norm_rules}
        assert "no_access_while_running" in by_reference
        rule = by_reference["no_access_while_running"]
        assert rule.source_document == "safety-standard"
        assert rule.source_clause == "2.1"
        assert rule.severity == "critical"

    def test_a_violation_is_derived_on_belief(self, program: RuleProgram) -> None:
        """稼働中に人が近づける状態は安全規程違反。確信度は min で伝播する。"""
        result = program.solve(
            [
                Fact("holds_at", ("running", PUMP, 3), confidence=0.9),
                Fact("holds_at", ("accessible", PUMP, 3), confidence=0.8),
            ],
            steps=5,
        )
        violations = result.of("norm_violation")
        assert [v.args[0] for v in violations] == ["no_access_while_running"]
        assert violations[0].confidence == pytest.approx(0.8)

    def test_no_violation_without_the_conditions(self, program: RuleProgram) -> None:
        result = program.solve([Fact("holds_at", ("running", PUMP, 3), confidence=0.9)], steps=5)
        assert result.of("norm_violation") == ()

    def test_weak_beliefs_still_derive_but_with_low_confidence(self, program: RuleProgram) -> None:
        """θ_5 のゲートは規則エンジンではなく kind5 が持つ（閾値を .lp に埋めない）。"""
        result = program.solve(
            [
                Fact("holds_at", ("running", PUMP, 3), confidence=0.25),
                Fact("holds_at", ("accessible", PUMP, 3), confidence=0.30),
            ],
            steps=5,
        )
        violations = result.of("norm_violation")
        assert len(violations) == 1
        assert violations[0].confidence == pytest.approx(0.25)
        assert result.of("norm_violation", min_confidence=0.7) == ()
