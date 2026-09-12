---
paths:
  - "experiments/**"
  - "src/gtwm/eval/**"
  - "docs/results/**"
---

# 実験と評価（C14、EXP-01〜11）

## 構成
- `experiments/EXP-xx/README.md`：仮説 ID、指標、合格基準、条件、手順を計画書 6.2 から転記（要約ではなく同じ数値）。
- `experiments/EXP-xx/config.yaml`：Hydra 設定。`smoke: true` で5分以内に終わる縮小版に切り替わる。
- `experiments/EXP-xx/run.py`：`gtwm.eval.runner.run(config)` を呼ぶだけの薄いファイル。ロジックは `eval/` に置く。
- `experiments/criteria.yaml`：全実験の合格基準の唯一の置き場。コード内に閾値を書かない。基準の変更は ADR 必須。
- 出力は `runs/EXP-xx/<timestamp>/`：`metrics.json`（指標、seed 毎の値、平均、95%区間）、`report.md`（自動生成）、`config_resolved.yaml`、`git.txt`（コミットハッシュと dirty フラグ）、`log.txt`。

## 実行規則
- 本実行は seed 3本（`seeds: [0, 1, 2]`）。smoke は seed 1本。
- 学習・評価の分割は時系列順（エピソード ID の後半を評価）。同一エピソードを学習と評価の両方に入れない。
- 比較実験（EXP-03 のアブレーション等）は同一入力に対する対応ありの比較にし、`eval/stats.py` の `paired_bootstrap_ci()` を使う。割合は `wilson_interval()`。
- 10分を超える実行は `nohup` でバックグラウンド起動し、`log.txt` を tail で確認する。
- MLflow のトラッキング URI は `mlruns/`（ローカル）。実験名は `EXP-xx`、ラン名は `<timestamp>-seed<k>`。

## 盲検
- 注入台帳（`data/injections/`）を読んでよいのは `gtwm.eval.scoring` だけ。`grounding/` と `wm/` から `data/injections` や `gtwm.sim.wms_mock.injections` を import したら `tests/unit/test_blindness.py` が失敗する。
- 注入の seed は実験 seed と別系統（`injection_seed`）にする。

## 指標
- 実装は `eval/metrics.py`。関数名と定義は付録 A に合わせる：`fact_distance_d()`（重み付き不一致率、位置は隣接ゾーンを 0.5）、`epsilon_h()`、`grounding_prf()`、`id_switch_rate()`、`idf1()`、`effective_horizon()`（τ=0.2）、`detection_rate()`、`false_alarms_per_day()`、`detection_latency()`、`ece()`、`reconstruction_ssim()`、`reid_top1()`。
- 全指標に docstring で数式と単位を書き、`tests/unit/test_metrics.py` に手計算できる小さな例を置く。
- KPI（待ち行列長、処理時間、動線長）は信念 KG のスナップショットから SPARQL（`kg/queries/kpi_*.rq`）で集計する。

## レポート（`docs/results/EXP-xx.md`）
- 見出し：目的 / 設定（config パス、コミット）/ 結果表（指標、seed 別、平均±95%区間、合格基準、判定）/ 図（`runs/.../fig_*.png` へのリンク）/ 考察（未達なら ε の分解に基づく原因）/ 次のアクション。
- 判定は「合格 / 不合格 / 参考（smoke）」の三値。不合格を「概ね良好」などの言い換えで書かない。
- 実験を回すたびに `docs/status.md` の実験表を更新する。
