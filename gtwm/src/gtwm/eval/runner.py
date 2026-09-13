"""実験ランナー（C14）：config → 実行 → `runs/EXP-xx/<timestamp>/` への結果書き出し
（`.claude/rules/experiments.md`）。

`experiments/EXP-xx/run.py` はここの `run()` を呼ぶだけの薄いファイルにすること。
実際の測定ロジックは `measure_fn`（`src/gtwm/eval/experiments/*.py`）に置く。
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omegaconf import DictConfig, OmegaConf

from gtwm.utils.paths import repo_root
from gtwm.utils.seed import seed_everything

MeasureFn = Callable[[DictConfig, int], dict[str, Any]]


@dataclass
class RunResult:
    exp_id: str
    run_dir: Path
    metrics: dict[str, Any]


def _git_info() -> str:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root(), text=True
        ).strip()
        dirty = subprocess.run(["git", "diff", "--quiet"], cwd=repo_root()).returncode != 0
        return f"commit={commit}\ndirty={dirty}\n"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git 情報取得失敗: {exc}\n"


def _aggregate(per_seed_metrics: list[dict[str, Any]]) -> dict[str, float]:
    """数値指標について seed 間の平均を取る（本実行の3seed平均に相当。
    smoke（seed1本）でも同じロジックで動く＝平均はその1値そのもの）。"""
    if not per_seed_metrics:
        return {}
    numeric_keys = [k for k, v in per_seed_metrics[0].items() if isinstance(v, int | float)]
    aggregated: dict[str, float] = {}
    for k in numeric_keys:
        values = [float(m[k]) for m in per_seed_metrics if isinstance(m.get(k), int | float)]
        if values:
            aggregated[k] = sum(values) / len(values)
    return aggregated


def _write_mlflow(
    exp_id: str, run_name: str, seeds: list[int], aggregated: dict[str, float]
) -> None:
    try:
        import mlflow
    except ImportError:  # pragma: no cover - devの最小構成では起きない
        return
    # 新しめの mlflow はファイルストア（`mlruns/`）を既定で拒否し sqlite への移行を促す
    # （`wm/train.py` と同じワークアラウンド）。
    import os

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri(str(repo_root() / "mlruns"))
    mlflow.set_experiment(exp_id)
    with mlflow.start_run(run_name=run_name):
        mlflow.log_param("seeds", seeds)
        for k, v in aggregated.items():
            mlflow.log_metric(k, v)


def run(
    exp_id: str,
    config: DictConfig,
    measure_fn: MeasureFn,
    run_timestamp: str,
) -> RunResult:
    """`measure_fn(config, seed)` を `config.seeds` ぶん実行し、
    `runs/<exp_id>/<run_timestamp>/{metrics.json,report.md,config_resolved.yaml,
    git.txt,log.txt}` を書き出す。

    smoke は `config.smoke=true` かつ `config.seeds=[0]`（1本）、本実行は
    `seeds=[0,1,2]`（3本）を想定（`.claude/rules/experiments.md`「実行規則」）。
    `run_timestamp` は呼び出し側（CLI）が生成して渡す（テストで固定できるように
    引数化し、このモジュール自身は時刻を生成しない）。
    """
    smoke = bool(config.get("smoke", False))
    if not smoke and config.get("full_run") is not None:
        # `full_run:` はこれまで各 experiments/EXP-xx/config.yaml に転記されているだけの
        # ドキュメントで、smoke=false でも自動適用されなかった（本実行が smoke と同じ
        # n_episodes/duration_s/probe_config を使ってしまうバグ）。ここで一度だけ、
        # トップレベル設定へ full_run の値を上書きマージする（Phase B Stage 1 で発見・修正）。
        merged = OmegaConf.merge(config, config.full_run)
        assert isinstance(merged, DictConfig)
        config = merged
    seeds = [int(s) for s in config.get("seeds", [0])]

    run_dir = repo_root() / "runs" / exp_id / run_timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    log_lines = [f"exp_id={exp_id} smoke={smoke} seeds={seeds}"]
    per_seed_metrics: list[dict[str, Any]] = []
    start = time.time()
    for seed in seeds:
        seed_everything(seed)
        seed_metrics = measure_fn(config, seed)
        per_seed_metrics.append(seed_metrics)
        log_lines.append(f"seed={seed} 完了: {seed_metrics}")
    duration_s = time.time() - start
    log_lines.append(f"総所要時間: {duration_s:.2f}s")

    aggregated = _aggregate(per_seed_metrics)
    metrics_payload: dict[str, Any] = {
        "exp_id": exp_id,
        "smoke": smoke,
        "seeds": seeds,
        "per_seed": per_seed_metrics,
        "aggregated": aggregated,
        "duration_s": duration_s,
    }

    (run_dir / "metrics.json").write_text(
        json.dumps(metrics_payload, indent=2, default=str, ensure_ascii=False)
    )
    (run_dir / "config_resolved.yaml").write_text(OmegaConf.to_yaml(config))
    (run_dir / "git.txt").write_text(_git_info())
    (run_dir / "log.txt").write_text("\n".join(log_lines) + "\n")

    _write_mlflow(exp_id, run_timestamp, seeds, aggregated)

    return RunResult(exp_id=exp_id, run_dir=run_dir, metrics=metrics_payload)


def judge_criteria(aggregated: dict[str, float], criteria: dict[str, dict[str, Any]]) -> str:
    """`aggregated` を `criteria`（`experiments/criteria.yaml` の該当仮説の `metrics`）と
    比較し、"合格"/"不合格" を返す（本実行のみで使うこと。smoke には適用しない、
    `.claude/rules/experiments.md`「判定は...smoke なら参考」）。

    `criteria` の各エントリは `{"target": float, "op": "<="|">="|"=="}` の形。
    `op` が `report_only` の指標（例：N1のコスト試算）は判定対象から除外する。
    """
    ops: dict[str, Callable[[float, float], bool]] = {
        "<=": lambda v, t: v <= t,
        ">=": lambda v, t: v >= t,
        "==": lambda v, t: v == t,
    }
    for name, spec in criteria.items():
        op = str(spec.get("op"))
        if op == "report_only" or spec.get("target") is None:
            continue
        if name not in aggregated:
            return "不合格"  # 指標が無い＝基準を満たせていない
        if not ops[op](aggregated[name], float(spec["target"])):
            return "不合格"
    return "合格"


def generate_report(
    exp_id: str,
    purpose: str,
    config_path: str,
    result: RunResult,
    criteria: dict[str, dict[str, Any]],
    full_run_note: str,
) -> str:
    """`.claude/rules/experiments.md`「レポート」節の見出しに沿った report.md を生成する。

    smoke（`result.metrics["smoke"]`）の場合は判定を常に「参考（smoke）」にする
    （smoke の結果で合否を書かない、docs/prompts.md セッション06）。
    """
    aggregated = result.metrics["aggregated"]
    smoke = result.metrics["smoke"]
    verdict = "参考（smoke）" if smoke else judge_criteria(aggregated, criteria)

    lines = [
        f"# {exp_id} 結果",
        "",
        "## 目的",
        purpose,
        "",
        "## 設定",
        f"- config: `{config_path}`",
        "- git: 実行時コミット・dirty フラグは `git.txt` を参照",
        f"- seeds: {result.metrics['seeds']}",
        f"- 所要時間: {result.metrics['duration_s']:.2f}s",
        "",
        "## 結果表",
        "",
        "| 指標 | 平均値 | 合格基準 |",
        "|---|---|---|",
    ]
    for name, spec in criteria.items():
        value = aggregated.get(name)
        target = spec.get("target")
        op = spec.get("op", "")
        value_str = f"{value:.4f}" if isinstance(value, int | float) else "N/A"
        target_str = "報告のみ" if op == "report_only" else f"{op} {target}"
        lines.append(f"| {name} | {value_str} | {target_str} |")

    lines += [
        "",
        "## 判定",
        f"**{verdict}**",
        "",
        "## 考察",
    ]
    if smoke:
        lines.append(full_run_note)
    else:
        lines.append("（本実行の考察はこの report.md を手動で更新して追記する）")

    lines += ["", "## 次のアクション", "（未定：手動で追記する）", ""]
    report = "\n".join(lines)
    (result.run_dir / "report.md").write_text(report)
    return report


__all__ = ["RunResult", "run", "judge_criteria", "generate_report"]
