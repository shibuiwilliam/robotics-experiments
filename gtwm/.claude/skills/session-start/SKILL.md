---
name: session-start
description: セッション開始時に進捗と git 状態を読み、今日のタスクを1つ提案する。ユーザーが /session-start と打った時に使う。
---

1. `docs/status.md`、`git log --oneline -10`、`git status --short` を読む。
2. 未コミットの変更があれば内容を要約し、コミットするか破棄するかを聞いて止まる。
3. 着手順チェックリストと実験表から、次にやるべきタスクを1つ選び、理由（依存関係、未達の受入基準）を2行で示す。
4. そのタスクの受入基準を `docs/poc_plan.md` と `experiments/criteria.yaml` から引用する。
5. 実装は始めない。ユーザーの承認を待つ。
