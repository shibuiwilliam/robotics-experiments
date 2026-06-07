# PSL-Bench 検証レポート

> **実行日**: 2026-06-07
> **モード**: ONLINE（Claude Agent SDK + pseudo-cloud HTTP + VLA hash fallback）+ OFFLINE（メトリクス + 5シード統計 + Phase 4 ストレステスト）
> **シード**: 42（オンライン）/ [42, 123, 456, 789, 1024]（オフライン5シード）
> **Git commit**: `b5b7b5c`
> **Platform**: macOS Darwin 25.5.0 (Apple Silicon)

---

## 1. エグゼクティブサマリ

7シナリオ全てが **ビジネス成功 + PSL成功** の両レイヤーで PASS。
全シナリオがオンラインで正常完走（フォールバックゼロ、HTTP 404 ゼロ）。
5シード×7シナリオ=35ランのマルチシード統計評価でも全 PASS。
Phase 4 ストレステスト（スキーマファジング・クロックスキュー・接地汎化）を実行し、PSL の限界点を定量的に特定した。

| Scenario | Business | PSL | Wall (online) | Cost | Turns | Tools | 404s |
|----------|----------|-----|---------------|------|-------|-------|------|
| S1 Mixed Fleet Pick | PASS | PASS | 61.7s | $0.192 | 10 | 9 | 0 |
| S2 Line Changeover | PASS | PASS | 39.7s | $0.135 | 5 | 4 | 0 |
| S3 Lab Custody | PASS | PASS | 94.5s | $0.330 | 18 | 17 | 0 |
| S4 Field Inspection | PASS | PASS | 34.2s | $0.131 | 6 | 5 | 0 |
| S5 Pharma Logistics | PASS | PASS | 55.1s | $0.136 | 5 | 4 | 0 |
| S6 E-Waste Disassembly | PASS | PASS | 31.3s | $0.138 | 6 | 5 | 0 |
| S7 Degraded Ops | PASS | PASS | 58.0s | $0.149 | 6 | 5 | 0 |
| **合計** | **7/7** | **7/7** | **374.4s** | **$1.211** | — | **49** | **0** |

---

## 2. シナリオ別ブレークポイント結果

### S1: Mixed Fleet Pick

異種ロボット間（Panda m/z-up ↔ AMR mm/y-up）の R2R ハンドオフ精度。

| Breakpoint | Value | Threshold | Passed |
|------------|-------|-----------|--------|
| calibration_nll | -0.745 | < 5.0 | PASS |
| r2r_handoff_error | 0.0 | < 0.05 m | PASS |
| negotiation_feasible | 1.0 | > 0.5 | PASS |

- Joint RMSE: 0.0、Commutativity: 0.0、Baseline B1 RMSE: 576.3
- Negotiation notes: 4（frame, unit, control_mode, joint count の差異検出）
- エージェント: resolve_document ×2 → 200 OK（Bin C, QA_TRAY 即時解決）

### S2: Line Changeover

マルチホップ翻訳の可換性と安全ゲートによる不可能操作拒否。

| Breakpoint | Value | Threshold | Passed |
|------------|-------|-----------|--------|
| commutativity_divergence | 0.0 | < 1e-6 | PASS |
| safety_gate_rejection | 0.0 | < 0.5 | PASS |

- Gate rejected impossible: 1.0、False reject: 0.0

### S3: Lab Custody

プロヴェナンスチェーン完全性。

| Breakpoint | Value | Threshold | Passed |
|------------|-------|-----------|--------|
| provenance_chain_intact | 0.99 | > 0.5 | PASS |
| provenance_ablation_breaks | 1.0 | > 0.5 | PASS |

- エージェント: 17 MCP 呼び出し（query_world_model ×7 でカストディチェーン全段階確認）
- 最多ツール呼び出し・最高コストのシナリオ（$0.330, 18 turns）

### S4: Field Inspection + Drone 空中検査

LOD マルチ解像度整合 + **Drone R2R ハンドオフ（N+N 実証）**。

| Breakpoint | Value | Threshold | Passed |
|------------|-------|-----------|--------|
| lod_consistency | 15.0 | > 1.0 | PASS |
| fusion_fidelity | 0.0 | < 0.05 | PASS |
| bidirectional_anchoring | 1.0 | > 0.5 | PASS |
| **drone_r2r_handoff** | **0.0** | **< 1e-6** | **PASS** |

- Drone round-trip error: 0.0（ENU ↔ world 完全可逆）
- Drone-Panda negotiation: feasible（3 translation notes）

### S5: Pharma Logistics

ニューロ・シンボリック束縛 + プロヴェナンス汚染検出。

| Breakpoint | Value | Threshold | Passed |
|------------|-------|-----------|--------|
| neuro_symbolic_false_accept | 0.0 | < 0.05 | PASS |
| provenance_poisoning_detected | 1.0 | > 0.5 | PASS |

### S6: E-Waste Disassembly

オープンワールド・アフォーダンス接地。

| Breakpoint | Value | Threshold | Passed |
|------------|-------|-----------|--------|
| embedding_generalization_advantage | 0.84 | > 0.1 | PASS |

- Embedding 84% vs symbol-only 0%

### S7: Degraded Ops

クロックスキュー下の因果整合性。

| Breakpoint | Value | Threshold | Passed |
|------------|-------|-----------|--------|
| causal_ordering_violations | 0.0 | < 0.5 | PASS |
| graceful_degradation | 1.0 | > 0.5 | PASS |

- LOD staleness: 2.0s、全オンライン完走（前々回はタイムアウト→フォールバック）

---

## 3. 仮説評価

| H | Statement | Evidence (5-seed) | Assessment |
|---|-----------|-------------------|------------|
| H1 | PSL が異種性増大に耐性 | B1=576.319±0.003 vs PSL=1.1e-5±2e-6 | **支持** |
| H2 | 忠実度契約が較正 | NLL=-0.532±0.191 (CI [-0.769, -0.294]) | **支持** |
| H3 | 可換性が閾値内 | divergence=0.0±0.0 全シード | **支持** |
| H4 | N+N スケーリング | Drone 追加で既存コード変更ゼロ、S4 handoff=0.0 | **支持** |
| H5 | 埋め込み優位 | 0.789±0.030 (CI [0.752, 0.827]) vs 0.0 | **支持** |

---

## 4. 用量反応曲線

| Dose | Unit Scale | Frame Rot | Noise σ | RMSE | Info Loss |
|------|-----------|-----------|---------|------|-----------|
| 0 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| 1 | 1000.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| 2 | 1.0 | π/4 | 0.0 | 0.0 | 0.0 |
| 3 | 1.0 | 0.0 | 0.01 | 1.08e-2 | 0.438 |
| 4 | 1000.0 | π/4 | 0.01 | 7.28e-6 | 2.96e-4 |
| 5 | 1000.0 | π/2 | 0.05 | 3.79e-5 | 1.54e-3 |

単位変換・フレーム回転は完全可逆。**ノイズのみが不可逆的劣化源**。

---

## 5. エージェント行動分析

### MCP ツール利用（オンライン）

| Tool | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Total |
|------|----|----|----|----|----|----|-----|-------|
| resolve_document | 2 | 1 | 3 | 1 | 1 | 0 | 2 | 10 |
| query_world_model | 1 | 1 | 7 | 1 | 1 | 1 | 1 | 13 |
| subscribe_affordances | 1 | 0 | 2 | 1 | 1 | 1 | 0 | 6 |
| command_robot_semantic | 4 | 1 | 4 | 1 | 0 | 2 | 1 | 13 |
| ToolSearch | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 7 |
| **Total** | **9** | **4** | **17** | **5** | **4** | **5** | **5** | **49** |

- HTTP 404: **0件**（全シナリオ）
- フォールバック: **0件**
- S3 がツール最多（17 calls, $0.330）— カストディチェーン各段階で WorldModel を逐次確認

---

## 6. N+N スケーリング

| Adapter | Type | Frame | Unit |
|---------|------|-------|------|
| PandaAdapter (7-DOF) | Robot | z_up | SI |
| AMRAdapter (mobile) | Robot | y_up | mm, deg |
| DroneAdapter (6-DOF) | Robot | ENU | SI |
| ClaudeAgentAdapter | Agent | z_up | dimensionless |
| CloudDataAdapter | Cloud | world | m |

5 adapters → N+N = 10 paths vs N×N = 20 paths (50% reduction)。
Drone 追加で PandaAdapter/AMRAdapter **変更ゼロ**。

---

## 7. Phase 4 ストレステスト

### 7.1 スキーマファジング（50ランダム変換）

| Metric | Value |
|--------|-------|
| Gate 通過率 | 42/50 (84%) |
| 最大 RMSE | 7.546 |
| 最大情報損失 | 1.0 |

- 単位スケール 0.01〜10000、フレーム回転 0〜2π、ノイズ 0〜0.1 をランダム組合せ
- **ノイズなし変換は全て RMSE=0**（PSL の単位/フレーム変換は完全可逆）
- ゲート拒否 8件は全て極端なノイズ + スケール変換の組合せ

### 7.2 クロックスキュー注入

| Skew (s) | Violations | Rejections | Status |
|----------|------------|------------|--------|
| 0.000 | 0 | 0 | OK |
| 0.001 | 20 | 20 | REJECTED |
| 0.010 | 20 | 20 | REJECTED |
| 0.050 | 20 | 20 | REJECTED |
| 0.100 | 20 | 20 | REJECTED |
| 0.500 | 20 | 20 | REJECTED |
| 1.000 | 20 | 20 | REJECTED |
| 5.000 | 20 | 20 | REJECTED |

- **ゲート感度**: 1ms の負方向スキューでも因果違反を検出・拒否
- uncertainty slack が skew の 30% に設定されており、skew > slack で全遷移が違反

### 7.3 接地汎化（20ホールドアウト物体）

| Metric | Value |
|--------|-------|
| Embedding coverage | 100% (20/20) |
| Affordance coverage | 100% (20/20) |
| Material diversity | 5 classes |
| Mean nearest cosine | 0.068 |
| Max nearest cosine | 0.164 |

- 20個の未知物体全てにエンベディングとアフォーダンス予測を生成
- 既知物体との平均コサイン類似度 0.068 — **十分に区別可能**
- 5種のマテリアルクラス（pcb, metal, plastic, glass, composite）に分類

---

## 8. マルチシード統計評価（5シード）

全7シナリオ × 5シード = 35ラン、**全 PASS**。

| Scenario | Key Metric | Mean ± Std |
|----------|-----------|------------|
| S1 | baseline_b1_rmse | 576.319 ± 0.003 |
| S1 | calibration_nll | -0.532 ± 0.191 |
| S1 | defect_confidence | 0.611 ± 0.104 |
| S2 | gate_rejected_impossible | 1.0 ± 0.0 |
| S3 | chain_intact | 1.0 ± 0.0 |
| S4 | drone_negotiation_notes | 3.0 ± 0.0 |
| S5 | ns_detection_rate | 1.0 ± 0.0 |
| S6 | advantage | 0.789 ± 0.030 |
| S7 | degradation_monotonic | 1.0 ± 0.0 |

決定論的メトリクスはシード間ばらつきゼロ。確率的メトリクス（NLL, confidence, embedding rate）は適度な変動。

---

## 9. 物理精度

全シナリオで EE sensor vs ground truth = **0.0 m**、WM object vs ground truth = **0.0 m**。
全シナリオで agent_fallback = false、agent_404_count = 0。

---

## 10. テストスイート

| Category | Count | Status |
|----------|-------|--------|
| Unit | 160 | PASS |
| Oracle | 50 | PASS |
| Metamorphic | 24 | PASS |
| Scenario | 40 | PASS |
| Stress (fuzz/skew/grounding) | 14 | PASS |
| CLIP integration | 4 | SKIPPED |
| **Total** | **262 passed, 4 skipped** | **ALL PASS** |

---

## 11. 再現情報

| Item | Value |
|------|-------|
| Git commit | `b5b7b5c` |
| Python | 3.12.9 |
| MuJoCo | 3.9.0 |
| Pydantic | 2.13.4 |
| pint | 0.25.3 |
| NumPy | 2.4.6 |
| SciPy | 1.17.1 |
| pytransform3d | 3.15.0 |
| hypothesis | 6.155.1 |
| VLA mode | hash_fallback |
| Agent model | claude-sonnet-4-6 |
| Agent temperature | 0.0 |
| Run manifests | `experiments/runs/` |
| Multi-seed report | `experiments/runs/multi_seed_report.json` |
| Stress report | `experiments/runs/stress_report.json` |

---

## 12. 結論

PSL-Bench は PROJECT.md の5つの成功基準全てを満たす（5シード統計評価 + Phase 4 ストレステストで確認）:

1. **H1**: B1 RMSE=576.3 vs PSL=0.0 — PSL は異種性増大に耐性を示す
2. **H2**: calibration NLL=-0.532±0.191 — 不確実性が較正されている
3. **H3**: commutativity=0.0 全シード — 完全可換
4. **H4**: Drone 追加で既存コード変更ゼロ、S4 handoff=0.0 — N+N
5. **H5**: embedding 0.789±0.030 vs symbol 0.0 — 統計的に有意

Phase 4 ストレステストにより限界点も特定:
- 単位/フレーム変換は**完全可逆**（ノイズなしでRMSE=0）
- ノイズが唯一の不可逆劣化源（max RMSE 7.55 at extreme noise）
- 安全ゲートは**1ms の因果違反を検出可能**
- 20個の未知物体に**100% のエンベディング・アフォーダンス生成**

**PSL は「検証可能にスムーズ」であると主張できる。**
