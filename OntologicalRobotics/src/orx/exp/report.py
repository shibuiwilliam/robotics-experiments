"""Markdownレポート生成（`orx report`）。

run の manifest + metrics（＋存在すれば反実仮想リプレイ各条件）から
再現情報入りのレポートを reports/ に書き出す。
"""

from __future__ import annotations

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
