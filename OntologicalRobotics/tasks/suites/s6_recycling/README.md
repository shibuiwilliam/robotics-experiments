# S6 リサイクル選別（T13）— 宣言的仕様

> コードは `src/orx/exp/suites/s6_recycling/`（ADR-013）。authoritative=SCENARIOS.md。

- 試金石: 双対表現（記号×ベクトル） / 仮説: H4 / Tier: A
- 世界: `configs/world/s6_recycling.yaml`（ID無し物体・規制規則・非対称コスト行列）
- 実験: `configs/experiments/s6_recycling.yaml`
- 条件: OR-full（接地×規制×委譲）/ OR-vec（接地のみ・規制無し）/ OR-sym（記号のみ・接地不能）
- 真値導出: `orx.oracle.scenarios.s6`（真クラス×規制規則で正レーン・コスト）
- X4 視覚接地: `grounding.ground`（プロトタイプ最近傍, ADR-017）/ X6 非対称コスト: `scorer`
- 語彙: orx-biz:RegulatedClass/disposalRoute/routedTo, orx-st:DisposalLane（+SHACL+CQ）
- demo: `uv run orx scenario demo s6` / 実験: `uv run orx scenario run configs/experiments/s6_recycling.yaml`

## 失敗予言（反証テスト）
- OR-sym は記号ID無しで接地不能 → 全件委譲（スループット崩壊）。
- OR-vec は規制推論が無く誤レーン（電池の高コスト誤り）。
- OR-full は接地＋規制＋確信度委譲でコスト最小・高コスト誤り0。

## 受入基準（SCENARIOS.md §4 逐語）
コスト加重スコアで OR-full が両アブレーションを有意に上回る。較正曲線の報告。
