---
name: adr
description: 設計判断を docs/adr/ に ADR として記録し、関連する CLAUDE.md・rules・criteria を同じコミットで更新する。技術選定・指標定義・合格基準・gt: 名前空間を変える前に必ず使う。
---

引数: $ARGUMENTS（ADR のタイトル）

1. `docs/adr/` の最大番号を調べ、次の番号で `docs/adr/NNNN-<slug>.md` を作る。
2. 見出し：タイトル / 日付 / 状況（何が問題か、制約は何か）/ 検討した選択肢（少なくとも2つ、利点と欠点）/ 決定 / 結果と影響（受入基準、計画書、コスト、リスクへの影響）/ 関連ファイル。
3. 決定に伴って変わる `CLAUDE.md`、`.claude/rules/*.md`、`experiments/criteria.yaml`、`docs/poc_plan.md` の該当箇所を同じコミットで更新する。CLAUDE.md は200行以内を保つ。
4. `docs: ADR NNNN <title>` でコミットし、ADR の要旨を3行で報告する。
