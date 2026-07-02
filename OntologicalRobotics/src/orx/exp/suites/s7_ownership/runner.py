"""S7 介護施設「所有」（T14, H2/H4/H6）ランナー: 三系統融合4条件→誤配送採点→類似度掃引。

決定的（視覚署名＋所有台帳＋最終目撃）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.record import persist_episode
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.stats import PairedComparison, paired_comparisons
from orx.exp.suites.s7_ownership import generator
from orx.exp.suites.s7_ownership.agent import AGENT_CONDITIONS
from orx.exp.suites.s7_ownership.model import S7World
from orx.exp.suites.s7_ownership.reference import CONDITIONS
from orx.exp.suites.s7_ownership.scorer import S7ConditionScore, score_condition

WORLD = "configs/world/s7_ownership.yaml"
_DEFAULT_SEP = 0.5

META = ScenarioMeta(
    id="s7",
    suite="T14",
    slug="ownership",
    title="介護施設：所有という知覚不可能な関係",
    tier="B",
    touchstones=["情報的関係の同一性", "三系統融合（記号×時空間×ベクトル）"],
    hypotheses=["H2", "H4", "H6"],
    conditions=CONDITIONS + AGENT_CONDITIONS,
    status="implemented",
)


class S7ConditionAgg(StrictModel):
    success_rate: float
    misdelivery_rate: float
    escalation_rate: float


class S7Result(StrictModel):
    scenario: str = "s7"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    primary_sep: float
    per_condition: dict[str, S7ConditionAgg]
    comparisons: list[PairedComparison] = []
    falsification: dict[str, bool]
    robustness: dict
    scope: str = "deterministic"  # "deterministic" | "agent" (R-B)
    total_tokens: dict[str, int] = {}


def _eval_seeds(
    world: S7World,
    seeds: list[int],
    sep: float,
    record_dir: Path | None = None,
    knob: str | None = None,
) -> tuple[dict[str, S7ConditionAgg], dict[str, list[S7ConditionScore]]]:
    per: dict[str, list[S7ConditionScore]] = {c: [] for c in CONDITIONS}
    for seed in seeds:
        ep = generator.generate_episode(world, seed, sep)
        persist_episode(record_dir, ep, seed, knob, sep)  # D-3
        for c in CONDITIONS:
            rng = SeedTree(seed).child("s7").child(c).rng()
            per[c].append(score_condition(world, ep, c, rng))
    agg: dict[str, S7ConditionAgg] = {}
    for c in CONDITIONS:
        rows = per[c]
        n = len(rows)
        agg[c] = S7ConditionAgg(
            success_rate=round(sum(r.success_rate for r in rows) / n, 6),
            misdelivery_rate=round(sum(r.misdelivery_rate for r in rows) / n, 6),
            escalation_rate=round(sum(r.escalation_rate for r in rows) / n, 6),
        )
    return agg, per


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    if any(c.endswith("-llm") for c in config.conditions):
        return run_agent(config, exp_dir, notify)
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S7World)
    sep = float(config.params.get("primary_sep", _DEFAULT_SEP))

    per_condition, per_seed = _eval_seeds(
        world, config.seeds, sep, record_dir=exp_dir, knob="lookalike_sep"
    )
    for c in CONDITIONS:
        a = per_condition[c]
        notify_fn(
            f"  {c:<8} 成功={a.success_rate:.3f} 誤配送={a.misdelivery_rate:.3f} "
            f"確認={a.escalation_rate:.3f}"
        )

    full = per_condition["OR-full"]
    falsification = {
        # H6/H2: B0 は「誰のものか」の情報経路が無く誤配送多数
        "B0_no_ownership_path": per_condition["B0"].misdelivery_rate > 0.5,
        # H4: OR-vec は所有関係を扱えず瓜二つで誤配送
        "OR_vec_lookalike_misdelivery": per_condition["OR-vec"].misdelivery_rate > 0.0,
        # H4: OR-sym は視覚署名が無く ID無し瓜二つを判別できず確認多数・自動成功低
        "OR_sym_cannot_disambiguate": (
            per_condition["OR-sym"].escalation_rate > 0.5
            and per_condition["OR-sym"].success_rate < full.success_rate
        ),
        # OR-full は三系統融合＋確認(X5)で誤配送ほぼ0かつ最高成功
        "OR_full_no_misdelivery": (
            full.misdelivery_rate <= 1e-9
            and full.success_rate >= per_condition["OR-vec"].success_rate
            and full.success_rate >= per_condition["OR-sym"].success_rate
        ),
    }

    # 対比較（OR-full vs OR-vec/OR-sym/B0, R-3d）: 成否＝誤配送0（安全側）、指標=成功率。
    success_by = {c: [s.misdelivery_rate <= 1e-9 for s in per_seed[c]] for c in CONDITIONS}
    metric_by = {c: [s.success_rate for s in per_seed[c]] for c in CONDITIONS}
    comparisons = paired_comparisons(
        "OR-full",
        [c for c in CONDITIONS if c != "OR-full"],
        success_by,
        metric_by,
        "success_rate",
        config.seeds[0],
    )

    knob = config.knob or "lookalike_sep"
    values = config.knob_values or [1.5, 1.0, 0.5, 0.25]
    misdeliv_curve = {c: [] for c in CONDITIONS}
    success_curve = {c: [] for c in CONDITIONS}
    for v in values:
        agg, _ = _eval_seeds(world, config.seeds, v, record_dir=exp_dir, knob=knob)
        for c in CONDITIONS:
            misdeliv_curve[c].append(round(agg[c].misdelivery_rate, 6))
            success_curve[c].append(round(agg[c].success_rate, 6))
        notify_fn(
            f"  {knob}={v}: " + " ".join(f"{c}={agg[c].misdelivery_rate:.2f}" for c in CONDITIONS)
        )
    robustness = {
        "knob": knob,
        "values": values,
        "misdelivery_rate": misdeliv_curve,
        "success_rate": success_curve,
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S7Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (stub visual signature, no LLM)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        primary_sep=sep,
        per_condition=per_condition,
        comparisons=comparisons,
        falsification=falsification,
        robustness=robustness,
    )
    return result.model_dump(mode="json")


def _score_decisions(world: S7World, episode, decisions: dict[str, int]) -> S7ConditionScore:
    """エージェントの配送決定 {resident: index|ESCALATE} を真値で採点する（oracle 経由）。"""
    from orx.oracle.scenarios.s7 import delivery_outcome

    n = len(world.residents)
    success = misdeliv = escal = 0
    for r in world.residents:
        outcome = delivery_outcome(decisions.get(r, -1), r, episode.objects)
        if outcome == "success":
            success += 1
        elif outcome == "misdelivery":
            misdeliv += 1
        else:
            escal += 1
    return S7ConditionScore(
        success_rate=round(success / n, 6),
        misdelivery_rate=round(misdeliv / n, 6),
        escalation_rate=round(escal / n, 6),
    )


def run_agent(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    """S7 を**実 LLM エージェント**が配送（双対表現の蒸留情報の有無で対比, agent 射程・R-B）。"""
    from orx.common.providers import make_llm_client
    from orx.exp.suites.s7_ownership.agent import decide_episode_llm

    notify_fn = notify if callable(notify) else (lambda *_: None)
    if config.provider is None:
        raise ValueError("agent 条件には provider 設定が必要です")
    world = load_config(repo_root() / config.world_config, S7World)
    sep = float(config.params.get("primary_sep", _DEFAULT_SEP))
    conds = [c for c in config.conditions if c in AGENT_CONDITIONS]
    llm = make_llm_client(config.provider)

    per: dict[str, list[S7ConditionScore]] = {c: [] for c in conds}
    tokens: dict[str, int] = {c: 0 for c in conds}
    for seed in config.seeds:
        ep = generator.generate_episode(world, seed, sep)
        for c in conds:
            decisions, rr = decide_episode_llm(c, ep, world, llm)
            per[c].append(_score_decisions(world, ep, decisions))
            tokens[c] += rr.prompt_tokens + rr.completion_tokens
        notify_fn(
            f"  seed={seed}: " + " ".join(f"{c}=成功{per[c][-1].success_rate:.2f}" for c in conds)
        )

    per_condition: dict[str, S7ConditionAgg] = {}
    for c in conds:
        rows = per[c]
        n = len(rows)
        per_condition[c] = S7ConditionAgg(
            success_rate=round(sum(r.success_rate for r in rows) / n, 6),
            misdelivery_rate=round(sum(r.misdelivery_rate for r in rows) / n, 6),
            escalation_rate=round(sum(r.escalation_rate for r in rows) / n, 6),
        )
    primary = "OR-full-llm"
    success_by = {c: [r.misdelivery_rate <= 1e-9 for r in per[c]] for c in conds}
    metric_by = {c: [r.success_rate for r in per[c]] for c in conds}
    comparisons = paired_comparisons(
        primary,
        [c for c in conds if c != primary],
        success_by,
        metric_by,
        "success_rate",
        config.seeds[0],
    )
    falsification = {
        # OR-full-llm は蒸留双対表現で誤配送を避け最高成功
        "OR_full_llm_no_misdelivery": (
            per_condition[primary].misdelivery_rate <= 1e-9
            and all(
                per_condition[primary].success_rate >= per_condition[b].success_rate
                for b in conds
                if b != primary
            )
        ),
        # 蒸留情報を欠く baseline は瓜二つを誤配送 or 過剰委譲で自動成功が低い
        "baseline_worse_without_dual_rep": any(
            per_condition[b].misdelivery_rate > 0.0
            or per_condition[b].success_rate < per_condition[primary].success_rate
            for b in conds
            if b != primary
        ),
    }
    # ツール側ガード（S7 mixed への対処）: 低マージン配送を機械強制で ESCALATE に上書きすると、
    # 決定的版の安全（誤配送0）がエージェントにも移る（プロンプト依存の確率的順守を補う）。
    guarded = "OR-full-llm-guarded"
    if guarded in per_condition:
        falsification["guard_reduces_misdelivery"] = (
            per_condition[guarded].misdelivery_rate < per_condition[primary].misdelivery_rate
            or per_condition[primary].misdelivery_rate <= 1e-9
        )

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S7Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot=f"live: {config.provider.llm_model} (mode={config.provider.mode})",
        seeds=list(config.seeds),
        conditions=conds,
        primary_sep=sep,
        per_condition=per_condition,
        comparisons=comparisons,
        falsification=falsification,
        robustness={},
        scope="agent",
        total_tokens=tokens,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s7-demo",
        scenario="s7",
        world_config=WORLD,
        conditions=CONDITIONS,
        seeds=[701, 702, 703, 704],
        duration_s=0.0,
        knob="lookalike_sep",
        knob_values=[1.5, 1.0, 0.6, 0.3],
        params={"primary_sep": 0.6},
    )
    exp_dir = runs_root / "scenario-s7-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s7-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = [f"S7 介護「所有」（T14）— 配送（look-alike分離 sep={result['primary_sep']}）:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<8} 成功={a['success_rate']:.3f} 誤配送={a['misdelivery_rate']:.3f} "
            f"確認={a['escalation_rate']:.3f}"
        )
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def _render_agent_report(result: dict) -> str:
    from orx.exp import scope
    from orx.exp.scenario import comparison_section

    mode = (
        "openai"
        if "mode=openai" in result.get("model_snapshot", "")
        else ("cache" if "mode=cache" in result.get("model_snapshot", "") else "stub")
    )
    lines = [
        f"# ORX Scenario Report — S7 介護「所有」（T14・agent/live） `{result['exp_id']}`",
        "",
        f"- agent 検証（双対表現の蒸留情報が LLM の所有同定を助けるか, H2/H4/H6）/ "
        f"model: {result.get('model_snapshot', '?')} / git: `{result.get('git_commit', '?')}`",
        "",
        scope.scope_section([scope.AGENT], scope.agent_status_for(mode)),
        "> **射程注記**: H2/H4/H6（三系統融合）の機構検証は決定的 OR-vec/OR-sym/B0 が担う。"
        "本 agent 版は接地済みの**最終目撃ゾーン＋note署名コサイン類似度（双対表現の蒸留）**を"
        "実 LLM が使って瓜二つを正しく配送し確信不足で委譲できるかを測る別射程の検証。",
        "",
        "## 1. 配送成績（agent）",
        "",
        "| 条件 | 自動成功率 | 誤配送率 | 確認(委譲)率 |",
        "|------|-----------|----------|--------------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['success_rate']:.3f} | {a['misdelivery_rate']:.3f} | "
            f"{a['escalation_rate']:.3f} |"
        )
    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full-llm vs B0-llm・成否=誤配送0/指標=成功率）",
        ),
    ]
    tok = result.get("total_tokens", {})
    if tok:
        lines += ["", "## 2. トークン", "", "| 条件 | 総トークン |", "|------|-----------|"]
        for c in result["conditions"]:
            lines.append(f"| {c} | {tok.get(c, 0)} |")
    return "\n".join(lines) + "\n"


def render_report(result: dict) -> str:
    if result.get("scope") == "agent":
        return _render_agent_report(result)
    from orx.exp import scope

    stamp = (
        f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
    )
    lines = [
        f"# ORX Scenario Report — S7 介護「所有」（T14） `{result['exp_id']}`",
        "",
        f"- シナリオ: s7 / 仮説: H2,H4,H6 / シード数: {len(result['seeds'])} / "
        f"分離sep={result['primary_sep']} / 構成ハッシュ: `{result['config_hash']}`{stamp}",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 4条件は決定的ソルバ（B0/OR-vec/OR-sym=機構アブレーション, OR-full=三系統融合）。"
        "LLM/実埋め込みでの H2/H4/H6 検証は未実行（live）。",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "B0_no_ownership_path": "B0 は『誰のものか』の情報経路が無く誤配送多数（H6）",
        "OR_vec_lookalike_misdelivery": "OR-vec は所有関係を扱えず瓜二つで誤配送（H4）",
        "OR_sym_cannot_disambiguate": "OR-sym は視覚署名が無く瓜二つを判別できず確認多数（H4）",
        "OR_full_no_misdelivery": "OR-full は三系統融合＋確認(X5)で誤配送≈0・最高成功",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")

    lines += [
        "",
        f"### 条件別サマリ（sep={result['primary_sep']}）",
        "",
        "| 条件 | 自動成功率 | 誤配送率 | 確認(委譲)率 |",
        "|------|-----------|----------|--------------|",
    ]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['success_rate']:.3f} | {a['misdelivery_rate']:.3f} | "
            f"{a['escalation_rate']:.3f} |"
        )

    from orx.exp.scenario import comparison_section

    lines += [
        "",
        *comparison_section(
            result.get("comparisons", []),
            "対比較（OR-full vs OR-vec/OR-sym/B0・対応のある検定）",
        ),
    ]

    rob = result.get("robustness") or {}
    lines += ["", "## 2. 頑健性曲線（look-alike 分離 × 誤配送率）", ""]
    if rob:
        header = f"| {rob['knob']} | " + " | ".join(result["conditions"]) + " |"
        lines += [header, "|------|" + "------|" * len(result["conditions"])]
        for i, v in enumerate(rob["values"]):
            cells = " | ".join(f"{rob['misdelivery_rate'][c][i]:.2f}" for c in result["conditions"])
            lines.append(f"| {v} | {cells} |")
        lines.append("\n（sep小=瓜二つほど OR-vec の誤配送増、OR-full は確認で誤配送0を維持）")
    lines += [
        "",
        "## 3. トークン効率",
        "",
        "決定的ソルバ（情報層）のため **0 トークン**。H2/H4/H6 の agent 検証は live。",
        "",
    ]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
