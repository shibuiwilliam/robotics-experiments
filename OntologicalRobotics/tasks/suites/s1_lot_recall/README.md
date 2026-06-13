# S1 ロット回収（T8）— 宣言的仕様

> コードは `src/orx/exp/suites/s1_lot_recall/`（ADR-013）。本ディレクトリは宣言的仕様。

- 試金石: ①越境同一性（主）、③監査（従） / 仮説: H2, H6, H7 / Tier: A
- 世界: `configs/world/s1_lot_recall.yaml`（ロット属性・隔離ゾーン・進行中搬送）
- 実験: `configs/experiments/s1_lot_recall.yaml`
- 条件: OR-full / B0（生ダンプ）/ B1（個別スキーマ・非融合）
- 真値導出: `orx.oracle.scenarios.s1.recall_truth`（シム真値×lotテーブル、ORコア非依存）
- CQ: `tasks/competency_questions/s1_lot_recall/`
- テスト: `tests/scenarios/s1/`
- demo: `uv run orx scenario demo s1` / 実験: `uv run orx scenario run configs/experiments/s1_lot_recall.yaml`

## 失敗予言（反証テストで実行可能化）
- B0 は搬送中・ID不可読個体のロット帰属を解けず列挙recall<1。
- B1 は横断同一性が無く搬送済み個体の現在地が陳腐化（location精度<1）。
- OR-full は越境同一性＋識別子スレッドで完遂率100%。

## 受入基準（SCENARIOS.md §4 逐語）
ノイズ0で列挙F1=1.0かつ完遂率100%。反証テストが green（B0/B1が指定クエリで失敗）。
