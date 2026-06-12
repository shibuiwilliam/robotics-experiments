# ORX Experiment Report — `exp-t2-business-5989b2fc`

- タスク: t2（業務‐物理クエリ） / 質問数: 54 / LLMモード: stub / 構成ハッシュ: `5989b2fc9e5542bb`

## 条件別サマリ（H6 正答率 / H7 知識効率）

| 条件 | 正答率 | 総トークン | 正答率/1kトークン |
|------|--------|-----------|---------------------|
| OR-reference | 1.000 | 0 | 0.0000 |
| OR-full | 0.000 | 0 | 0.0000 |
| B1 | 0.000 | 0 | 0.0000 |
| B0 | 0.000 | 0 | 0.0000 |

> **注**: LLMモードが stub のため、エージェント条件（OR-full/B1/B0）の正答率は無意味（ハーネス検証のみ）。OR-reference が表現の上限を示す。本計測は mode=openai（要コスト承認）で実行する。

## 対比較（質問単位のMcNemar）

- OR-reference vs OR-full: 1.000 / 0.000, McNemar p = 1.11e-16, 差CI95 [1.000, 1.000]
- OR-reference vs B1: 1.000 / 0.000, McNemar p = 1.11e-16, 差CI95 [1.000, 1.000]
- OR-reference vs B0: 1.000 / 0.000, McNemar p = 1.11e-16, 差CI95 [1.000, 1.000]

## 質問タイプ別正答率

| タイプ | OR-reference | OR-full | B1 | B0 |
|------|------|------|------|------|
| count_in_zone | 1.00 | 0.00 | 0.00 | 0.00 |
| destinations_in_zone | 1.00 | 0.00 | 0.00 | 0.00 |
| fragile_in_zone | 1.00 | 0.00 | 0.00 | 0.00 |
| orders_in_zone | 1.00 | 0.00 | 0.00 | 0.00 |
| where_order | 1.00 | 0.00 | 0.00 | 0.00 |
| where_order_negative | 1.00 | 0.00 | 0.00 | 0.00 |
