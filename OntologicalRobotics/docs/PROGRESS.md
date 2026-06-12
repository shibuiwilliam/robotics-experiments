# ORX 進捗トラッカー

> `docs/IMPLEMENTATION_PLAN.md` のチェックリスト鏡像。セッション開始時はここを読み、最初の未チェック項目から再開する。
> 各フェーズゲートでは完了基準の**測定値**をこのファイルに記録する。

## Scaffold（計画承認前の足場 — 完了）

- [x] リポジトリ構成（PROJECT.md §11 準拠）、pyproject.toml、uv環境
- [x] ツーリング: ruff（T20=print禁止）、pytest、import-linter（依存方向＋oracle分離契約）
- [x] 空テストスイート緑（14 passed: import・CLI・アーキテクチャ契約）
- [x] docs/IMPLEMENTATION_PLAN.md 作成
- [x] **計画のユーザー承認**（2026-06-12、D1–D4の推奨案込み）

## Phase P0 — 基盤 ✅（2026-06-12 完了）

- [x] P0-01 common.schemas（pydanticメッセージ）
- [x] P0-02 common.iri
- [x] P0-03 common.seeding
- [x] P0-04 common.logging（structlog JSONL）
- [x] P0-05 common.providers（openai/cache/stub）
- [x] P0-06 common.config（YAML＋構成ハッシュ）
- [x] P0-10 C1 ミニ倉庫世界 v0（1アーム・8箱・オフスクリーン224px・真値アクセサ）
- [x] P0-11 C1 ベンダースキーマA＋劣化ノブ骨格（全ノブ0、delay/contradictionはP4で実装）
- [x] P0-12 C4 合成検出器＋CLIP埋め込み（MPS/CPU/stub）
- [x] P0-13 C4 知覚パスプロファイル → docs/design/perf_p0.md（全予算クリア）
- [x] P0-14 オントロジー v0（upper/spacetime/agency TTL＋SHACL shapes＋sameAs禁止shape）
- [x] P0-15 C6 kg（書込API・named graphクレーム・調停・物質化・LanceDB索引）
- [x] P0-16 C5 アンカリング骨格（ID決定的＋最近傍ゲート、OR−identityアブレーション切替）
- [x] P0-17 C8 oracle（真値ABox＋忠実度メトリクス、ORコア不依存）
- [x] P0-18 C9 replay（記録・マニフェスト・反実仮想リプレイ）
- [x] P0-19 CQフレームワーク＋CQ 4問（tasks/competency_questions/）
- [x] P0-20 CLI（demo / sim run / replay / report / cq / version）
- [x] P0-21 **フェーズゲート** — 測定値（demo_tiny 12s, seed 7, ノイズ0）:
  - 忠実度: トリプルF1=**1.000**（基準>0.95 ✓）、同一性F1=1.000、位置RMSE=0.000m、
    遷移遅延=0.0s、取りこぼし=0、陳腐化率=0
  - リプレイ同一性: metrics.json **バイト一致** ✓（tests/exp/test_episode.py）
  - 反実仮想: OR-no-identity リプレイで同一性F1が劣化（条件切替が機能）
  - 性能: 60sエピソード記録1.06s（予算120s）、リプレイ0.12s（予算30s）
  - テスト: 108 passed（単体・CQ回帰・SHACL・リプレイ同一性・アーキテクチャ不変条件）
  - 補足: D2のon/containsはP0世界に積み重ねが無いため未使用（語彙は定義済み、P1世界で有効化）

## Phase P1 — アンカリング・同一性（T1）✅（2026-06-12 完了）

- [x] P1-01 C1 モバイルベース（擬似LiDAR・cm単位・ID不可読のベンダースキーマB）＋slide搬送
- [x] P1-02 C2 skills骨格 → **P3へ延期**（ADR-009: T1の被験変数は決定の正しさであり、
      物理ピックはスキル導入(P3)で接続。物理再実行なしの反実仮想リプレイと整合）
- [x] P1-03 C5 本格アンカリング（D1最終形: 静止仮説＝密度正規化ガウス ×
      搬送仮説＝等速予測ガウス、埋め込み乗法変調、イベント内大域貪欲割当、
      識別子衝突分裂。ADR-010）
- [x] P1-04 C10 expランナー（条件×シード、記録1回→全条件リプレイ、McNemar/
      Wilcoxon/ブートストラップCI、`orx exp run`・exp対応 `orx report`）
- [x] P1-05 条件定義（OR-full / OR-no-identity、コンフィグのみで切替）
- [x] P1-06 T1スイート（シード駆動生成・決定的実行器・真値採点）
- [x] P1-07 **フェーズゲート** — 測定値（t1_handoff、20シード、劣化: ID読取失敗5%・
      姿勢ノイズ2cm・オクルージョン2%）:
  - T1成功率: **OR-full 0.950 vs OR−identity 0.000**（不一致 19/0）
  - **McNemar p = 3.81e-06** ✓ 有意、Wilcoxon(同一性F1) p < 0.001
  - 同一性F1平均: OR-full 0.985 vs OR−identity 0.000
  - 成果物: data/runs/exp-t1-identity-275e01f8 / reports/exp-t1-identity-275e01f8.md
  - テスト: 120 passed（T1縮小版統合テスト含む、完全オフライン）

## Phase P2 — 業務ブリッジ・エージェント（T2, T7）

- [ ] P2-01 C3 模擬WMS＋SOPコーパス＋識別子スレッド種データ
- [ ] P2-02 business.ttl＋仮想グラフ写像
- [ ] P2-03 アイデンティティ・スレッド表現＋D2述語拡張（oracle同期）
- [ ] P2-04 C7 agent（ツール: SPARQL/業務DB/文書検索/スキル）
- [ ] P2-05 ベースライン B0 / B1
- [ ] P2-06 T2スイート（50問）
- [ ] P2-07 T7スイート（オントロジー誘導 vs ベクトルRAG）
- [ ] P2-08 メタモルフィック変換 v1
- [ ] P2-09 **フェーズゲート** — 測定値: T2正答率 B0=___ / B1=___ / OR=___、正答率/トークン=___

## Phase P3 — 能力契約・故障注入（T3, T6）

- [ ] P3-01 故障注入器
- [ ] P3-02 capability.ttl＋能力契約スキーマ
- [ ] P3-03 能力台帳の経験更新
- [ ] P3-04 エージェント計画の能力クエリ統合
- [ ] P3-05 T3/T6スイート
- [ ] P3-06 **フェーズゲート** — 測定値: Brier改善曲線=___

## Phase P4 — 信念管理・劣化掃引（T4）

- [ ] P4-01 劣化ノブ（sim/perception側のみ）
- [ ] P4-02 信念調停本格化＋陳腐化監視
- [ ] P4-03 T4掃引ランナー＋頑健性曲線
- [ ] P4-04 **フェーズゲート** — 測定値: 乖離領域=___

## Phase P5 — スキーマ・ファジング・オンボーディング（T5）

- [ ] P5-01 スキーマ・ファジング生成器
- [ ] P5-02 `orx onboard` ワークフロー
- [ ] P5-03 T5スイート＋統合コスト比較
- [ ] P5-04 メタモルフィック最終版＋H7横断分析
- [ ] P5-05 （任意）小型実VLA差し替え
- [ ] P5-06 **フェーズゲート** — 統合コスト比較レポート=___
