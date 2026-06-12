# 実験定義（configs/experiments/）

全実験はコンフィグ駆動（CLAUDE.md §6）。条件変更のためにコードを書き換えない。

実行: `uv run orx exp run <experiment-config>`

実験定義は 条件（B0/B1/OR-full/アブレーション）× タスクスイート × シード の直積を宣言する。
出力（`reports/`, `data/runs/`）には構成ハッシュ・シード・モデルスナップショット名が自動で焼き込まれる。

書式は C10 実装時（P1〜P2）にここへ追記する。
