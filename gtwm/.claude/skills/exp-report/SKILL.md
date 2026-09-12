---
name: exp-report
description: 完了した実験の runs ディレクトリから docs/results/EXP-xx.md を書き、status を更新する。引数は「EXP-ID runs/EXP-ID/<timestamp>」。
---

引数: $ARGUMENTS

1. 指定 runs ディレクトリの `metrics.json`、`config_resolved.yaml`、`git.txt`、`log.txt` の末尾を読む。
2. `experiments/criteria.yaml` の該当指標と比較し、各指標を 合格/不合格（smoke なら 参考）で判定する。
3. `docs/results/<EXP-ID>.md` を `.claude/rules/experiments.md` のレポート形式で書く：目的 / 設定（config パス、コミット、dirty フラグ）/ 結果表（指標、seed 別、平均±95%区間、目標、判定）/ 図へのリンク / 考察 / 次のアクション。
4. 不合格の指標には、ε の分解（知覚 / プロセス逸脱 / オントロジー欠落）に基づく原因仮説を最大3つ書き、それぞれ「次に試す変更」と「その検証方法」を付ける。閾値は変えない。
5. `docs/status.md` の実験表を更新し、`docs: EXP-xx results` でコミットする。
6. 「概ね良好」などの言い換えで不合格を隠さない。
