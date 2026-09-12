---
name: session-end
description: セッションの成果を検証・記録・コミットして締める。ユーザーが /session-end と打った時だけ使う。
disable-model-invocation: true
---

1. `make lint` と `make test` を実行する。赤なら直す。直せないなら理由を `docs/status.md` の「既知の制約」に書く。
2. `docs/status.md` を更新する：着手順チェックリスト、実験表（EXP・状態・runs パス・判定）、既知の制約（MPS fallback、amd64 イメージ、計画書との差分）。
3. Conventional Commits（feat/fix/exp/docs/chore/test）で1つ以上のコミットを作る。1コミット1関心事。`data/` `runs/` `mlruns/` は含めない。
4. 次の形式で報告して終わる：
   - 変更ファイル一覧（`git diff --stat HEAD~N`）
   - 実行したコマンドと結果（緑/赤）
   - 受入基準の達成表（基準、目標値、実測値、判定）
   - 次のセッションでやるべきこと（1つ）
   - 判断が必要な点
