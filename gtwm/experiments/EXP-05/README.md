# EXP-05 乖離注入と検知

- 仮説: H4（物理と記録の乖離を自動検知できる）
- 段階: P1
- 所要（計画目安）: 4週

## 手順（poc_plan.md 6.2 原文）

乖離5型×10件以上を、時間帯と対象を無作為化して2週間に分散して注入。注入台帳は
実験者のみが保持し、検知側には知らせない（盲検）。

## 出力（poc_plan.md 6.2 原文）

型別の検知率、誤報数／日、検知遅延分布、誤報の原因分類。

## 安全（poc_plan.md 6.2 原文）

注入は業務責任者の承認のもと、出荷に影響しない物体で実施し、実験終了時に原状回復。
（本 PoC は全フェーズをシミュレーションで行うため、この安全手順は実サイト展開時の
参照事項として記載するのみ。）

## 合格基準（poc_plan.md 3.1、`experiments/criteria.yaml` H4）

| 指標 | 目標値 |
|---|---|
| detection_rate | >= 0.90 |
| false_alarms_per_day | <= 2件/日 |
| detection_latency_median_s | <= 60秒 |

## smoke での簡略化（`src/gtwm/eval/experiments/exp05.py` 参照）

- 2週間の分散注入 → `n_episodes` 本（既定2）の30秒エピソードに
  `configs/realism/p1.yaml` の注入設定（各型3件/エピソード）をまとめて入れる。
- `false_alarms_per_day` は評価した合計秒数から日数換算するため、smoke の
  数十秒では値が極端になる（本実行の実データでの評価が必須）。
- 盲検境界：`eval/scoring.py` だけが `data/injections/` を読む
  （`tests/unit/test_blindness.py` で検査）。

## 本実行に必要なデータ・時間

2週間分のP1運転ログ（`data/sim/p1_eval` 相当を拡張し、乖離5型×10件以上、合計80件目標）。
`gtwm sim gen` の実測生成速度（5分エピソード 約75秒/本）から按分すると、2週間
（14エピソード相当）で約18分。
