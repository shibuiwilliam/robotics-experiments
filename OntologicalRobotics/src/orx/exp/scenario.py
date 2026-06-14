"""シナリオ・レジストリと共通実験コンフィグ（T8〜T14）。

各シナリオは `src/orx/exp/suites/s{N}_{slug}/runner.py` に run/demo/render を実装し、
ここに登録する。CLI（`orx scenario ...`）と `orx report` はこのレジストリを介す。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orx.common.config import config_hash, load_config
from orx.common.schemas import StrictModel
from orx.replay.io import next_available_run_dir

RESULTS = "results.json"


class ScenarioMeta(StrictModel):
    id: str  # "s1"
    suite: str  # "T8"
    slug: str  # "lot_recall"
    title: str
    tier: str  # "A" | "B" | "C"
    touchstones: list[str]  # ["①越境同一性", ...]
    hypotheses: list[str]  # ["H2", "H6", "H7"]
    conditions: list[str]
    status: str  # "implemented" | "planned"


class ScenarioExperimentConfig(StrictModel):
    """シナリオ実験コンフィグ（`orx scenario run <cfg>` が読む）。"""

    name: str
    scenario: str  # "s1"
    world_config: str  # repo相対
    conditions: list[str]
    seeds: list[int]
    duration_s: float
    claim_ttl_s: float = 5.0
    knob: str | None = None  # 頑健性掃引ノブ（DegradationConfig フィールド）
    knob_values: list[float] = []
    params: dict[str, Any] = {}  # シナリオ固有パラメータ


# run(config, exp_dir, notify) -> result dict; demo(runs_root, notify) -> (result, report_md)
RunFn = Callable[[ScenarioExperimentConfig, Path, Any], dict]
DemoFn = Callable[[Path, Any], tuple[dict, str]]
ReportFn = Callable[[dict], str]
SummaryFn = Callable[[dict], list[str]]


class ScenarioSpec(StrictModel):
    model_config = {"arbitrary_types_allowed": True}
    meta: ScenarioMeta
    run: RunFn
    demo: DemoFn
    render_report: ReportFn
    summarize: SummaryFn


_REGISTRY: dict[str, ScenarioSpec] = {}


def register(spec: ScenarioSpec) -> None:
    _REGISTRY[spec.meta.id] = spec


def _ensure_loaded() -> None:
    """登録副作用のための遅延 import（循環回避）。"""
    if "s1" not in _REGISTRY:
        from orx.exp.suites.s1_lot_recall import runner as _s1

        _s1.register_self()
    if "s2" not in _REGISTRY:
        from orx.exp.suites.s2_allergen import runner as _s2

        _s2.register_self()
    if "s3" not in _REGISTRY:
        from orx.exp.suites.s3_multi_vendor import runner as _s3

        _s3.register_self()
    if "s6" not in _REGISTRY:
        from orx.exp.suites.s6_recycling import runner as _s6

        _s6.register_self()


def get(scenario_id: str) -> ScenarioSpec:
    _ensure_loaded()
    if scenario_id not in _REGISTRY:
        raise ValueError(
            f"未知のシナリオ {scenario_id!r}（対応: {sorted(_REGISTRY)}）"
        )
    return _REGISTRY[scenario_id]


def all_specs() -> list[ScenarioSpec]:
    _ensure_loaded()
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def load_scenario_experiment(path: Path) -> ScenarioExperimentConfig:
    config = load_config(path, ScenarioExperimentConfig)
    spec = get(config.scenario)
    unknown = [c for c in config.conditions if c not in spec.meta.conditions]
    if unknown:
        raise ValueError(
            f"シナリオ {config.scenario} の未知の条件 {unknown}"
            f"（対応: {spec.meta.conditions}）"
        )
    return config


def run_experiment(
    config: ScenarioExperimentConfig, runs_root: Path, progress: object | None = None
) -> tuple[Path, dict]:
    """シナリオ実験を実行し、exp ディレクトリと結果dictを返す。"""
    spec = get(config.scenario)
    exp_dir = next_available_run_dir(
        runs_root, f"scenario-{config.scenario}-{config_hash(config)[:8]}"
    )
    exp_dir.mkdir(parents=True, exist_ok=True)
    result = spec.run(config, exp_dir, progress)
    (exp_dir / RESULTS).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return exp_dir, result


def is_scenario_result(exp_dir: Path) -> bool:
    path = exp_dir / RESULTS
    if not path.exists():
        return False
    try:
        return "scenario" in json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False


def write_report(exp_dir: Path, out_dir: Path) -> Path:
    data = json.loads((exp_dir / RESULTS).read_text(encoding="utf-8"))
    spec = get(data["scenario"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{exp_dir.name}.md"
    out_path.write_text(spec.render_report(data), encoding="utf-8")
    return out_path


# ----------------------------------------------------------------- milestones

TIER_A = ["s1", "s2", "s6"]  # SCENARIOS.md §5: M-Scenario-A の対象


def run_milestone(
    scenario_ids: list[str], runs_root: Path, progress: object | None = None
) -> tuple[Path, dict]:
    """複数シナリオを標準実験コンフィグで実行し、比較レポート用に結果を束ねる。"""
    from orx.common.paths import repo_root

    notify = progress if callable(progress) else (lambda *_: None)
    results: dict[str, dict] = {}
    for sid in scenario_ids:
        spec = get(sid)
        cfg_path = repo_root() / "configs" / "experiments" / f"{sid}_{spec.meta.slug}.yaml"
        config = load_scenario_experiment(cfg_path)
        notify(f"=== {sid} ({spec.meta.suite}) ===")
        _exp_dir, result = run_experiment(config, runs_root, progress)
        results[sid] = result
    out = {"scenarios": scenario_ids, "results": results}
    return runs_root, out


def render_milestone(name: str, milestone: dict) -> str:
    from orx.exp import scope

    ids = milestone["scenarios"]
    results = milestone["results"]
    lines = [
        f"# ORX Milestone Report — {name}",
        "",
        f"対象シナリオ: {', '.join(ids)}（SCENARIOS.md §5）。",
        "",
        scope.scope_section([scope.CEILING, scope.ABLATION]),
        "> 全シナリオの数値は決定的リファレンスソルバ（ceiling/ablation）。"
        "**仮説 H1–H7 のエージェントレベル検証は未実行（live）。**",
        "",
        "## 反証予言の総括",
        "",
        "| シナリオ | Suite | 仮説 | 反証 green | 構成ハッシュ |",
        "|----------|-------|------|-----------|--------------|",
    ]
    all_green = True
    for sid in ids:
        spec = get(sid)
        r = results[sid]
        fal = r.get("falsification", {})
        green = all(fal.values()) if fal else False
        all_green = all_green and green
        mark = "✓ 全green" if green else "✗ 未達"
        lines.append(
            f"| {sid} {spec.meta.title} | {spec.meta.suite} | "
            f"{','.join(spec.meta.hypotheses)} | {mark} | `{r.get('config_hash', '?')}` |"
        )
    lines += ["", f"**Tier A 反証総合判定: {'全green ✓' if all_green else '未達 ✗'}**", ""]

    for sid in ids:
        spec = get(sid)
        lines += [f"## {sid} — {spec.meta.title}（{spec.meta.suite}）", ""]
        # 各 summarize() は末尾に失敗予言行を含む
        lines += [f"- {line}" for line in spec.summarize(results[sid])]
        lines.append("")
    lines += [
        "## 注記",
        "",
        "各シナリオの数値は表現上限（決定的リファレンスソルバ）と機構アブレーションであり、"
        "「オントロジーを使う LLM エージェントが生データに勝つ」という仮説本体ではない。"
        "エージェントレベル検証は live 計測（OPENAI_API_KEY＋コスト承認）で別途実施する。",
        "",
    ]
    return "\n".join(lines)
