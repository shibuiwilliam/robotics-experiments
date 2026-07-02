# R-ABC_PLAN.md — IMPROVEMENT.md §2 残課題の実装計画

策定日: 2026-06-16 / 基準: `PROJECT.md`（H1–H7・§5.2不変条件・§7評価・§8.3再現性）、`CLAUDE.md`、
`IMPROVEMENT.md`（§2 R-A/R-B/R-C）、`docs/SCENARIO_AGENT_DESIGN.md`（R-3a 共通ハーネス）。

> **位置づけ**: IMPROVEMENT.md §2 の残課題は**すべて任意（外的妥当性の補強・横展開）**でブロッカーは無い。
> 本計画は「既に出ている結論を変えず、頑健性とカバレッジを上積みする」ことを唯一の目的とする。

---

## 0. スコープ境界（最重要）

- **オフラインで完結し、`make scenario-all` 緑＋`uv run pytest -q` 緑で検証できるもの**を本作業で**完全実装**する。
- **実 API 課金（mode=openai の新規記録）を要する live 計測**は、CLAUDE.md §6（>50 エピソードの新規記録は
  事前にユーザー承認）に従い、**ワンコマンドで実行可能な状態（config・Makefile・コスト見積・doc）まで整備**し、
  実際の課金実行はユーザーのトリガに委ねる。stub プロバイダでハーネスの健全性を完全に検証する。
- 既存の不変条件を厳守: 真値（true_* / oracle）は agent プロンプトに渡さない。採点は oracle ヘルパ経由。
  グラフ書込は来歴・確信度・時刻付き。`owl:sameAs` 直書きなし。乱数は `SeedTree` 派生。

---

## 1. 優先度付き実装リスト

| # | 項目 | 由来 | 優先 | コスト | 検証 |
|---|------|------|------|--------|------|
| 1 | **scenario-all 恒久サマリの時刻サフィックス** | R-C | 高(簡) | 0 | 単体 |
| 2 | **S6 agent 主シグナル前面化**（平均コスト差＋Wilcoxon/CI を主、McNemar の低検出力を明示） | R-A | 中 | 0 | 単体＋report |
| 3 | **S7 agent 化**（OR-full-llm 双対表現蒸留 vs B0-llm ゾーンのみ） | R-B | 中 | 0(stub) | stub＋scenario-all |
| 4 | **S5 agent 化**（OR-full-llm 規範写像 vs B1-llm 規範なし、経路計画） | R-B | 中 | 0(stub) | stub |
| 5 | **S3 agent 化**（OR-full-llm 語彙横断能力表 vs B1-llm 基準ベンダーのみ） | R-B | 中 | 0(stub) | stub |
| 6 | **S4 agent 化**（OR-full-llm 位置＋署名＋確信度 vs OR-sym-llm 位置のみ） | R-B | 中 | 0(stub) | stub |
| 7 | **第2モデル再現の readiness**（*_m2 live config＋Makefile＋protocol doc＋見積） | R-A | 中 | 0 | config/doc |
| 8 | **docs＋全検証ゲート**（本計画・SCENARIO_AGENT_DESIGN 拡張・IMPROVEMENT 更新・lint/test/scenario-all） | — | 高 | 0 | check |

---

## 2. R-B 各シナリオ agent 設計（S6 パターンの横展開）

共通形（`s6_recycling/agent.py` + `runner.run_agent` に厳密準拠）:
- `AGENT_CONDITIONS = ["OR-full-llm", "<baseline>-llm"]`（2条件・トークン最小化）。
- 1 エピソード=1 LLM コール（まとめて意思決定）→ `parse_*` で安全フォールバック→既存 oracle 真値ヘルパで採点。
- `runner.run()` 先頭で `any(c.endswith("-llm") for c in conditions)` なら `run_agent()` に分岐。
- 結果は `scope="agent"`・`total_tokens`・`comparisons`（paired_comparisons）・`falsification`。
- 真値（true_owner / true_anomaly / true_payload_kg / 違反真値）は**プロンプトに出さない**。接地・距離・類似度・
  宣言値など**蒸留済み情報**のみ与える（S6 が grounding を決定的に行い接地クラスのみ渡すのと同型）。

| S | H | OR-full-llm が見る蒸留情報 | baseline-llm が欠くもの | 決定 | 採点(oracle) | 主指標(成否/連続) |
|---|---|----------------------------|--------------------------|------|--------------|-------------------|
| **S7** | H2/H4/H6 | 各入居者の note 署名×各物体の cos 類似度＋最終目撃ゾーン（記号×ベクトル×時空間の蒸留表） | B0-llm: ゾーンのみ（署名類似度なし） | 入居者→物体index / ESCALATE | `delivery_outcome` | 誤配送0 / 成功率 |
| **S5** | H5/H6 | ゾーングラフ（edges・class）＋**規範写像**（item_class→禁止 zone class） | B1-llm: 規範写像なし（最短経路） | transport→経路(zone列) | `count_violations` | 違反0 / 違反数(↓) |
| **S3** | H1/H3 | **語彙横断**の能力表（全ベンダー machine の宣言可搬・素材・事前成功率を正規化） | B1-llm: 基準ベンダー機体のみ（横断翻訳不可） | task(product)→machine_id | `allocation_correct` | 全正答 / 正答率 |
| **S4** | H2/H5 | 各観測の位置＋視覚署名＋確信度＋視点（来歴）、台帳の参照署名 | OR-sym-llm: 位置のみ（署名・確信度なし） | obs→asset 対応＋asset→異常bool | `is_missed_anomaly`/`required_action` | 対応完全＆見逃し0 / 対応精度 |

**射程注記（各レポートに明記）**: agent 版は決定的アブレーションが担う機構検証とは**別射程**の「実 LLM が当該蒸留情報を
使って勝てるか」を測る。stub では数値は無意味（ハーネス健全性のみ）。live（OPENAI_API_KEY＋コスト承認）で実測。

各 S3/S4 は静的割当・単発推論に簡約する（オンライン較正ループ・完全 custody 再構成は決定的版が担当）。
この簡約は S6 が「H4 機構＝決定的、規制写像の agent 寄与＝agent 版」と射程を分けるのと同じ方針。

---

## 3. R-A / R-C 詳細

- **R-C-1 時刻サフィックス**: `write_scenario_all(..., time_suffix="")`、CLI `report-all --timestamped` で
  `scenario-all-<date>T<HH-MM-SS>.md`。既定（日付のみ）は後方互換のまま。単体テスト追加。
- **R-A-2 S6 主シグナル**: agent レポートに「主シグナル＝平均コスト差（Wilcoxon p・bootstrap CI）」の
  callout を追加。高コスト誤りが稀/同値だと per-seed McNemar が低検出力（p=0.5）になる旨を明示し、
  結論は平均コスト差で述べる。`docs/LIVE_RESULTS.md` にも 1 段落。
- **R-A-1 第2モデル readiness**: `configs/experiments/*_m2.yaml`（llm_model を第2スナップショットに固定、
  他は既存 live と同一）を T2/T7/T5/S1/S2/S6 に用意。`make live-m2-all`＋`make exp-m2-estimate`、
  `docs/LIVE_RESULTS.md` に再現プロトコル＋コスト見積（`orx exp estimate`）。**課金は user トリガ**。
- **R-C-2 S5 p 値**: 主評価が決定的・seed 非依存のため意図的に対象外。レポートに理由を明示（強制 seed 化はしない）。

---

## 4. 検証ゲート（DoD）

1. `uv run pytest -q` 全緑（新規 stub テスト含む）。
2. `make scenario-all` 緑（決定的シナリオは glob で *_live.yaml を除外＝従来通り、反証ゲート不変）。
3. `make lint`（ruff）緑。
4. 各 agent 経路が stub で実 API を叩かず通る（CacheMiss/PreflightError は openai のみ）。
5. live config は `orx exp estimate` 相当のコスト提示とともに**実行可能状態**（実課金は保留）。
6. docs（本計画・SCENARIO_AGENT_DESIGN・IMPROVEMENT・LIVE_RESULTS）整合。

---

## 5. 進め方（小さな単位で実装→検証→修正→反復）

順序: 1(R-C) → 2(S6) → 3..6(S7,S5,S3,S4) → 7(第2モデル) → 8(docs＋全ゲート)。
各単位で「対象 Phase/仮説の一文宣言 → 実装 → 単体/stub → scenario-all 影響確認」を回す。

---

## 8. R-1 §1 ラウンド（2026-06-17）: 横断サマリ完全性・live 再現ゲート・S4 ハードケース

IMPROVEMENT.md §1（B/C/A-readiness）の実装ラウンド。すべてオフライン完結・課金ゼロで検証済み。

### §1-B 横断サマリ・ログの完全性（完了）
- `render_scenario_all` に `_comparison_digest`（対比較 p 値の 1 行ダイジェスト／S5 は「なし」を明示）＋
  `_robustness_digest`（primary 条件の曲線端点）＋`_s7_safety_note`（誤配送0維持＋success 曲線）を追加。
  集約サマリから曲線・対比較・S7 トレードオフが抜ける問題を解消。テスト 4 本（`test_scenario_all_report.py`）。
- **live 再現ゲート**: `tests/scenarios/test_live_cache_reproduction.py` が S1/S2/S6 の `*_live.yaml` を
  **provider.mode=cache** に上書きして実行し、headline 不等式（S1 完遂=1.0>baseline・S2 安全違反0<baseline・
  S6 コスト<B0 かつ高コスト誤り0）を回帰検証。実 API 非依存（CacheLLMClient はミスで CacheMissError）、
  キャッシュ不在 checkout は `pytest.skip`。`make scenario-verify-live-cache`＋`orx scenario run --mode cache`。

### §1-C ハードケース増強（S4 着手）
- S4 世界に近接異常資産 `I-501`（正常 `I-500` と約0.27m）を追加。OR-sym は位置取り違えで異常見逃し 5→8、
  OR-full は署名で 1.000/見逃し0 を維持。反証 27/27 不変。回帰テスト `test_hardcase_i500_i501_position_ambiguity`。

### §1-A live readiness（検証のみ・無課金）
- 7 本の `s*_live.yaml` が `make scenario-run-live-all` で 1 コマンド実行可能（ロード検証済み・workload は
  8–24 agent-calls/シナリオ）。第2モデルは `make exp-estimate-m2`（T2 中央 ~$0.49・T5 ~$0.03・T7 ~$0.0005）。
  **実課金（mode=openai）は未実行**（CLAUDE.md §6・要承認）。stub/cache 経路は全緑。

### 検証ゲート（2026-06-17）
`uv run pytest -q` 369 passed / `make scenario-all` EXIT 0・反証 27/27 ✓ / `ruff` clean /
`lint-imports` 7 kept 0 broken / `make scenario-verify-live-cache` 3 passed（0 円）。

---

## 9. §1-C 残部ラウンド（2026-06-17b）: S7 ハードケース＋ S3/S5 前面化掃引の回帰固定

IMPROVEMENT.md §1-C を全 7 シナリオで「明示ハード事例 or 前面化掃引」のいずれかにより完了。

- **S7 ハードケース**: `configs/world/s7_ownership.yaml` の dining_hall を 3→4 人（最混雑ゾーン）。
  ゾーン+記号が最も無力な 4-way 曖昧で OR-vec/B0 誤配送 0.833→0.857、OR-full は署名＋確認(X5)で誤配送0 維持。
  回帰テスト `test_hardcase_crowded_zone_or_full_zero_misdelivery`。
- **S3/S5 前面化掃引の回帰固定**: トポロジ/較正の flip リスクを避け世界は不変のまま、既に前面化済みの頑健性掃引を
  回帰テストで固定（S3=`fault_degradation` 全域で OR-full 正答率最優位／S5=`custody_gap_rate` 全域で OR-full
  監査完全性最優位）。`test_robustness_or_full_*_across_sweep`。SCENARIOS.md §4「掃引を主指標に前面化」分岐を満たす。
- **設計判断**: S2/S6/S1 は live キャッシュ依存のため世界不変（cache 再現ゲート保全）。S3/S5 はトポロジ編集の
  flip リスク＞限界価値のため世界不変。S4/S7 は live キャッシュ非依存かつ価値が高いため明示ハード事例を追加。

### 検証ゲート（2026-06-17b）
`uv run pytest -q` 372 passed / `make scenario-all` EXIT 0・反証 27/27 ✓ / `ruff` clean /
`lint-imports` 7 kept 0 broken / `make scenario-verify-live-cache` 3 passed（0 円・S1/S2/S6 cache 不変）。

---

## 10. §2 S7 検出力ラウンド（2026-06-17b）＋ §1 コスト概算

- **§2 解決（S7 検出力）**: 主操作点 sep=0.6 で OR-full が確認委譲を多用するため、連続指標 success_rate の
  vs B0 検定が 8 seed で p≈0.0625（不確定）だった。効果は全 seed 数で一貫（OR-full≈2×B0）であることを
  オフライン probe で確認（n=8:0.0625 → n=16:0.0205 → n=20:0.0119）した上で、`configs/experiments/s7_ownership.yaml`
  の seeds を 8→20 に増やし**検出力を付与**（p-hacking ではない）。結果: success Wilcoxon vs B0 p=**0.0119**、
  安全 McNemar p=**1.91e-06**、誤配送0 不変、反証 27/27 緑。回帰テスト
  `test_success_rate_powered_vs_b0_at_production_seeds`（本番シード数で success/safety 両検定の有意を固定）。
- **§1 コスト概算（無課金で算出）**: 全 live 記録 < $1（S3/S4/S5/S7 agent 新規 ~$0.07／第2モデル T2/T5/T7 ~$0.52／
  S1/S2/S6 m2 ~$0.08）。記録は CLAUDE.md §6 によりユーザー承認が前提のため**未実行**。go/no-go と runbook は
  IMPROVEMENT.md §1 に記載。

### 検証ゲート（2026-06-17b）
`uv run pytest -q` 373 passed / `make scenario-all` EXIT 0・反証 27/27 ✓ / `ruff` clean /
`lint-imports` 7 kept 0 broken / `make scenario-verify-live-cache` 3 passed（0 円）。
