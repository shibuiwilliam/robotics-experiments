# ORX 進捗トラッカー

> `docs/IMPLEMENTATION_PLAN.md` のチェックリスト鏡像。セッション開始時はここを読み、最初の未チェック項目から再開する。
> 各フェーズゲートでは完了基準の**測定値**をこのファイルに記録する。

## Scaffold（計画承認前の足場 — 完了）

- [x] リポジトリ構成（PROJECT.md §11 準拠）、pyproject.toml、uv環境
- [x] ツーリング: ruff（T20=print禁止）、pytest、import-linter（依存方向＋oracle分離契約）
- [x] 空テストスイート緑（14 passed: import・CLI・アーキテクチャ契約）
- [x] docs/IMPLEMENTATION_PLAN.md 作成
- [ ] **計画のユーザー承認** ← いまここ（承認まで実装着手しない）

## Phase P0 — 基盤

- [ ] P0-01 common.schemas（pydanticメッセージ）
- [ ] P0-02 common.iri
- [ ] P0-03 common.seeding
- [ ] P0-04 common.logging（structlog JSONL）
- [ ] P0-05 common.providers（openai/cache/stub）
- [ ] P0-06 common.config（YAML＋構成ハッシュ）
- [ ] P0-10 C1 ミニ倉庫世界 v0（1アーム・10物体・オフスクリーン224px・真値アクセサ）
- [ ] P0-11 C1 ベンダースキーマA＋劣化ノブ骨格（全ノブ0）
- [ ] P0-12 C4 合成検出器＋CLIP埋め込み（MPS/CPU/stub）
- [ ] P0-13 C4 知覚パス1バッチプロファイル（性能予算比較を記録）
- [ ] P0-14 オントロジー v0（upper/spacetime/agency TTL＋SHACL shapes）
- [ ] P0-15 C6 kg（書込API・named graphクレーム・LanceDB索引）
- [ ] P0-16 C5 アンカリング骨格（ID決定的＋最近傍ゲート）
- [ ] P0-17 C8 oracle（真値ABox＋忠実度メトリクス）
- [ ] P0-18 C9 replay（記録・マニフェスト・リプレイ同一性）
- [ ] P0-19 CQフレームワーク＋構造CQ 3〜5問
- [ ] P0-20 CLI（demo / sim run / replay / report / cq）
- [ ] P0-21 **フェーズゲート** — 測定値: 忠実度F1=___（基準>0.95）、リプレイ同一性=___

## Phase P1 — アンカリング・同一性（T1）

- [ ] P1-01 C1 モバイルベース＋ベンダースキーマB
- [ ] P1-02 C2 skills骨格（navigate/pick/place）
- [ ] P1-03 C5 本格アンカリング（D1スコア・マージ/分裂）
- [ ] P1-04 C10 expランナー骨格（行列・統計・exp run）
- [ ] P1-05 条件定義（OR-full / OR−identity）
- [ ] P1-06 T1スイート
- [ ] P1-07 **フェーズゲート** — 測定値: T1成功率(OR-full)=___ vs (OR−identity)=___、McNemar p=___

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
