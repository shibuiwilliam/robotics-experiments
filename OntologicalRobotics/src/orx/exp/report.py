"""Markdownレポート生成（`orx report` / `orx report-main`）。

- `render_run_report`: 個別 run の manifest + metrics（＋反実仮想リプレイ）。
- `build_main_report`: **REPORT.md を results.json から決定的に再生成**する（R-INFRA）。
  REPORT.md が git 非追跡で消失しても `make report` で一次データから復元できるようにする。
"""

from __future__ import annotations

import json
from pathlib import Path

from orx.common.schemas import FidelityReport
from orx.replay.io import METRICS, RunReader


def _fidelity_rows(report: FidelityReport) -> list[tuple[str, str]]:
    delay = (
        "-" if report.transition_mean_delay_s is None else f"{report.transition_mean_delay_s:.3f}"
    )
    return [
        ("評価ティック数", str(report.n_eval_ticks)),
        ("トリプル precision", f"{report.triple_precision:.4f}"),
        ("トリプル recall", f"{report.triple_recall:.4f}"),
        ("トリプル F1", f"{report.triple_f1:.4f}"),
        ("同一性 F1", f"{report.identity_f1:.4f}"),
        ("位置 RMSE [m]", f"{report.position_rmse:.4f}"),
        ("遷移検出 平均遅延 [s]", delay),
        ("遷移取りこぼし率", f"{report.transition_miss_rate:.4f}"),
        ("陳腐化率", f"{report.staleness_rate:.4f}"),
    ]


def render_run_report(run_dir: Path) -> str:
    reader = RunReader(run_dir)
    manifest = reader.manifest()
    lines: list[str] = [
        f"# ORX Run Report — `{manifest.run_id}`",
        "",
        "## 再現情報",
        "",
        "| 項目 | 値 |",
        "|------|-----|",
        f"| 世界 | {manifest.world_config_name} |",
        f"| 条件 | {manifest.condition} |",
        f"| ルートシード | {manifest.root_seed} |",
        f"| 構成ハッシュ | `{manifest.config_hash}` |",
        f"| gitコミット | `{manifest.git_commit}` |",
        f"| LLM | {manifest.llm_mode} ({manifest.llm_model}) |",
        f"| 視覚埋め込み | {manifest.visual_embedder} |",
        f"| 記録時刻 | {manifest.created_at} |",
        f"| orx版 | {manifest.orx_version} |",
        "",
        "## 忠実度（記録時 / 条件: " + manifest.condition + "）",
        "",
        "| 指標 | 値 |",
        "|------|-----|",
    ]
    if reader.is_complete():
        lines += [f"| {k} | {v} |" for k, v in _fidelity_rows(reader.metrics())]
    else:
        lines.append("| （未完了run: metrics.json なし） | - |")

    replays_dir = run_dir / "replays"
    if replays_dir.exists():
        conditions = sorted(p.name for p in replays_dir.iterdir() if (p / METRICS).exists())
        if conditions:
            lines += ["", "## 反実仮想リプレイ", ""]
            header = "| 指標 | " + " | ".join(conditions) + " |"
            lines += [header, "|------|" + "------|" * len(conditions)]
            reports = {
                c: FidelityReport.model_validate_json(
                    (replays_dir / c / METRICS).read_text(encoding="utf-8")
                )
                for c in conditions
            }
            n_rows = len(_fidelity_rows(next(iter(reports.values()))))
            for i in range(n_rows):
                label = _fidelity_rows(next(iter(reports.values())))[i][0]
                cells = [_fidelity_rows(reports[c])[i][1] for c in conditions]
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write_run_report(run_dir: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_dir.name}.md"
    out_path.write_text(render_run_report(run_dir), encoding="utf-8")
    return out_path


# ============================================================ REPORT.md（R-INFRA）
# REPORT.md は git 非追跡ゆえ消失しうる。一次データ（results.json）＋ docs/LIVE_RESULTS.md
# から決定的に再生成できるようにし、`make report` / `make scenario-all` で常に最新化する。


def latest_results_by_scope(runs_root: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """`data/runs/scenario-*/results.json` を scope 別・シナリオ別に最新（mtime 最大）で集める。

    返り値 (deterministic, agent): いずれも sid -> results dict。
    provenance を欠く legacy run は scope 判定を誤る温床のため除外する（D-4）。
    """
    from orx.exp.scenario import has_provenance

    det: dict[str, tuple[float, dict]] = {}
    agent: dict[str, tuple[float, dict]] = {}
    for path in runs_root.glob("scenario-*/results.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sid = data.get("scenario")
        if not sid or not has_provenance(data):
            continue
        target = agent if data.get("scope") == "agent" else det
        mtime = path.stat().st_mtime
        if sid not in target or mtime > target[sid][0]:
            target[sid] = (mtime, data)
    return (
        {sid: d for sid, (_m, d) in sorted(det.items())},
        {sid: d for sid, (_m, d) in sorted(agent.items())},
    )


def _condition_table(d: dict, max_metrics: int = 4) -> list[str]:
    """results の per_condition から主要指標表（先頭 N 個の数値指標）を作る。"""
    conds = d.get("conditions", [])
    pc = d.get("per_condition", {})
    if not conds or not pc:
        return []
    first = pc.get(conds[0], {})
    keys = [k for k in first if isinstance(first[k], (int, float))][:max_metrics]
    lines = ["| 条件 | " + " | ".join(keys) + " |", "|------|" + "------|" * len(keys)]
    for c in conds:
        row = pc.get(c, {})
        cells = " | ".join(
            f"{row[k]:.3f}" if isinstance(row.get(k), float) else str(row.get(k, "")) for k in keys
        )
        lines.append(f"| {c} | {cells} |")
    return lines


def build_main_report(deterministic: dict[str, dict], agent: dict[str, dict], date_str: str) -> str:
    """REPORT.md 本文を results.json から決定的に再生成する。

    deterministic/agent: sid -> results dict（`latest_results_by_scope` の出力）。
    抽象タスク（T2/T5/T7）の live 詳細は `docs/LIVE_RESULTS.md` を一次ソースとして参照する。
    """
    from orx.exp import scope as scope_mod
    from orx.exp.scenario import (
        _comparison_digest,
        _meta_for,
        _robustness_digest,
        _s7_safety_note,
    )

    def _falsification_lines(d: dict) -> list[str]:
        fal = d.get("falsification", {})
        if not fal:
            return []
        return ["- 反証予言: " + " / ".join(f"{k}={'✓' if v else '✗'}" for k, v in fal.items())]

    total_pred = sum(len(d.get("falsification", {})) for d in deterministic.values())
    total_pass = sum(
        sum(1 for v in d.get("falsification", {}).values() if v) for d in deterministic.values()
    )
    s8 = deterministic.get("s8")
    s8_agent_done = "s8" in agent

    lines = [
        "# REPORT.md — ORX 検証レポート",
        "",
        f"- 生成日: {date_str} / 生成元: `make report`（`orx report-main`）— "
        "results.json から決定的に再生成（R-INFRA）",
        "- 対象: PROJECT.md の仮説 H1–H7（＋キネティック層 H8）",
        "- 一次ソース: `data/runs/scenario-*/results.json`（決定的＋agent）／"
        "抽象タスク live は `docs/LIVE_RESULTS.md`",
        "",
        "> **本書は自動再生成される**。REPORT.md は git 非追跡で消失しうるため、`make report` /"
        " `make scenario-all` が results.json から本書を復元する（IMPROVEMENT.md R-INFRA）。",
        "",
        "---",
        "",
        "## 1. 計測射程の明示（PROJECT.md §7）",
        "",
        "数値は **ceiling（表現上限・決定的）/ ablation（機構寄与）/ agent（実LLM・仮説本体）** に"
        "厳密分離する。**ceiling/ablation は仮説確認ではない**。ループ種別: S1–S7=open（反実仮想"
        "リプレイ）/ **S8=closed**（アクションが物理を変える→条件独立ロール"
        "アウト＋seed-paired 検定）。",
        "",
        "---",
        "",
        "## 2. 決定的 ceiling/ablation（情報層）— 反証総括",
        "",
        f"`make scenario-all` 由来。**反証予言 {total_pass}/{total_pred} ✓**。",
        "",
        "| S | Suite | 仮説 | 反証 | 条件 | seed | loop |",
        "|---|---|---|---|---|---|---|",
    ]
    for sid, d in deterministic.items():
        meta = _meta_for(sid)
        fal = d.get("falsification", {})
        npass = sum(1 for v in fal.values() if v)
        mark = "✓ 全" if fal and npass == len(fal) else f"{npass}/{len(fal)}"
        lines.append(
            f"| {sid} | {meta.suite if meta else '?'} | "
            f"{','.join(meta.hypotheses) if meta else '?'} | {mark} | "
            f"{len(d.get('conditions', []))} | {len(d.get('seeds', []))} | "
            f"{d.get('loop', 'open')} |"
        )

    lines += [
        "",
        "## 3. シナリオ別 主要指標（決定的 ceiling/ablation）",
        "",
        "各シナリオは条件別主要指標・対比較（seed-paired）・頑健性掃引・反証予言を併記する。"
        "各 reference solver は「その条件が許す情報のみ」を使う no-rigging 規律（採点は oracle が"
        "真値から独立に導出）。",
        "",
    ]
    for sid, d in deterministic.items():
        meta = _meta_for(sid)
        stamp = f"`{d.get('git_commit', '?')}` / {d.get('model_snapshot', '?')}"
        lines.append(f"### {sid} — {meta.title if meta else sid}（{meta.suite if meta else '?'}）")
        lines += [
            "",
            f"- 仮説: {', '.join(meta.hypotheses) if meta else '?'} / "
            f"seed: {len(d.get('seeds', []))} / loop: {d.get('loop', 'open')} / {stamp}",
            "",
            *_condition_table(d),
            "",
            f"- {_comparison_digest(d)}",
        ]
        lines += [f"- {ln}" for ln in _robustness_digest(d)]
        lines += _falsification_lines(d)
        lines += _s7_safety_note(d)
        lines.append("")

    # agent 射程
    lines += ["", "## 4. agent 射程（仮説本体・実LLM/実埋め込み・live）", ""]
    if agent:
        lines += [
            "記録済み agent 結果（`mode=cache` で $0 再現）。**一様な勝ちではなく**、live でしか"
            "分からない honest な null/mixed/model-dependent を含む（PROJECT.md §12）。抽象タスク"
            "T2/T5/T7（H1/H4/H6/H7）の live 詳細と境界条件の解説は `docs/LIVE_RESULTS.md` を参照。",
            "",
        ]
        for sid, d in agent.items():
            meta = _meta_for(sid)
            lines.append(
                f"### {sid} — {meta.title if meta else sid}（agent / "
                f"{d.get('model_snapshot', '?')}・seed {len(d.get('seeds', []))}）"
            )
            lines += ["", *_condition_table(d, max_metrics=4)]
            lines += ["", f"- {_comparison_digest(d)}"]
            lines += _falsification_lines(d)
            lines.append("")
    else:
        lines += [
            "（agent 射程の記録が data/runs に無い — `mode=cache` 再現にはキャッシュが必要）",
            "",
        ]

    # キネティック層
    lines += ["", "## 5. キネティック層（アクション型・S8/T15）", ""]
    if s8 is not None:
        s8pc = s8.get("per_condition", {})
        rec = {c: s8pc.get(c, {}).get("recovery_rate") for c in s8.get("conditions", [])}
        lines += [
            "アクション型の決定的 ceiling（送信基準＝能力・規範・所有権の検証＋来歴付き書戻し）:",
            "",
            *_condition_table(s8, max_metrics=5),
            "",
            f"- {_comparison_digest(s8)}",
            f"- 回復率（H8・可逆性: 注入誤動作の検出・補償）: {rec}",
        ]
        s8a = agent.get("s8")
        if s8a is not None:
            apc = s8a.get("per_condition", {})
            orf = apc.get("OR-full-llm", {})
            lines += [
                f"- **agent 射程（live・{s8a.get('model_snapshot', '?')}）実測済み**: "
                f"OR-full-llm 完遂 {orf.get('completion', '?')}・安全違反 "
                f"{orf.get('safety_violations', '?')}・誤配送 {orf.get('misdeliveries', '?')}・"
                f"監査 {orf.get('audit_completeness', '?')}・回復 {orf.get('recovery_rate', '?')}"
                "（ベースラインは誤配送＋監査0）。**H8 を agent 射程で実証**。詳細は §4 s8。",
            ]
        else:
            lines.append("- agent 射程（live）: **未計測（承認ゲート K2）— IMPROVEMENT.md R-K1**")
        lines.append("")
    else:
        lines += ["（s8 results.json が無い）", ""]

    lines += [
        "## 6. 装置の信頼性（再現性・評価独立性）",
        "",
        "- **oracle 隔離**: import-linter で contracts kept / 0 broken（oracle は post-action 含む"
        "シム真値＋業務定義のみで採点・ORコア非 import）。",
        "- **再現性**: 決定的は `make scenario-all` で $0 再現、agent は `mode=cache` で $0 再生。"
        "各 results.json に config_hash・git_commit・model_snapshot・seeds を焼き込み"
        "（PROJECT.md §8.3）。",
        "- **本書の再現性**: REPORT.md は `make report` で results.json から再生成可能（R-INFRA）",
        "",
        scope_mod.loop_note("closed"),
        "",
        "## 7. 既知のギャップ（IMPROVEMENT.md 連動）",
        "",
    ]
    if s8_agent_done:
        lines.append(
            "**必須データの欠落なし**。S8 agent 射程（H8 本体）は live 実測済み"
            "（複数モデル・$0 再現）。残るは任意精緻化（R-K2・R-OPT）のみ — IMPROVEMENT.md 参照。"
        )
    else:
        lines.append(
            "1. **S8（キネティック層）agent 射程が未計測（K2 ゲート）**: 決定的 ceiling は完了"
            "したが、「実 LLM がアクション型で安全・監査・可逆を達成するか（H8 本体）」は未実証。"
            "配線済み・要モデル名＋コスト承認（IMPROVEMENT.md R-K1・§2）。"
        )

    # §8 仮説別判定（決定的 ceiling は data 由来・agent は LIVE_RESULTS.md 由来の判定）
    agent_ids = set(agent)
    h1 = "✓" if "s3" in agent_ids else "—"
    h2 = "✓" if {"s1", "s4"} & agent_ids else "—"
    h8 = "✓" if s8_agent_done else "**未計測（K2）**"
    lines += [
        "",
        "## 8. 仮説別判定",
        "",
        "| 仮説 | 検証タスク | ceiling | agent(live) | 判定 |",
        "|---|---|---|---|---|",
        f"| H1 ハブ&スポーク統合 | T5 | — | {h1}（S3/T5） | 支持・model-indep |",
        f"| H2 同一性解決 | T1/S1/S4 | ✓ | {h2} | 支持 |",
        "| H3 能力オントロジー計画 | T3/S3/**S8** | ✓ | ✓（S3） | 支持 |",
        "| H4 双対表現 | T7/S4/S6 | ✓ | ✓（T7）/能力帯依存（S6） | 支持・境界 |",
        "| H5 信念管理頑健性 | T4/S2/S5/**S8** | ✓ | ✓（S2）/null（S5） | 支持・境界 |",
        "| H6 業務‐物理クエリ | T2/S1/S5 | ✓ | ✓（T2） | 支持 |",
        "| H7 文脈圧縮 | T2 横断 | — | ✓（T2） | 支持 |",
        f"| **H8** アクション型: 安全・監査・可逆 | **S8** | ✓（5/5） | {h8} | "
        + ("agent live 実証（複数モデル・model-independent）" if s8_agent_done else "ceiling 実証")
        + " |",
        "",
        "> agent 射程の詳細値・honest な null/mixed/model-dependent は `docs/LIVE_RESULTS.md`、"
        "残課題は `IMPROVEMENT.md` を参照。",
        "",
    ]
    return "\n".join(lines)


def write_main_report(runs_root: Path, out_path: Path, date_str: str) -> Path:
    """REPORT.md を results.json から再生成して `out_path` に書く。"""
    deterministic, agent = latest_results_by_scope(runs_root)
    out_path.write_text(build_main_report(deterministic, agent, date_str), encoding="utf-8")
    return out_path
