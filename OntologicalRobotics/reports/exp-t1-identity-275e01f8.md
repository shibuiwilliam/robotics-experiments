# ORX Experiment Report — `exp-t1-identity-275e01f8`

- タスク: t1 / シード数: 20 / 構成ハッシュ: `275e01f869530d71`

## 条件別サマリ

| 条件 | タスク成功率 | 同一性F1 (平均) |
|------|-------------|------------------|
| OR-full | 0.950 | 0.979 |
| OR-no-identity | 0.000 | 0.000 |

## 対比較（対応のある検定）

### OR-full vs OR-no-identity

- 成功率: 0.950 vs 0.000（差のCI95: [0.850, 1.000]）
- 不一致ペア: OR-fullのみ成功 19 / OR-no-identityのみ成功 0
- McNemar p = 3.81e-06
- Wilcoxon (identity_f1) p = 6.96e-05
