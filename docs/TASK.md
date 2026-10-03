# TASK.md — SBP 開発計画・タスク管理

- 日付: 2026-10-03（初版）
- 対象: SBP の設計・実装・構築・運用に関わるすべての開発者・Claude Code セッション
- 位置づけ: **計画とタスクの単一管理簿**。規約は `docs/development-rules.md`、設計の正典は `docs/design/sbp-design.md`、マイルストーンの現在地サマリは `docs/STATUS.md`。矛盾があれば 設計書 → CLAUDE.md → development-rules.md → 本書 の順に優先する。

---

## 0. スコープ定義

### 0.1 スコープ内（本リポジトリで作るもの）

| 領域 | 内容 | 設計書 |
|---|---|---|
| 基盤本体 | core 型、イベントログ、二時制知識グラフ、業務統合、WM、接地、推論、アクション、API | §2–§10 |
| ローカル開発基盤 | Docker Compose（4 サービス）、Makefile、CI 相当の `make check` | §12 |
| 合成データ検証 | `SynthWorld` によるテスト用フィクスチャと統合テスト | §13.2 |
| 運用ツール | 再接地ジョブ、可観測性、runbooks、不一致キュー UI | §12, §6.8 |

### 0.2 スコープ外（やらない。要求されたら ADR で棄却理由を残す）

- ロボット制御ループそのもの（モーションプランニング）— 基盤は状態と意味の供給まで（§1.5）
- 業務システムの代替 — ERP/MES は system of record のまま
- 汎用 LLM チャットボット
- ユースケース検証（実機・サンドボックス実験）— 別リポジトリ
- 実データ・実人物映像の取り扱い — 開発中は合成データのみ（不変条件 9）
- CUDA / 大規模モデル — MacBook（MPS）で学習・推論できる数百万パラメータ級まで

---

## 1. 品質基準（Definition of Done とゲート）

### 1.1 タスク単位の DoD（全タスク共通）

1. 型が通る（pyright strict）、`make check` が緑。
2. テストがある — 新機能は単体 + 該当する契約/性質テスト。バグ修正は再現テストが先。
3. 不変条件 1〜12（CLAUDE.md）に抵触しない。抵触しうる変更はテストで「壊れたら落ちる」ことを保証する。
4. 公開関数に docstring（1 行要約 + 引数の意味）。ログに `event_id` / `correlation_id` / `entity` を bind。
5. 設計から逸れた判断は ADR 起票済み。オントロジー変更は CHANGELOG + semver 判定済み。
6. PR 記載事項（触った層 / 不変条件への影響 / オントロジー版 / テスト種別 / ADR リンク）が埋まっている。

### 1.2 マイルストーン単位のゲート

**M(n) の統合ゲートが緑になるまで M(n+1) に着手しない**（CLAUDE.md）。ゲートは各マイルストーン末尾の「ゲート G(n)」に定義する。ゲート通過時に `docs/STATUS.md` を更新し、本書の該当タスクをすべて `[x]` にする。

### 1.3 数値品質目標（Phase 1 相当。設計書 §13.1 より、合成データで先行計測する）

| 領域 | 指標 | 目標 |
|---|---|---|
| 接地 | 上位 20 概念の AUROC / ECE | ≥ 0.85 / ≤ 0.05（プローブ昇格は整合残差との AND） |
| 照合 | 不一致検出の精度 / 再現率（注入テスト基準） | ≥ 0.8 / ≥ 0.6 |
| 予測 | 区間被覆率 | ≥ 0.85 |
| 同一性 | 束縛正解率 / 撤回率 | ≥ 0.9 / ≤ 0.05 |
| 性能 | 単体テスト所要 | ≤ 5 分（ネットワーク不要） |

---

## 2. タスク運用ルール

- **ID 規約**: `M<里程>-<連番>`（例 `M0-03`）。横断タスクは `X-<連番>`、運用タスクは `OPS-<連番>`。
- **状態**: チェックボックスで管理。`[ ]` 未着手 / `[~]`（手書きで `~`）進行中 / `[x]` 完了。進行中は同時に **2 件まで**（WIP 制限）。
- **粒度**: 1 タスク ≒ 1 PR ≒ 1 日以内で完了できる大きさ。超えるなら分割して連番を振り直す。
- **ブランチ対応**: 1 タスク = 1 ブランチ（`feat/<module>-<topic>` / `fix/...` / `ont/...`）。コミットは Conventional Commits。
- **着手条件（Definition of Ready)**: 依存タスクが完了している、設計書の該当章を読んだ、オントロジー変更を伴うなら変更手順（development-rules.md §6.1）を先に回す計画がある。
- **順序**: 各マイルストーン内のタスクは原則番号順（依存を番号順に並べてある）。並行可能なものは `並行可` と明記。
- **更新手順**: タスク完了ごとに本書のチェックを更新して同じ PR に含める。ゲート通過時のみ `docs/STATUS.md` も更新する（進捗の二重管理をしない: 日々の粒度は本書、里程の粒度は STATUS.md）。
- **追加・変更**: タスクの追加は自由（連番を末尾に足す）。スコープ変更・タスクの削除は ADR。
- **Claude Code セッションの進め方**: セッション開始時に本書の進行中タスクを確認 → 該当層のルール（`.claude/rules/`）と設計書該当章を読む → 実装 → DoD 確認 → チェック更新。大きな変更（新モジュール・オントロジー MAJOR・ストア変更・サービス追加）は plan mode で承認を得てから。定型の指示文は `docs/PROMPT.md` のプロンプト集（P-00 状況把握 → P-10 タスク実行 → P-12 完了処理 ほか）を使う。

---

## 3. マイルストーン計画

### M0 骨格 — 型・イベントログ・二時制 API・開発基盤

目的: 以後の全レイヤーが依存する「core の型」「イベントが流れる」「信念が二時制で永続化される」を最小で成立させ、`make check` を緑にする。

- [x] **M0-01** リポジトリ独立化の判断と実施 — `git init`（親 repo から分離）、`.gitignore`（`.env`, `__pycache__`, `.DS_Store` 等）、初回コミット。独立しない場合は ADR に理由を残す → 完了（ADR-0001、2026-10-03）
- [x] **M0-02** プロジェクト基盤 — `pyproject.toml`（uv, Python 3.12, ruff, pyright strict, pytest + markers, pytest-socket, hypothesis, freezegun）、`Makefile`（help/bootstrap/test/lint/typecheck 他、全ターゲット .PHONY + 1 行説明） → 完了（pyproject: uv/ruff/pyright strict/pytest+markers/pytest-socket/hypothesis/freezegun、Makefile 17 ターゲット）
- [x] **M0-03** `sbp.core.errors` / `sbp.core.config` — 例外階層、`Settings`（pydantic-settings, `SBP_` プレフィクス）。`.env.example` と同期 → 完了（errors 階層 40 例外、Settings + 層別閾値 3 グループ）
- [x] **M0-04** `sbp.core.time` / `sbp.core.ids` — UTC 強制（naive 拒否）、右開区間型 `Interval`、Allen 区間関係、UUIDv7。性質テスト（区間代数）同時作成 → 完了（UTC 強制・右開 Interval・Allen 13 関係・UUIDv7、性質テスト 9 件）
- [x] **M0-05** `sbp.core` 中核型 — `IRI`, `Modality`（様相格子）, `Belief`（boolean/categorical/numeric）, `Assertion`, `EventEnvelope`（pydantic v2、設計書 §3.5 / §6.1 / 付録 A.1 準拠） → 完了（Modality 格子・Belief 3 種・Assertion・EventEnvelope）
- [x] **M0-06** オントロジー最小セット — `ontology/imports/` 取得スクリプト（固定版同梱）、`ontology/core/*.ttl`（L1 接続概念 + 必須注釈）、`shapes/`、`process/*.lp` の骨格、`VERSION` / `CHANGELOG.md` → 完了（オントロジー 0.1.0: 78 用語、SHACL 8 形状、事象計算 .lp）
- [x] **M0-07** 生成パイプライン — `make gen`（ontology → `vocab.py`、EventType → `schemas/*.json`）と `make ontology-check`（TTL 構文 / SHACL 自己検証 / .lp 対応 / semver 互換 / 生成物差分） → 完了（make gen / make ontology-check 7 項目）
- [x] **M0-08** `infra/compose.yaml` — redpanda / timescaledb-ha / oxigraph / minio（platform・healthcheck・mem_limit、計 6 GB 以内）、名前付きボリューム、`make up/down/nuke` → 完了（4 サービス稼働。ADR-0003/0004）
- [x] **M0-09** `make seed` — トピック作成（`scripts/topics.py`）、alembic 初期マイグレーション（`processed_events`、`latent_index` 雛形、hypertable）、MinIO バケット → 完了（トピック 11・alembic・バケット・オントロジー投入。冪等）
- [x] **M0-10** `sbp.eventlog` — producer（EventEnvelope のみ・送信前 JSON Schema 検証）、consumer（at-least-once、`processed_events` による冪等化、リトライ、graceful shutdown）。単体はモックブローカー → 完了（producer/consumer・スキーマ検証・冪等化・有限リトライ。単体 20 件）
- [x] **M0-11** `sbp.knowledge.assertions` — 二時制 API（`assert_` / `retract` / `query(as_of, valid_at, modality, min_confidence)`）、RDF-star メタデータ、名前付きグラフ戦略、`ontology_version` 必須返却。単体は pyoxigraph 組み込み → 完了（assert_/retract/query。ADR-0005 で RDF 1.2 記法に。単体 23 件）
- [x] **M0-12** 性質・契約テスト一式 — 二時制 as-of/valid-at、様相格子、冪等性（hypothesis）、`schemas/*.json` ↔ pydantic 往復 → 完了（性質 14 件: 二時制・区間代数 / 契約: スキーマ往復・語彙・semver）
- [x] **M0-13** 統合テスト G0 — Compose 上で produce → consume → assert_ → as-of query の往復（`-m integration`） → 完了（統合 9 件緑）

**ゲート G0**: ✅ **通過（2026-10-03）**
- 条件 1 `make check` 緑: lint / pyright strict / 層依存 5 契約 / ontology-check 7 項目 / 単体・性質・契約 168 件
- 条件 2 M0-13 緑: 統合 9 件（produce → consume → assert_ → as-of query、再配送の冪等性、シナリオ隔離、スタック疎通）
- 条件 3 再現性: 4 サービス稼働（oxigraph は ADR-0004 により Health 欄が空欄で正常）、`make seed` は冪等、全イメージ固定版で匿名取得可
- 実装中に判明した外部要因・仕様差は ADR-0003/0004/0005 に記録し、CLAUDE.md・層別ルール・設計書へ反映済み

主要リスク: pyoxigraph の RDF-star API 制約（詰まったら ADR で回避策）、arm64 イメージの互換。

### M1 統合 — 業務世界を信念に引き込む

目的: 業務記録・計画・規範が「信頼性メタ付きの主張」としてイベントログと知識グラフに入る。

- [x] **M1-01** `Record` / `BusinessEvent` / `PlanItem` / `Norm` の core 型とオントロジー定義（EventType → schema 生成を含む。オントロジー変更手順を先行） → 完了（Record / PlanItem / Norm / UnresolvedRef。オントロジーは 0.2.0 で既存）
- [x] **M1-02** `Connector` Protocol と業務モック — `mock/`（SQLite + 台本）。CDC/API ポーリングの共通 IF（`pull(since)`, `stream()`） → 完了（Connector Protocol + SQLite モック。§14 の台本で意図的な誤りを仕込める）
- [x] **M1-03** RML 写像基盤 — `mappings/*.rml.ttl` の実行器、写像ごとの契約テスト（キー保存・必須制約・多重度） → 完了（写像 DSL 4 本。ADR-0006。契約テスト 17 件で 3 保存性を検査）
- [x] **M1-04** スキーマ指紋とドリフト検知 — 起動時比較、`MappingDriftDetected` イベント（例外にしない） → 指紋比較と検知は実装済み。列単位の差分（added/removed/retyped）の算出が未了（前回の列構成の永続化が必要） → 完了（指紋比較 + 列単位差分 added/removed/retyped + 破壊性判定）
- [x] **M1-05** `is_about` 解決 — `resolver.py`、未解決は `UnresolvedRef` として保持（捨てない） → 完了（MasterDataResolver。未解決は UnresolvedRef として残しイベントも出す）
- [x] **M1-06** 情報源信頼性モデル — `reliability/*.yaml` 初期値、`likelihood(record, state) -> log p`、`update_from_resolution()` の骨格 → 完了（時刻ずれ分布・混同行列・系統誤差・継続推定。設計書 §3.6 の値を初期値に）
- [x] **M1-07** 計画・規範の取込 — PlanItem → `planned` アサーション（遵守率事前分布）、Norm → `.lp` 規則 + 出典来歴 → 完了（PlanItem → planned アサーション、遵守率事前分布。NormRule は出典必須）
- [x] **M1-08** 書き戻し dry-run — 冪等 `command_id`、応答の BusinessEvent 化、`SBP_WRITEBACK_MODE=dry` 既定、補償定義の検証 → 完了（既定 dry。補償必須・冪等 command_id・失敗は再試行可）
- [x] **M1-09** 統合テスト G1 — モック業務システムの台本 → 写像 → BusinessEvent → 知識グラフに `planned` / 記録由来アサーションが載る → 完了（統合 8 件。§14 の 4 情報源すべて取り込み、planned と記録由来が共存）

**ゲート G1**: ✅ **通過（2026-10-03）**
- 条件 1 G0 維持 + `make check` 緑: 単体・性質・契約 231 件、ontology-check 7 項目、層依存 5 契約
- 条件 2 M1-09 緑: 統合 17 件（G0 の 9 + G1 の 8）。§14 の 4 情報源すべてが台本から取り込まれ、
  planned（遵守率 0.9）と記録由来の信念が別グラフで共存、書き戻しは dry で業務システムを変更しない
- 条件 3 写像契約テスト緑: キー・必須制約・多重度の 3 保存性を 41 件で検査
- ADR-0006（写像 DSL）を起票し、ルールと設計書 §7.2 に反映済み

### M2 接地 — 潜在から記号へ（WM は合成潜在で代替）

目的: 接地層の全 IF を、`SynthWorld` が生成する合成潜在系列で先に完成させる（WM 本体は M4）。

- [x] **M2-01** `sbp.testing.synth.SynthWorld(seed)` — 事象計算規則からの状態系列生成、合成潜在（既知の線形構造 + ノイズ）、`oracle`（真値。testing 外から import 禁止の lint 境界も同時に） → 完了（真値・観測・階層潜在。cycles で変化点を複数に。oracle は lint 境界で保護）
- [x] **M2-02** `ProbeSpec` と線形プローブ — 登録メタデータ（§5.2）、分類/回帰、学習（弱教師）、非線形必要時のログと WM 改善候補登録 → 完了（線形プローブ。未較正の予測を例外で拒否。有効ドメイン判定つき）
- [x] **M2-03** `TimeBag` と MIL 損失 — 記録由来の弱ラベル生成（Record → 概念×実体×時間バッグ）、信頼性由来の重み → 完了（TimeBag + noisy-OR MIL。重みは信頼性由来。勾配は解析的）
- [x] **M2-04** `Calibrator` — 温度スケーリング / conformal、ECE 計測、認識的・偶然的不確実性の分離 → 完了（温度スケーリング + conformal。ECE 0.082→0.015 を実測、順位は不変）
- [x] **M2-05** `promote()` ゲート — AUROC ≥ 0.85 ∧ ECE ≤ 0.05 ∧ 整合残差 ≤ 既存 active 中央値の AND。満たさなければ例外 → 完了（AND ゲート。AUROC 1.0 でも ECE・整合残差で拒否することをテストで保証）
- [x] **M2-06** 事象昇格パイプライン — BOCPD（自前 `bocpd.py`）→ ヒステリシス → 最小持続 → clingo 逆写像 → 重複抑制 → `TypedProbabilisticEvent`（`uncertainty_s` 必須） → 完了（BOCPD はベイズ因子判定。逆写像が §14 の Fault/StopCmd 判別を再現）
- [ ] **M2-07** 同一性束縛 — Track → `UnidentifiedPhysicalObject` 登録、`linear_sum_assignment`、`IdentityClaim`（撤回時 kind=4 起票は M3 と接続）
- [ ] **M2-08** 空間束縛 — shapely ゾーン包含、位置 `(mean, cov)` → `locatedIn` 確率、包含階層の導出
- [ ] **M2-09** 検証 — pyshacl（Violation → 隔離キュー / Warning → 不一致候補）、イベントの `validation` フィールド
- [ ] **M2-10** 統合テスト G2 — SynthWorld の合成潜在 → プローブ → 昇格 → イベントログ → 知識グラフまで通し、既知真値に対する AUROC/ECE を計測

**ゲート G2**: G1 維持 + M2-10 緑 + 合成データ上で昇格ゲート（M2-05）を通るプローブが 1 概念以上存在。

### M3 推論 — 三源統合と不一致の製品化

目的: 観測・記録・計画が一つの事後信念に統合され、五類型の不一致が起票→解決→学習まで回る。

- [ ] **M3-01** `AuditMask` — 監査対象の記録と派生信念を入力から除外する仕組み（不変条件 6）。条件付け・記録尤度の両方に適用
- [ ] **M3-02** 信念更新エンジン — 実体単位の前向きフィルタ（半マルコフ、log 空間・numpy）、遷移（T + 滞在時間 + 遵守率）× プローブ尤度 × 記録尤度、三源寄与率 `contrib` を来歴へ
- [ ] **M3-03** 実体間結合 — `strong_relations`（部分−全体・同一 Situation）限定の loopy BP 周期実行
- [ ] **M3-04** `Discrepancy` と状態機械 — 付録 A.2 準拠、`transition()` 経由のみ、重複統合、Suppressed は系統誤差限定 + 来歴
- [ ] **M3-05** 検出規則 kind1〜kind5 — `rules/kind<N>.py` に 1 つずつ。閾値 θ・持続 τ は Settings（拠点別上書き）
- [ ] **M3-06** 解決 → 学習信号 — `learning.py`（物理正: 記録訂正提案 + 信頼性更新 / 記録正: 負例 TimeBag / 両誤: 概念候補 / 判定不能: 観測要求キュー）
- [ ] **M3-07** 説明サービス — 説明グラフ（記号側規則・記録・計画 ∪ 潜在側プローブ・観測窓・帰属 + 寄与率）、as-of 指定の再構成
- [ ] **M3-08** 規則エンジン — clingo 実行基盤（`.lp` 側テンプレート）、確信度伝播（min/積/noisy-OR）の規則別宣言
- [ ] **M3-09** 統合テスト G3 — 注入テスト: SynthWorld に既知の不一致（幽霊在庫・遅延記帳・状態食い違い）を注入し、検出精度 ≥ 0.8 / 再現率 ≥ 0.6 を計測

**ゲート G3**: G2 維持 + M3-09 緑 + ワークドエグザンプル（設計書 §14）の前半（観測×記録×計画 → 事後 → D2 起票）が合成データで再現できる。

### M4 WM — 本物の潜在ダイナミクス

目的: 合成潜在を実モデルに差し替える。接地層 IF は M2 で固定済みのため、WM 側だけを入れ替えられることを証明する。

- [ ] **M4-01** `WorldModel` Protocol — `encode/step/rollout/probe/explain/embed/health`、`LatentState(z, cov|ensemble, level, ts, wm_version)`
- [ ] **M4-02** `koopman` 族 — 線形潜在遷移、SynthWorld 時系列で学習、MPS 対応・数百万パラメータ以下
- [ ] **M4-03** `jepa_ts` 族 — 時系列潜在予測（並行可: M4-02 と独立）
- [ ] **M4-04** 学習パイプライン — `train/`、合成損失 `L_pred + λ_phys + λ_probe + λ_logic + λ_align`（各項個別ログ、λ_align 後期スケジュール）、`seed` 必須
- [ ] **M4-05** 記号条件付け — `context.build(entity, mask: AuditMask)`、実例グラフ k-hop 埋め込み
- [ ] **M4-06** レジストリ — `WorldModelVersion` のオントロジー登録、重み `minio://models/<id>/<version>/`、version 指定ロード（latest 禁止）、プローブ互換性行列
- [ ] **M4-07** `health()` とドリフト監視 — 潜在シフト（MMD/PSI）、予測誤差、整合残差。閾値は Settings、超過でイベント発行
- [ ] **M4-08** 潜在索引の実運用 — `latent_index` への書き込み、HNSW、ハイブリッドクエリ `hybrid.plan()`
- [ ] **M4-09** 統合テスト G4 — SynthWorld 観測 → koopman encode → M2 のプローブ/昇格がそのまま動作し、整合残差が計測できる。合成潜在版と指標比較

**ゲート G4**: G3 維持 + M4-09 緑 + 2 モデル族が同一 Protocol で差し替え可能（レジストリ経由の切替テスト）。

### M5 閉ループ — アクションと検証可能な期待

目的: 信念 → アクション → 期待 → 検証 → 学習のループを閉じ、外部（API）から安全に使えるようにする。

- [ ] **M5-01** ActionType 基盤 — `actions/*.yaml` 読込 + pydantic 検証（§9.1 形式）、compensation 未定義は登録拒否
- [ ] **M5-02** 意味契約の前提検証 — 記号（SHACL/Datalog）・物理（信念クエリ閾値）・規範（Norm 規則）の三面チェック
- [ ] **M5-03** 承認・実行・補償 — riskClass ≥ medium の承認必須、人の上書きは来歴、レート制限、`ActionEvent` 発行、失敗時補償
- [ ] **M5-04** `Expectation` 検証器 — 付録 A.3 準拠の状態機械、conformal 被覆 / Bayes 因子、記号側期待と潜在側期待の分離検証
- [ ] **M5-05** 予測サービス — SOSA `Forecast`（`horizon_origin` / `lead_time` / `conditionedOn`）、起点別 CRPS・被覆率を `metrics` へ、計画変更時の再予測トリガ
- [ ] **M5-06** What-if / シナリオ — 介入エンコーダ → rollout → `hypothetical` + `scenario` 格納、`promote_scenario()`、反実仮想の妥当性スコア
- [ ] **M5-07** API — FastAPI + strawberry GraphQL（`asOf`/`validAt`/`modality`/`minConfidence` 引数）、SPARQL プロキシ（拡張 SERVICE）、ストリーム購読、コマンド API
- [ ] **M5-08** ワーカー実体化 — `services/`（bridge- / belief- / reconcile- / scheduler-worker）、SIGTERM graceful shutdown、起動時疎通検査
- [ ] **M5-09** 統合テスト G5 — ワークドエグザンプル §14 を合成データで完走: 不一致起票 → アクション提案 → 承認 → dry-run 書き戻し → Expectation 生成 → 検証 → 学習信号

**ゲート G5**: G4 維持 + M5-09 緑 + 予測被覆率 ≥ 0.85（合成データ）。

### M6 進化 — 概念発見・能動観測・LLM 連携

目的: 基盤が自らの知識の欠落を発見し、安全に語彙を育てる。

- [ ] **M6-01** `sbp.llm.Provider` — anthropic / openai / gemini 実装、`complete_structured` / `embed`、tenacity によるリトライ一元化、トークン使用量ログ
- [ ] **M6-02** replay 基盤 — `SBP_LLM_MODE=replay|record|live`、fixture `tests/fixtures/llm/<task>/<hash>.json`、fixture 欠落時は失敗（黙って live に落ちない）
- [ ] **M6-03** プロンプト版管理 — `prompts/<task>/<version>.md`、hash を来歴へ、`routing.yaml`
- [ ] **M6-04** 概念発見ループ — 低被覆・高密度クラスタ / 整合残差持続 / 反復不一致の検出、latent クラスタリング、LLM 候補提案（自動採用禁止）、curation → MINOR 版起票 → プローブ学習 → 再接地の一連
- [ ] **M6-05** エピステミック・スケジューラ — 効用 `Σ v(x)·EIG − cost`、貪欲 + 劣モジュラ、質問頻度上限、結果は `observed` で還流
- [ ] **M6-06** 文書抽出・NL→クエリ — オントロジーガイド付き構造化抽出（根拠スパン必須、pydantic → SHACL 検証、`inferred` 登録）、自然言語 → GraphQL/SPARQL 変換
- [ ] **M6-07** 統合テスト G6 — SynthWorld に未知概念を仕込み、発見 → 提案 →（モック curation）→ プローブ学習 → 再接地で遡及的に事実が増えることを確認（LLM は replay）

**ゲート G6**: G5 維持 + M6-07 緑 + LLM がホットパス（信念更新・照合・事象昇格）から呼ばれていないことの静的検査。

### M7 運用 — 再接地・UI・可観測性

目的: 版更新と日常運用に耐える状態にする。

- [ ] **M7-01** 再接地ジョブ — `plan(scope)` → コスト見積り → `run(plan)`、旧文は `tx_to` 閉鎖のみ、差分レポート（runbook 形式）
- [ ] **M7-02** 版ライフサイクル統合 — オントロジー版 × WM 版 × プローブ版 × 写像版の互換性行列、昇格パイプライン（影響分析 → カナリア → 再接地 → 差分レビュー）
- [ ] **M7-03** 可観測性 — §12.3 の指標（WM / 接地 / 信念 / 照合 / 統合 / 運用）を metrics テーブル + Prometheus エクスポート、Grafana ダッシュボード（observability profile）
- [ ] **M7-04** UI — 不一致キュー（証拠並置・解決入力 = 学習信号）、信念ビュー（三源 + as-of スライダ）、期待ボード、概念 curation（`apps/ui`, Node 20）
- [ ] **M7-05** runbooks — 再接地 / 版昇格 / 障害対応（ブローカー停止・グラフストア停止・ワーカー滞留）/ バックアップ・リストア
- [ ] **M7-06** プライバシー・セキュリティ検証 — 潜在復元攻撃テストの雛形、ABAC（概念型 × 様相 × 拠点）の API 適用、シークレットスキャン
- [ ] **M7-07** 統合テスト G7 — プローブ版を上げて再接地 → as-of で旧信念が再構成でき、新 tx で新信念が載ることを確認

**ゲート G7**: G6 維持 + M7-07 緑 + runbooks に従って第三者（別セッション）が再接地を実行できる。

---

## 4. 横断・継続タスク（マイルストーンに属さない）

- [ ] **X-01** CI 整備 — GitHub Actions 等で `make check` + 統合テスト（サービスコンテナ）。M0 完了後すぐ
- [ ] **X-02** import 境界 lint — 層依存の向き（development-rules.md §4）と `sbp/testing` オラクル境界を機械検査（import-linter 等）。M0-05 以降随時
- [ ] **X-03** ADR の継続運用 — 設計逸脱・閾値変更・ライブラリ選定のたびに `docs/adr/NNNN-*.md`
- [ ] **X-04** `docs/STATUS.md` 更新 — ゲート通過時のみ（§2 の更新手順）
- [ ] **X-05** 依存更新・脆弱性対応 — 月次で `uv lock --upgrade` + `make check`
- [ ] **X-06** development-rules.md / 本書の保守 — 規約・計画の変更はコード PR と同じレビューを通す

---

## 5. 運用フェーズの定常タスク（G7 通過後）

| ID | 周期 | 内容 |
|---|---|---|
| OPS-01 | 毎日 | ダッシュボード確認: イベントログ滞留、ワーカー死活、CDC 遅延、照合起票数/解決率 |
| OPS-02 | 毎週 | ドリフト指標レビュー（潜在シフト・予測誤差・整合残差・ECE）。閾値超過はシャドウ評価 → 再学習タスク起票 |
| OPS-03 | 毎週 | 不一致キューの棚卸し: Suppressed の妥当性、未 Assign の滞留、解決結果 → 学習信号の還元率 |
| OPS-04 | 月次 | 人手監査サンプル（不一致起票のランダム抽出を人が判定し精度推定。§13.2） |
| OPS-05 | 月次 | 予測の精度追跡レビュー（起点別 CRPS / 被覆率、「信頼できる先行時間」の更新） |
| OPS-06 | 版更新時 | 昇格パイプライン実施: 影響分析 → カナリア → 再接地 → 差分レビュー（M7-02 の手順） |
| OPS-07 | 版更新時 | 潜在復元攻撃テスト（WM 版ごと。失敗時は次元削減・雑音付加して再評価） |
| OPS-08 | 随時 | インシデント対応は runbooks に従い、事後に runbook 自体を更新する |

---

## 6. リスク登録簿（開発に効くものを抜粋。全体は設計書 §15）

| リスク | 兆候 | 本計画での手当て |
|---|---|---|
| 接地 IF と WM の結合が密になり差し替え不能 | M4 で M2 のテストが書き直しになる | M2 を合成潜在で先行固定し、M4 ゲートに「差し替えテスト」を置く |
| pyoxigraph / RDF-star の機能制約 | M0-11 で実装が設計 §6.4 を満たせない | 早期（M0）に検証。詰まれば ADR で代替（補助テーブル等） |
| ショートカット学習 | AUROC は高いが整合残差が大きい | 昇格ゲートの AND 条件（M2-05）を絶対化。例外昇格を作らない |
| 記録と推定の循環汚染 | 記録訂正後に不一致が消えすぎる | `AuditMask` を M3-01 として独立タスク化し、全呼び出しに強制 |
| スコープ膨張（UI・新サービス・実データ） | Compose が 4 サービスで収まらない | §0.2 のスコープ外リストと ADR 必須ルールで棄却 |
| MacBook の計算資源制約 | 学習が回らない・メモリ逼迫 | モデルは数百万パラメータ上限、Compose 6 GB 上限を healthcheck で監視 |
| 一人開発での品質劣化 | レビューなしマージ | `make check` + 統合ゲートを機械的関門にし、PR テンプレで自己申告を強制 |

---

## 7. 未決事項（決まり次第 ADR 化して本節から消す）

1. ~~**リポジトリ独立化**（M0-01）~~ → 決定済み: 独立リポジトリ化（ADR-0001、2026-10-03）
2. ~~**初期ドメインの選定**~~ → 決定済み: 製造・設備保全（ADR-0002、2026-10-03）
3. **CI 基盤** — GitHub Actions か ローカル `make check` のみか（X-01 着手時に判断）。
4. **UI の技術選定**（M7-04）— フレームワークは着手時に ADR。
5. **法令上の消去要求への対応** — 追記専用原則との両立（knowledge ルールで「別途 ADR」とされている）。運用開始前までに。
