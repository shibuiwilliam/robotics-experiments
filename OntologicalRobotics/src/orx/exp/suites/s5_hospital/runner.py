"""S5 病院内搬送（T12, 規範層・H5）ランナー: 規範適合経路＋custody監査の4条件。

決定的（deontic 経路計画＋来歴記録）。LLM不使用・完全オフライン（ceiling/ablation 射程）。
新評価軸 = **監査可能性**（custody連鎖の完全回答率）。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.config import config_hash, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.exp.scenario import ScenarioExperimentConfig, ScenarioMeta, ScenarioSpec, register
from orx.exp.suites.s5_hospital.model import S5World
from orx.exp.suites.s5_hospital.reference import CONDITIONS
from orx.exp.suites.s5_hospital.scorer import S5ConditionScore, score_condition

WORLD = "configs/world/s5_hospital.yaml"

META = ScenarioMeta(
    id="s5",
    suite="T12",
    slug="hospital",
    title="病院内搬送：規範が支配する物理空間",
    tier="C",
    touchstones=["規範と監査", "custody連鎖"],
    hypotheses=["H5", "H6"],
    conditions=CONDITIONS,
    status="implemented",
)


class S5ConditionAgg(StrictModel):
    violations: int
    audit_completeness: float
    normative_cost: float
    delivered: int


class S5Result(StrictModel):
    scenario: str = "s5"
    exp_id: str
    name: str
    config_hash: str
    git_commit: str
    orx_version: str
    model_snapshot: str
    seeds: list[int]
    conditions: list[str]
    per_condition: dict[str, S5ConditionAgg]
    falsification: dict[str, bool]


def _eval(world: S5World) -> dict[str, S5ConditionAgg]:
    out: dict[str, S5ConditionAgg] = {}
    for c in CONDITIONS:
        s: S5ConditionScore = score_condition(world, c)
        out[c] = S5ConditionAgg(
            violations=s.violations,
            audit_completeness=s.audit_completeness,
            normative_cost=s.normative_cost,
            delivered=s.delivered,
        )
    return out


def run(config: ScenarioExperimentConfig, exp_dir: Path, notify: object) -> dict:
    notify_fn = notify if callable(notify) else (lambda *_: None)
    world = load_config(repo_root() / config.world_config, S5World)
    per_condition = _eval(world)
    for c in CONDITIONS:
        a = per_condition[c]
        notify_fn(
            f"  {c:<16} 違反={a.violations} 監査完全={a.audit_completeness:.3f} "
            f"規範コスト={a.normative_cost:.2f} 完遂={a.delivered}"
        )

    full = per_condition["OR-full"]
    falsification = {
        # 規範層無しは最短経路で禁止区画を通過する（違反）
        "no_normative_violates": per_condition["OR-no-normative"].violations > 0,
        # 来歴無しは custody 連鎖を再構成できず監査クエリに完全回答できない
        "no_prov_audit_incomplete": per_condition["OR-no-prov"].audit_completeness < 1.0,
        # B1 は規範も来歴も無く 違反かつ監査不能（最悪）
        "b1_violates_and_unauditable": (
            per_condition["B1"].violations > 0
            and per_condition["B1"].audit_completeness < 1.0
        ),
        # OR-full は違反0かつ監査完全（規範コストを定量化して許容）
        "or_full_compliant_auditable": (
            full.violations == 0
            and full.audit_completeness >= 1.0
            and full.normative_cost > 0.0
        ),
    }

    from orx import __version__
    from orx.exp.episode import _git_commit

    result = S5Result(
        exp_id=exp_dir.name,
        name=config.name,
        config_hash=config_hash(config),
        git_commit=_git_commit(),
        orx_version=__version__,
        model_snapshot="deterministic (deontic planner, no LLM)",
        seeds=list(config.seeds),
        conditions=CONDITIONS,
        per_condition=per_condition,
        falsification=falsification,
    )
    return result.model_dump(mode="json")


def demo(runs_root: Path, notify: object) -> tuple[dict, str]:
    config = ScenarioExperimentConfig(
        name="s5-demo", scenario="s5", world_config=WORLD, conditions=CONDITIONS,
        seeds=[501], duration_s=0.0,
    )
    exp_dir = runs_root / "scenario-s5-demo"
    n = 1
    while exp_dir.exists():
        n += 1
        exp_dir = runs_root / f"scenario-s5-demo-{n}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = run(config, exp_dir, notify)
    return result, render_report(result)


def summarize(result: dict) -> list[str]:
    lines = ["S5 病院内搬送（T12）— 規範適合＋custody監査:"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"  {c:<16} 違反={a['violations']} 監査完全={a['audit_completeness']:.3f} "
            f"規範コスト={a['normative_cost']:.2f} 完遂={a['delivered']}"
        )
    fal = result["falsification"]
    lines.append("失敗予言: " + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items()))
    return lines


def render_report(result: dict) -> str:
    from orx.exp import scope

    stamp = f" / git: `{result.get('git_commit', '?')}` / model: {result.get('model_snapshot', '?')}"  # noqa: E501
    lines = [
        f"# ORX Scenario Report — S5 病院内搬送（T12） `{result['exp_id']}`",
        "",
        f"- シナリオ: s5 / 仮説: 規範層, H5, H6 / 構成ハッシュ: `{result['config_hash']}`{stamp}",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 4条件は決定的ソルバ（no-normative/no-prov/B1=機構アブレーション, OR-full=規範＋来歴）。"
        "規範層は新設 `ontology/domains/normative.ttl`。agent 検証は未実行（live）。",
        "",
        "## 1. 失敗予言の検証結果",
        "",
        "| 予言 | 結果 |",
        "|------|------|",
    ]
    labels = {
        "no_normative_violates": "規範層なしは最短経路で禁止区画を通過（違反）",
        "no_prov_audit_incomplete": "来歴なしは custody を再構成できず監査不完全",
        "b1_violates_and_unauditable": "B1 は規範も来歴も無く違反かつ監査不能",
        "or_full_compliant_auditable": "OR-full は違反0かつ監査完全（規範コストは定量化し許容）",
    }
    for key, ok in result["falsification"].items():
        lines.append(f"| {labels.get(key, key)} | {'✓ PASS' if ok else '✗ FAIL'} |")

    lines += ["", "## 2. 規範遵守・監査可能性・効率（新評価軸=監査可能性）", "",
              "| 条件 | 規範違反 | 監査完全回答率 | 規範コスト(追加ホップ) | 搬送完遂 |",
              "|------|----------|----------------|------------------------|----------|"]
    for c in result["conditions"]:
        a = result["per_condition"][c]
        lines.append(
            f"| {c} | {a['violations']} | {a['audit_completeness']:.3f} | "
            f"{a['normative_cost']:.2f} | {a['delivered']} |"
        )
    lines += ["",
              "規範遵守は経路を延ばす（効率劣化）が、違反0と監査完全という規範要件を満たす。"
              "効率と規範のトレードオフを定量化できる点が OR の価値。",
              "", "## 3. トークン効率", "",
              "決定的ソルバ（情報層）のため **0 トークン**。規範層の agent 検証は live。", ""]
    return "\n".join(lines)


def register_self() -> None:
    register(
        ScenarioSpec(
            meta=META, run=run, demo=demo, render_report=render_report, summarize=summarize
        )
    )
