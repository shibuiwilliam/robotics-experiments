---
name: exp-run
description: 実験を smoke または本実行で起動する。引数は「EXP-ID [smoke|full]」。ユーザーが /exp-run と打った時だけ使う。
disable-model-invocation: true
---

引数: $ARGUMENTS（例: `EXP-03 full`。モード省略時は smoke）

1. `experiments/<EXP-ID>/README.md`、`config.yaml`、`experiments/criteria.yaml` の該当行を読む。
2. 必要なデータセット（config の `data.set`）が `data/sim/` にあるか確認する。無ければ生成時間を見積もり、ユーザーの確認を取ってから `gtwm sim gen` を nohup で起動する。
3. smoke: `make exp EXP=<EXP-ID> SMOKE=1` をフォアグラウンドで実行する（5分以内）。
   full: seed 3本を `nohup make exp EXP=<EXP-ID> > runs/<EXP-ID>/launch_<timestamp>.log 2>&1 &` で起動し、完了を待たない。
4. 報告して終わる：起動コマンド、ログのパス、推定所要時間、完了後に実行すべきコマンド（`/exp-report <EXP-ID> runs/<EXP-ID>/<ts>`）。
5. 途中で criteria.yaml や config の閾値・重みを変えない。
