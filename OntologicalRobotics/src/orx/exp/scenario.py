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
from orx.common.providers import ProviderConfig
from orx.common.schemas import StrictModel
from orx.replay.io import next_available_run_dir

RESULTS = "results.json"


def comparison_section(
    comparisons: list[dict], title: str = "対比較（対応のある検定）"
) -> list[str]:
    """results.json の comparisons（list[dict]）を Markdown 表に整形する（R-3d 共通）。

    決定的シナリオでは各 seed が独立入力（episode 生成が seed 依存）であることを前提に、
    OR-full vs 各ベースラインの McNemar/Wilcoxon/ブートストラップCI を表示する。
    """
    if not comparisons:
        return []
    lines = [
        f"## {title}",
        "",
        "| 対 | 成功率(A/B) | McNemar p | 連続指標 | Wilcoxon p | 成功率差95%CI |",
        "|----|-------------|-----------|----------|------------|----------------|",
    ]
    for c in comparisons:
        wm = c.get("wilcoxon_metric") or "—"
        wp = c.get("wilcoxon_p")
        wp_s = f"{wp:.2e}" if isinstance(wp, (int, float)) else "—"
        lines.append(
            f"| {c['condition_a']} vs {c['condition_b']} | "
            f"{c['success_rate_a']:.3f}/{c['success_rate_b']:.3f} | "
            f"{c['mcnemar_p']:.2e} | {wm} | {wp_s} | "
            f"[{c['diff_ci_low']:+.3f}, {c['diff_ci_high']:+.3f}] |"
        )
    lines.append("")
    return lines


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
    # agent（live）射程の条件（"*-llm"）を回すときのプロバイダ設定（R-3a）。
    # None = 決定的ソルバのみ（従来）。mode=openai は要コスト承認。
    provider: ProviderConfig | None = None


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
    if "s4" not in _REGISTRY:
        from orx.exp.suites.s4_inspection import runner as _s4

        _s4.register_self()
    if "s5" not in _REGISTRY:
        from orx.exp.suites.s5_hospital import runner as _s5

        _s5.register_self()
    if "s6" not in _REGISTRY:
        from orx.exp.suites.s6_recycling import runner as _s6

        _s6.register_self()
    if "s7" not in _REGISTRY:
        from orx.exp.suites.s7_ownership import runner as _s7

        _s7.register_self()
    if "s8" not in _REGISTRY:
        from orx.exp.suites.s8_fulfillment import runner as _s8

        _s8.register_self()


def get(scenario_id: str) -> ScenarioSpec:
    _ensure_loaded()
    if scenario_id not in _REGISTRY:
        raise ValueError(f"未知のシナリオ {scenario_id!r}（対応: {sorted(_REGISTRY)}）")
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
            f"シナリオ {config.scenario} の未知の条件 {unknown}（対応: {spec.meta.conditions}）"
        )
    return config


def _archive_configs(config: ScenarioExperimentConfig, exp_dir: Path) -> dict[str, str]:
    """実験・世界コンフィグを run ディレクトリへ自己完結アーカイブする（D-3）。

    run ディレクトリ＋ソース版（git_commit）だけで再実行が完全に規定されるようにする。
    """
    from orx.common.paths import repo_root

    (exp_dir / "experiment_config.json").write_text(
        config.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    archived = {"experiment_config": "experiment_config.json"}
    world_src = repo_root() / config.world_config
    if world_src.exists():
        (exp_dir / "world_config.yaml").write_text(
            world_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        archived["world_config"] = "world_config.yaml"
    return archived


def run_experiment(
    config: ScenarioExperimentConfig, runs_root: Path, progress: object | None = None
) -> tuple[Path, dict]:
    """シナリオ実験を実行し、exp ディレクトリと結果dictを返す。

    全シナリオ共通のチョークポイントとして、per-run 構造化ログ（log.jsonl, D-2）と
    コンフィグアーカイブ＋記録方式メタデータ（D-3）をここで一括して行う。
    """
    from orx.common.logging import make_run_logger

    spec = get(config.scenario)
    exp_dir = next_available_run_dir(
        runs_root, f"scenario-{config.scenario}-{config_hash(config)[:8]}"
    )
    exp_dir.mkdir(parents=True, exist_ok=True)
    archived = _archive_configs(config, exp_dir)

    logger = make_run_logger(exp_dir, run_id=exp_dir.name, scenario=config.scenario)
    logger.info(
        "run_start",
        name=config.name,
        config_hash=config_hash(config),
        world_config=config.world_config,
        conditions=config.conditions,
        seeds=config.seeds,
        knob=config.knob,
        knob_values=config.knob_values,
        provider_mode=config.provider.mode if config.provider else None,
    )
    notify = progress if callable(progress) else (lambda *_: None)

    def _notify(msg: object) -> None:
        logger.info("progress", message=str(msg))
        notify(msg)

    result = spec.run(config, exp_dir, _notify)
    # 記録方式の機械可読メタデータ（D-3）: episodes/ を書いた suite は "episodes"、
    # world+seed から決定的に導出される suite は "deterministic-regeneration"。
    scheme = "episodes" if (exp_dir / "episodes").exists() else "deterministic-regeneration"
    result["recording"] = {"scheme": scheme, **archived}
    (exp_dir / RESULTS).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "run_end",
        scope=result.get("scope"),
        git_commit=result.get("git_commit"),
        model_snapshot=result.get("model_snapshot"),
        recording_scheme=scheme,
        falsification=result.get("falsification"),
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


# ----------------------------------------------- scenario-all 恒久ログ（R-3e）


def _meta_for(sid: str) -> ScenarioMeta | None:
    try:
        return get(sid).meta
    except ValueError:
        return None


def has_provenance(results: dict) -> bool:
    """results.json が再現性スタンプ（PROJECT.md §8.3）を持つか。

    provenance 焼き込み導入前の legacy run（git_commit / model_snapshot が null）は
    集計・最新選択から除外する（IMPROVEMENT.md D-4）。データ自体は削除しない。
    """
    return all(
        isinstance(results.get(k), str) and results[k]
        for k in ("git_commit", "model_snapshot", "config_hash")
    )


def latest_scenario_results(runs_root: Path) -> dict[str, dict]:
    """各シナリオの**最新**（mtime 最大）の results.json を集める（scenario-all 集約用）。

    provenance を欠く legacy run は対象外（D-4）。
    """
    latest: dict[str, tuple[float, dict]] = {}
    for path in runs_root.glob("scenario-*/results.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sid = data.get("scenario")
        if not sid or not has_provenance(data):
            continue
        mtime = path.stat().st_mtime
        if sid not in latest or mtime > latest[sid][0]:
            latest[sid] = (mtime, data)
    return {sid: d for sid, (_m, d) in sorted(latest.items())}


def _comparison_digest(d: dict) -> str:
    """results の comparisons を 1 行ダイジェストに（最も弱い=最大 p を代表値に, R-1B）。

    決定的・seed 非依存で対比較を持たないシナリオ（S5）は「なし」を明示する
    （空セルは「検定漏れ」と誤読されるため理由を書く）。
    """
    cmps = d.get("comparisons", [])
    if not cmps:
        return "対比較: なし（決定的・seed 非依存のため p 値を出さない設計）"
    primary = cmps[0]["condition_a"]
    bases = [c["condition_b"] for c in cmps]
    mcn = [c["mcnemar_p"] for c in cmps if isinstance(c.get("mcnemar_p"), (int, float))]
    wil = [c["wilcoxon_p"] for c in cmps if isinstance(c.get("wilcoxon_p"), (int, float))]
    parts = [f"対比較: {primary} vs {{{', '.join(bases)}}} (n={len(cmps)})"]
    if mcn:
        parts.append(f"McNemar p≤{max(mcn):.2e}")
    if wil:
        wm = next((c.get("wilcoxon_metric") for c in cmps if c.get("wilcoxon_metric")), "連続指標")
        parts.append(f"Wilcoxon({wm}) p≤{max(wil):.2e}")
    return " / ".join(parts)


def _robustness_digest(d: dict) -> list[str]:
    """頑健性掃引の端点ダイジェスト（knob 全域での primary vs baseline 曲線, R-1B）。

    集約サマリに曲線そのものは載せないが「OR がどの領域で立ち上がる/守るか」が一目で分かるよう、
    primary 条件の各曲線指標を knob 端点（最初→最後）で示す。詳細曲線は個別 reports/ にある。
    """
    rob = d.get("robustness") or {}
    knob = rob.get("knob")
    values = rob.get("values") or []
    if not knob or not values:
        return []
    conds = d.get("conditions", [])
    primary = conds[0] if conds else None
    curve_metrics = [k for k in rob if k not in ("knob", "values") and isinstance(rob[k], dict)]
    lines = [f"頑健性: `{knob}` {values[0]}→{values[-1]} 掃引（primary={primary}）"]
    for m in curve_metrics[:2]:
        series = rob[m].get(primary) if primary else None
        if not series:
            continue
        lines[-1] += f" / {m}: {series[0]:.3f}→{series[-1]:.3f}"
    return lines


def _s7_safety_note(d: dict) -> list[str]:
    """S7 専用: 主操作点 1 点（success 低）で「弱い」と誤読されないよう安全側挙動を明示（R-1B）。

    OR-full は look-alike が困難になっても**誤配送率を全域 0 に保ち**、自動成功を確認委譲へ
    切り替える。success_rate の低下＝安全を優先した委譲であることを曲線で示す。
    """
    rob = d.get("robustness") or {}
    if d.get("scenario") != "s7" or not rob:
        return []
    values = rob.get("values") or []
    mis = (rob.get("misdelivery_rate") or {}).get("OR-full")
    suc = (rob.get("success_rate") or {}).get("OR-full")
    if not values or mis is None or suc is None:
        return []
    mis_max = max(mis)
    return [
        "",
        f"> **S7 安全 vs 自動化（誤読防止）**: OR-full は look-alike 分離 `lookalike_sep` "
        f"{values[0]}→{values[-1]} の**全域で誤配送率 {mis_max:.3f}**（=0 を維持）。"
        f"自動成功率は {suc[0]:.3f}→{suc[-1]:.3f} と低下するが、これは"
        "**瓜二つほど確認(X5)へ委譲して誤配送ゼロを死守**するため。"
        "主操作点の success 単独を「弱い」と読まないこと。",
    ]


def render_scenario_all(results: dict[str, dict], date_str: str) -> str:
    """全シナリオの結果を 1 枚の恒久サマリ（Markdown）に集約する（R-3e）。

    各シナリオの反証全 ✓ / 条件数 / シード数 / 対比較件数 / スタンプを 1 行に畳み、
    各シナリオに**対比較ダイジェスト・頑健性曲線ダイジェスト**を併記する（R-1B: 集約サマリで
    曲線・対比較・S7 安全側挙動が抜けて横断比較ログとして不足する問題の是正）。
    `make scenario-all` から呼ばれ `reports/scenario-all-<date>.md` に保存される。
    """
    lines = [
        f"# ORX Scenario-All Summary — {date_str}",
        "",
        f"- 対象シナリオ: {len(results)} 件 / 生成 `make scenario-all`（決定的・ceiling/ablation）",
        "",
        "| S | Suite | 仮説 | 反証 | 条件数 | シード | 対比較 | git / model |",
        "|---|-------|------|------|--------|--------|--------|-------------|",
    ]
    total_pred = total_pass = 0
    for sid, d in results.items():
        fal = d.get("falsification", {})
        npred = len(fal)
        npass = sum(1 for v in fal.values() if v)
        total_pred += npred
        total_pass += npass
        meta = _meta_for(sid)
        suite = meta.suite if meta else "?"
        hyps = ",".join(meta.hypotheses) if meta else "?"
        ncmp = len(d.get("comparisons", []))
        stamp = f"`{d.get('git_commit', '?')}` / {d.get('model_snapshot', '?')}"
        mark = "✓ 全" if npass == npred and npred else f"{npass}/{npred}"
        lines.append(
            f"| {sid} | {suite} | {hyps} | {mark} | {len(d.get('conditions', []))} | "
            f"{len(d.get('seeds', []))} | {ncmp} | {stamp} |"
        )
    lines += [
        "",
        f"**反証予言: {total_pass}/{total_pred} ✓**。"
        "数値は決定的リファレンスソルバ（ceiling/ablation）。agent 射程は別途 live（R-3a）。",
        "",
        "## 条件別主要指標",
        "",
    ]
    for sid, d in results.items():
        meta = _meta_for(sid)
        title = meta.title if meta else sid
        lines.append(f"### {sid} — {title}")
        conds = d.get("conditions", [])
        pc = d.get("per_condition", {})
        if pc and conds:
            first = pc[conds[0]]
            metric_keys = [k for k in first if isinstance(first[k], (int, float))][:4]
            lines += [
                "",
                "| 条件 | " + " | ".join(metric_keys) + " |",
                "|------|" + "------|" * len(metric_keys),
            ]
            for c in conds:
                row = pc.get(c, {})
                cells = " | ".join(
                    f"{row[k]:.3f}" if isinstance(row.get(k), float) else str(row.get(k, ""))
                    for k in metric_keys
                )
                lines.append(f"| {c} | {cells} |")
        # 対比較・頑健性ダイジェスト（R-1B: 集約サマリに統計と曲線の要約を載せる）
        lines += ["", f"- {_comparison_digest(d)}"]
        lines += [f"- {ln}" for ln in _robustness_digest(d)]
        lines += [
            "- 詳細（曲線・較正・対比較表）: 個別 `reports/scenario-"
            f"{sid}-*.md` および results.json を参照",
        ]
        lines += _s7_safety_note(d)
        lines.append("")
    return "\n".join(lines)


def write_scenario_all(
    runs_root: Path, out_dir: Path, date_str: str, time_suffix: str = ""
) -> Path | None:
    """最新の各シナリオ結果を集約し `reports/scenario-all-<date>.md` を書き出す。

    `time_suffix`（例 "06-39-53"）を渡すと `scenario-all-<date>T<time>.md` となり、
    同日再実行での上書きを避け履歴を保持できる（R-C: 恒久サマリの mtime 上書き脆弱性の解消）。
    既定（空文字）では従来通り日付のみのファイル名で後方互換を保つ。
    """
    results = latest_scenario_results(runs_root)
    if not results:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    label = f"{date_str}T{time_suffix}" if time_suffix else date_str
    out_path = out_dir / f"scenario-all-{label}.md"
    out_path.write_text(render_scenario_all(results, label), encoding="utf-8")
    return out_path


# ----------------------------------------------------------------- milestones

TIER_A = ["s1", "s2", "s6"]  # SCENARIOS.md §5: M-Scenario-A の対象
TIER_ALL = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]  # 全シナリオ比較（s8=キネティック層）


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
    lines += ["", f"**反証総合判定: {'全green ✓' if all_green else '未達 ✗'}**", ""]

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
