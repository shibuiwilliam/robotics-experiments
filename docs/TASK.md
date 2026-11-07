# TASK.md — SBP 開発計画・タスク管理

- 日付: 2026-10-03（初版）
- 最終更新: 2026-10-25（2 回目の棚卸し。M8-30・X-11〜X-13 を追加、M7-01 に注記、M8-10 を `[ ]` に戻した、G8 の現在地を統合テストの実行で再測定）
- 前回: 2026-10-17（棚卸し。M8-23〜M8-29・X-09・X-10 を追加）
- 対象: SBP の設計・実装・構築・運用に関わるすべての開発者・Claude Code セッション
- 位置づけ: **計画とタスクの単一管理簿**。規約は `docs/development-rules.md`、設計の正典は `docs/design/sbp-design.md`、マイルストーンの現在地サマリは `docs/STATUS.md`、**人の判断を待つ事項は `docs/HITL.md`**。矛盾があれば 設計書 → CLAUDE.md → development-rules.md → 本書 の順に優先する。

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
- **判断待ちで止まったとき**: 推測で埋めずに `docs/HITL.md` に項目を足し、本書のタスク行に
  `H-NN の判断待ち` と書く。コード側には `TODO(question)` を置く（CLAUDE.md）。**止まっている
  理由と誰の判断待ちかを突き合わせられる状態にする**ことが目的である。
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
- [x] **M2-07** 同一性束縛 — Track → `UnidentifiedPhysicalObject` 登録、`linear_sum_assignment`、`IdentityClaim`（撤回時 kind=4 起票は M3 と接続） → 完了（割当問題で束縛。Person の個人識別を型レベルで禁止。撤回可能）
- [x] **M2-08** 空間束縛 — shapely ゾーン包含、位置 `(mean, cov)` → `locatedIn` 確率、包含階層の導出 → 完了（shapely ゾーン包含をモンテカルロで確率化。境界では断定しない）
- [x] **M2-09** 検証 — pyshacl（Violation → 隔離キュー / Warning → 不一致候補）、イベントの `validation` フィールド → 完了（pyshacl。Violation→隔離 / Warning→不一致候補。SHACL severity の位置バグを修正）
- [x] **M2-10** 統合テスト G2 — SynthWorld の合成潜在 → プローブ → 昇格 → イベントログ → 知識グラフまで通し、既知真値に対する AUROC/ECE を計測 → 完了（統合 10 件。AUROC 1.0000 / ECE 0.0064 / 整合残差 0.0000）

**ゲート G2**: ✅ **通過（2026-10-03）**
- 条件 1 G1 維持 + `make check` 緑: 単体・性質・契約 335 件、ontology-check 7 項目、層依存 5 契約
- 条件 2 M2-10 緑: 統合 27 件（G0 の 9 + G1 の 8 + G2 の 10）
- 条件 3 昇格ゲート通過: Running プローブが **AUROC 1.0000 / ECE 0.0064 / 整合残差 0.0000**
  （保留データ 576 標本）。設計書 §13.1 の Phase 1 目標（AUROC ≥ 0.85 / ECE ≤ 0.05）を満たす
- 三源（planned / observed / inferred）が同じフルーエントについて別グラフで共存することを実環境で確認。
  統合（事後の計算）は M3-02 の仕事
- 注意: AUROC 1.0 は合成潜在が線形分離できる設計（意図的な基準系）による。実 WM（M4）に
  差し替えたときの値がこの基準からどれだけ落ちるかが、潜在の質の指標になる

### M3 推論 — 三源統合と不一致の製品化

目的: 観測・記録・計画が一つの事後信念に統合され、五類型の不一致が起票→解決→学習まで回る。

- [x] **M3-01** `AuditMask` — 監査対象の記録と派生信念を入力から除外する仕組み（不変条件 6）。条件付け・記録尤度の両方に適用
- [x] **M3-02** 信念更新エンジン — 実体単位の前向きフィルタ（半マルコフ、log 空間・numpy）、遷移（T + 滞在時間 + 遵守率）× プローブ尤度 × 記録尤度、三源寄与率 `contrib` を来歴へ
- [x] **M3-03** 実体間結合 — `strong_relations`（部分−全体・同一 Situation）限定の loopy BP 周期実行
- [x] **M3-04** `Discrepancy` と状態機械 — 付録 A.2 準拠、`transition()` 経由のみ、重複統合、Suppressed は系統誤差限定 + 来歴
- [x] **M3-05** 検出規則 kind1〜kind5 — `rules/kind<N>.py` に 1 つずつ。閾値 θ・持続 τ は Settings（拠点別上書き）
- [x] **M3-06** 解決 → 学習信号 — `learning.py`（物理正: 記録訂正提案 + 信頼性更新 / 記録正: 負例 TimeBag / 両誤: 概念候補 / 判定不能: 観測要求キュー）
- [x] **M3-07** 説明サービス — 説明グラフ（記号側規則・記録・計画 ∪ 潜在側プローブ・観測窓・帰属 + 寄与率）、as-of 指定の再構成
- [x] **M3-08** 規則エンジン — clingo 実行基盤（`.lp` 側テンプレート）、確信度伝播（min/積/noisy-OR）の規則別宣言
- [x] **M3-09** 統合テスト G3 — 注入テスト: SynthWorld に既知の不一致（幽霊在庫・遅延記帳・状態食い違い）を注入し、検出精度 ≥ 0.8 / 再現率 ≥ 0.6 を計測

**ゲート G3**: ✅ **通過（2026-10-03）**
- 条件 1 G2 維持 + `make check` 緑: 単体・性質・契約 505 件、ontology-check 7 項目、層依存契約
- 条件 2 M3-09 緑: 統合 37 件（G0 の 9 + G1 の 8 + G2 の 9 + G3 の 11）
- 条件 3 注入テスト: **精度 3/3 = 1.0000（目標 ≥ 0.8）/ 再現率 3/3 = 1.0000（目標 ≥ 0.6）**。
  深刻度は業務価値で順位が付く（類型 3 = 0.900 > 類型 2 = 0.756 > 類型 1 = 0.541）
- 条件 4 ワークドエグザンプル前半: 三源の前向きフィルタが設計書 §14 の事後を再現
  （**0.1302**、設計の目標 0.14、観測が支配。寄与 observed 2.31 / record 1.34 / plan 0.97）→
  D2（状態不一致）が両側の根拠付きで起票され、`Triaged → Assigned → Resolved → Learned` まで回る
- オントロジーは 0.4.0 に上げた（実体間結合のための `partOf` / `aggregatesAs`。MINOR）

### M4 WM — 本物の潜在ダイナミクス

目的: 合成潜在を実モデルに差し替える。接地層 IF は M2 で固定済みのため、WM 側だけを入れ替えられることを証明する。

- [x] **M4-01** `WorldModel` Protocol — `encode/step/rollout/embed/health/attribute`、`LatentState(z, cov|ensemble, level, ts, wm_version)`。`probe`/`explain` は外した（**ADR-0007**）
- [x] **M4-02** `koopman` 族 — 線形潜在遷移（DMD with control、numpy）、SynthWorld 時系列で同定、582 パラメータ
- [x] **M4-03** `jepa_ts` 族 — 潜在予測（torch / MPS）、VICReg 系の崩壊防止、6304 パラメータ
- [x] **M4-04** 学習パイプライン — `train/`、合成損失（各項個別ログ、λ_align 後期スケジュール）、`seed` 必須、段階ゲート
- [x] **M4-05** 記号条件付け — `context.build(entity, mask: AuditMask)`。決定的な特徴化（GNN 埋め込みは将来 ADR）
- [x] **M4-06** レジストリ — 版指定ロード（`latest` 禁止）、プローブ互換性行列、candidate → canary → active
- [x] **M4-07** `health()` とドリフト監視 — 潜在シフト（MMD）、予測誤差、整合残差。単発超過は shadow_evaluate、持続で retrain
- [x] **M4-08** 潜在索引の実運用 — 次元別テーブル + `latent_index_registry`、HNSW、`hybrid.plan()`
- [x] **M4-09** 統合テスト G4 — SynthWorld 観測 → encode → M2 のプローブ／昇格がそのまま動作、合成潜在版と指標比較

**ゲート G4**: ✅ **通過（2026-10-03）**
- 条件 1 G3 維持 + `make check` 緑: 単体・性質・契約 665 件
- 条件 2 M4-09 緑: 統合 54 件（G0 の 9 + G1 の 8 + G2 の 9 + G3 の 11 + G4 の 17）
- 条件 3 **2 モデル族が同一 Protocol で差し替え可能**: レジストリ経由で koopman を退役 →
  jepa_ts をカナリアに切り替え、接地層のコードを変えずに通った

| 潜在 | 次元 | パラメータ | AUROC | ECE | 整合残差 | 潜在シフト | 1 刻み誤差 | 学習時間 |
|---|---|---|---|---|---|---|---|---|
| 合成（G2 基準） | 64 | — | 1.0000 | 0.0069 | 0.0000 | — | — | — |
| koopman | 24 | 582 | 0.9999 | 0.0065 | 0.0000 | 0.0303 | 0.0805 | 0.04 s |
| jepa_ts | 16 | 6304 | 1.0000 | 0.0048 | 0.0000 | 0.0431 | 0.1220 | 3.0 s (MPS) |

実モデルは合成潜在の 1/3 以下の次元で基準と同等。**G2 の「AUROC 1.0 は合成潜在が線形分離
できる設計による」という注記は、実モデルでも落ちなかったことで一度確認できた**——ただし
SynthWorld の難度自体が低いことは変わらない（実機データでの再測定が M7 以降の課題）。

### M5 閉ループ — アクションと検証可能な期待

目的: 信念 → アクション → 期待 → 検証 → 学習のループを閉じ、外部（API）から安全に使えるようにする。

- [x] **M5-01** ActionType 基盤 — `actions/*.yaml` 読込 + pydantic 検証（§9.1 形式）、compensation 未定義は登録拒否、補償の相互参照をロード時に検証
- [x] **M5-02** 意味契約の前提検証 — 記号・物理（信念クエリ閾値）・規範の三面チェック。どの面で落ちたかを残す
- [x] **M5-03** 承認・実行・補償 — riskClass ≥ medium の承認必須、上書きは理由付きで来歴、対象別レート制限、`ActionEvent` 発行
- [x] **M5-04** `Expectation` 検証器 — 付録 A.3 準拠の状態機械、conformal / Bayes 因子、記号側と潜在側の分離検証
- [x] **M5-05** 予測サービス — `Forecast`（`horizon_origin` / `lead_time` / `conditionedOn`）、先行時間別 CRPS・被覆率、`metrics` 行、計画変更での再予測判定
- [x] **M5-06** What-if / シナリオ — 介入エンコーダ → rollout → `hypothetical` + シナリオグラフ、`promote_scenario()`、反実仮想の妥当性スコア
- [x] **M5-07** API — FastAPI + strawberry GraphQL（`asOf`/`validAt`/`modality`/`minConfidence`）、SPARQL プロキシ（読み取り専用）、ストリーム購読、コマンド API
- [x] **M5-08** ワーカー実体化 — `services/runtime.py` + `belief_worker`、SIGTERM graceful shutdown、起動時疎通検査、結合の周期実行
- [x] **M5-09** 統合テスト G5 — ワークドエグザンプル §14 を合成データで完走

**ゲート G5**: ✅ **通過（2026-10-03）**
- 条件 1 G4 維持 + `make check` 緑: 単体・性質・契約 798 件
- 条件 2 M5-09 緑: 統合 69 件（G0 の 9 + G1 の 8 + G2 の 9 + G3 の 11 + G4 の 17 + G5 の 15）
- 条件 3 **予測被覆率 ≥ 0.85**: 先行 60 秒で 0.9500、先行 300 秒で 0.8667（conformal 較正）

| 先行時間 | 被覆率 | CRPS | 区間幅 |
|---|---|---|---|
| 60 s | 0.9500 | 0.0372 | 0.0338 |
| 300 s | 0.8667 | 0.1548 | 0.9801 |

**被覆率だけではゲートにならないことが実測で出た。** 先行 300 秒の区間幅は 0.98——ほぼ [0,1]
全域で、被覆率 0.87 はそれで買っている。CRPS と `reliable_lead_time`（60 秒）が「実際に使える
地平は 60 秒まで」を示す。設計書 §8.3 が「地平ごとの信頼できる先行時間を公開する」と言っている
のはこのためで、**被覆率を単独の合格条件にすると無情報な予測が通る**。G6 以降のゲートに数値条件
を足すときは、同じ落とし穴（指標を 1 つだけ見る）を避ける。

### M6 進化 — 概念発見・能動観測・LLM 連携

目的: 基盤が自らの知識の欠落を発見し、安全に語彙を育てる。

- [x] **M6-01** `sbp.llm.Provider` — Protocol + 構造化出力の検証、トークン使用量の用途別計上。各社 SDK は任意依存で遅延 import
- [x] **M6-02** replay 基盤 — `SBP_LLM_MODE=replay|record|live`、fixture `tests/fixtures/llm/<task>/<hash>.json`、**欠落時は失敗**（黙って live に落ちない）、record は既存 fixture を上書きしない
- [x] **M6-03** プロンプト版管理 — `src/sbp/llm/prompts/<task>/<version>.md`、hash を来歴へ、`routing.yaml`（全用途の宣言を要求、モデル ID は `.env` のみ）
- [x] **M6-04** 概念発見ループ — 低被覆クラスタ / 整合残差持続 / 反復不一致の 3 入口、密度クラスタリング、`ConceptProposal`（自動採用禁止・`rejected_alternatives` 必須）
- [x] **M6-05** エピステミック・スケジューラ — 効用 `Σ v(x)·EIG − cost`、貪欲 + 劣モジュラ（残存エントロピーの追跡）、人への質問頻度上限
- [x] **M6-06** 文書抽出・NL→クエリ — 根拠スパンを型で必須化 + 本文照合、`inferred` 固定。変換は更新操作を拒否し、補った既定値を見せる
- [x] **M6-07** 統合テスト G6 — SynthWorld に未知概念を仕込み、発見 → 提案（replay）→ curation → プローブ学習 → 再接地

**ゲート G6**: ✅ **通過（2026-10-03）**
- 条件 1 G5 維持 + `make check` 緑: 単体・性質・契約 914 件
- 条件 2 M6-07 緑: 統合 83 件（G0〜G5 の 69 + G6 の 14）
- 条件 3 **LLM がホットパスから呼ばれていないことの静的検査**: import-linter の契約
  「LLM をホットパス（信念更新・照合・事象昇格）から呼ばない」を追加（`make check` で検査）。
  テストでも対象モジュールの本文を直接確かめ、契約が外されたことに気付けるようにした

| 指標 | 値 |
|---|---|
| 低被覆クラスタ | 548 標本 / 既知プローブの活性 0.000 / 強さ 0.873 |
| クラスタ内の未知概念の割合 | 1.000（全体 0.400） |
| クラスタ所属だけで学習したプローブの弱ラベル再現 | AUROC 1.0000 |
| **同プローブの未知概念に対する性能** | **AUROC 0.9995 / ECE 0.0118** |

**この数値が証明していることの限界。** 仕込んだ概念 `Accessible` は SynthWorld では実質的に
`Running` の否定であり、発見されるクラスタは「Running プローブが反応しない領域」とほぼ一致する
——**この世界では発見がやや自明になる**。機構（低被覆クラスタ → 弱ラベル → プローブ → 再接地）が
通ることは確かめられたが、「独立した未知概念を見つけた」ことの証明にはならない。実機データでの
再測定が必要であり、`docs/PROGRESS.md` の既知の制約に記載した。

### M7 運用 — 再接地・UI・可観測性

目的: 版更新と日常運用に耐える状態にする。

- [x] **M7-01** 再接地ジョブ — `plan(scope)` → コスト見積り → `run(plan)`、旧文は `tx_to` 閉鎖のみ、差分レポート（runbook 形式）
  - **2026-10-25 注記**: 実装されているのは `plan`（見積り）と `summarise`（差分レポート）だけで、**`run(plan)` は存在しない**。runbook の「実行」の段は `tests/integration/test_g7_regrounding.py` を走らせる指示になっており、実データは再接地されない（`docs/PROGRESS.md` 所見 110）。チェックは当時の判定として残す。実行の段を作るかどうか・どの里程で扱うかは `docs/HITL.md` H-24
- [x] **M7-02** 版ライフサイクル統合 — オントロジー版 × WM 版 × プローブ版 × 写像版の互換性行列、昇格パイプライン（影響分析 → カナリア → 再接地 → 差分レビュー）
- [x] **M7-03** 可観測性 — §12.3 の指標（WM / 接地 / 信念 / 照合 / 統合 / 運用）を metrics テーブル + Prometheus エクスポート（指標名は列挙で固定。Grafana ダッシュボードは observability profile の設定として未作成）
- [x] **M7-04** UI — 不一致キュー（証拠並置・解決入力 = 学習信号）、信念ビュー（三源 + as-of スライダ）、期待ボード、概念 curation（`apps/ui`。ADR-0008 で Vite + React、GraphQL クライアントと UI ライブラリを入れない決定）
- [x] **M7-05** runbooks — 再接地 / 版昇格 / 障害対応（ブローカー停止・グラフストア停止・ワーカー滞留）/ バックアップ・リストア
- [x] **M7-06** プライバシー・セキュリティ検証 — 潜在復元攻撃テストの雛形（陽性対照必須）、ABAC（概念型 × 様相 × 拠点、既定は拒否）の API 適用、シークレットスキャンを `make check` に組み込み
- [x] **M7-07** 統合テスト G7 — プローブ版を上げて再接地 → as-of で旧信念が再構成でき、新 tx で新信念が載ることを確認

**ゲート G7**: G6 維持 + M7-07 緑 + runbooks に従って第三者（別セッション）が再接地を実行できる。

#### ゲート G7 の実測（2026-10-03、通過）

| 条件 | 実測 | 判定 |
|---|---|---|
| G0〜G6 維持 | 統合 98 件緑（G0〜G7） | 通過 |
| M7-07 緑 | G7 統合テスト 15 件緑 | 通過 |
| 再接地で旧信念が消えない | 差分レポートに「削除は 0 件」、旧文は `tx_to` 閉鎖のみ | 通過 |
| as-of で旧版の判断が返る | `as_of=再接地前` → provenance `probe/1.0.0`、`as_of=再接地後` → `probe/2.0.0`。**両方返る** | 通過 |
| 無期限の再接地を拒否する | 開いた区間の計画は `RegroundingError` | 通過 |
| 潜在の無効化は WM 版だけ | プローブ版の昇格では潜在を再計算しない | 通過 |
| 未検証の版組み合わせを拒否 | `CompatibilityMatrix.require_verified` が拒否 | 通過 |
| 指標が 6 領域 25 指標そろう | `METRICS` に 25 件、未定義の名前は構築時に拒否 | 通過 |
| 潜在から個体を同定できない | 同型設備 24 台、状態のみの潜在で top-1 が当て推量 1/24 を超えない（陽性対照は top-1 > 0.8 で成功） | 通過 |
| シークレットが混入していない | 追跡 262 ファイルで検出 0（陽性対照として偽の鍵 3 件を検出することを確認済み） | 通過 |
| runbooks に従って第三者が再接地できる | **部分的** — runbook が参照する記号（`plan` / `run` / `RegroundingReport` 等）が `sbp.knowledge` に実在することをテストで検査した。**「別セッションの人間が読んで実行できるか」は自動では検査できない**。**2026-10-25 追記**: `run` は実在せず（検査は `plan_regrounding` / `summarise_regrounding` / `CompatibilityMatrix` だけを見ていた）、runbook の実行手順は統合テストの実行だった（所見 110） | 条件つき通過 |

**この数値が証明していることの限界。** G7 の中心である「旧版の判断が as-of で返る」は実測で
確かめたが、プローブの新旧の差は**学習量（30 エポック対 500 エポック）で付けた**。不変条件 7 が
「較正していない旧版」を作ることを拒否するためで（生スコアを信念にしない）、現実の版上げに
近い形ではあるが、「アルゴリズムを変えた版上げ」での挙動は確かめていない。

**runbook の検証可能性には限界がある。** 「第三者が runbook に従って実行できる」は、手順書の
参照先が実在することまでは機械で検査できるが、手順の分かりやすさ・前提の抜けは人が読まないと
分からない。ここを「通過」と書くときは、何を検査して何を検査していないかを区別する必要がある。

---

### M8 配線 — 残ったギャップの解消（G7 通過後）

目的: `docs/PROGRESS.md`「既知の制約と未着手事項」のうち**他の未実装に依存していたもの**を解消し、4 ワーカー（bridge / belief / reconcile / scheduler）が実物のサービス上で非ゼロの処理件数を返す状態にする。共通する形は「型と判定は実装済み、供給元と周期実行が未実装」である（PROGRESS.md「次の着手」）。

**スコープ外**: 合成データでは確かめられないもの（プローブの実性能、概念発見の非自明性、潜在復元攻撃の強さ、照合の適合率、再接地の所要時間）。これらは実データに当てるまで測れず、ユースケース検証は別リポジトリで行う（ADR-0001）。本里程の役目は「測れる形で渡す」ところまでである。

- [x] **M8-01** プローブ重みの直列化とロード — `sbp.bridge.serialization`（純粋な束ね形式。I/O を持たない）+ `sbp.knowledge.artifacts`（バイト列の置き場。ローカル FS と S3）。較正器を欠いた `active` は保存もロードも拒否する（不変条件 7）。版は明示必須（`latest` 禁止、レジストリと同じ理由） → 完了（束ね形式 1.0.0・契約テストで golden 固定、置き場は既定で上書き拒否、単体 44 件 + 契約 3 件）
- [x] **M8-02** bridge-worker のプローブ供給配線 — M8-01 の束ねを置き場から読み、`active` だけを走らせる。接地する実体の一覧の供給元を決める（ADR）。`TODO(question)` を消す → 完了（ADR-0009。`sbp.bridge.runner.ProbeSet`、`sbp.knowledge.entities.entities_of_type`、`build_worker`。プローブ IRI の組み立て（不変条件 5 違反）も除去。単体 45 件）
- [x] **M8-03** ウォーターマークの永続化 — 再起動で進んだところから再開する。投影テーブル（alembic）に逃がす。`TODO(question)` を消す → 完了（alembic 0003 `grounding_watermarks`、`sbp.knowledge.watermarks.WatermarkStore`。キーに WM 版と階層を含める。単体 5 件 + 統合 3 件）
- [x] **M8-04** 不一致イベントの送信先の決定と配線 — 中央集約か拠点別か（ADR）。`reconcile_worker.main()` の sink を埋め、`TODO(question)` を消す → 完了（ADR-0010。既定は拠点トピック、`SBP_REASONING_DISCREPANCY_SCOPE=central` で切替。`sbp.reasoning.reconciliation.notify`。単体 14 件）
- [x] **M8-05** scheduler-worker の要求供給配線 — `ObservationRequest` の供給元（イベントログ / 投影テーブル）を決め（ADR）、観測手段の割り当てを実装する。`TODO(question)` を消す → 完了（ADR-0011。要求は作業キュー（alembic 0004 `observation_requests`）、手段は `observation/*.yaml`。**要求は主張ではないのでイベントログに載せられない**——様相格子に席が無い。単体 34 件 + 統合 12 件。キューに書く側は M8-08）
- [x] **M8-06** Grafana ダッシュボード — `infra/prometheus/` と observability profile のダッシュボード JSON（6 領域 25 指標。指標名は列挙で固定済み） → 完了（ダッシュボードは **`METRICS` からの生成物**（`make gen` / `scripts/gen_dashboard.py`）。compose に prometheus + grafana（observability profile）、`sbp.knowledge.MetricStore`、API の `GET /metrics`。単体 4 件 + 契約 7 件 + 統合 7 件。**取り込み先の API を起動する経路が無い**ので M8-13 を起票）
- [x] **M8-07** `SituationLink` による実体間結合 — 同一 Situation に属する実体の結合（現在は部分−全体のみ）。Situation の所属を引くクエリを `sbp.knowledge.relations` に足す → 完了（`situation_members()`。`graph_relations` が `(PartWhole, SituationLink)` の対を返すようにし、`propagate` の `situations` に繋いだ。参加者 1 件の Situation は落とす。単体 6 件）
- [x] **M8-08** 学習信号の適用ワーカー — UI/API に入った人の判断を受けて、書き戻し・プローブ再学習・概念採択を実行する。**人の承認が前提**（自動適用しない） → 完了（ADR-0012。判断は作業キュー（alembic 0005 `learning_decisions`）で受け、**5 番目のワーカー** `learning-worker` が適用する。API の `command_handler` を配線（受け付けるだけ）。**自動適用は観測要求と既定 dry の書き戻しだけ**——信頼性更新・負例バッグ・概念候補は記録するだけ。単体 26 件 + 統合 8 件。**M8-05 のキューに書く側がこれで埋まった**）
- [ ] **M8-09** 発見ループの周期実行 — 概念発見（検出 → 提案 → curation）を周期ジョブにする。**M8-19 で作った `concept_proposals` 表に書く側**がこれである。採用後のオントロジー MINOR 起票は人の手作業のまま（意図的）。**H-10（却下した提案の再提案を抑えるか）を先に決めると手戻りが減る**——周期実行を入れると実際に繰り返しが起こる。**2026-11-07 に ADR-0019 で設計を決め、枝番に分割した**（親は枝番がすべて終わったら `[x]`）
  - [x] **M8-09-1** ADR-0019 と H-10 の暫定決定、所見 123（文書のみ。置き場は learning-worker の周期処理、運用の入口は (c) 反復する不一致だけ、再提案は signal の指紋で抑える）→ 完了（2026-11-07）
  - [ ] **M8-09-2** 解決済み不一致の読み出しと signal 源 — `DiscrepancyStore.resolved`、`resolution_signals`、`signal_fingerprint`
  - [ ] **M8-09-3** 抑制と提案の生成 — プロンプト・スキーマを運用コードへ移す、`suppressed_fingerprints`、`run_discovery`（provider 注入）
  - [ ] **M8-09-4** learning-worker の発見パス — 設定 3 件、`step()`、`build_worker` の provider 配線と `unwired`
  - [ ] **M8-09-5** 統合テスト — 解決済みの不一致 → 提案、2 周目は増えない、却下の冷却
  - [ ] **M8-09-6** レビュー・評価・記録
- [ ] **M8-10** LLM の live 経路（**H-04 の判断待ち**） — 任意依存として SDK を 1 つ追加し、`live_factory` を実装する。fixture が無いときに黙って live に落ちないことをテストで固定。**どの SDK を入れるかが決まっていない**（契約・コスト・データの所在は運用方針。`docs/HITL.md` H-04）。H-01（鍵のローテーション）とも連動する。**2026-10-25: `[~]` → `[ ]` に戻した**——`src/sbp/llm/` にプロバイダ実装は無く（`base.py` / `registry.py` / `replay.py` のみ）着手の痕跡が無いので、`[~]` のままだと P-00 の WIP 数え上げで「進行中 1 件」と誤読される。止まっている理由は「H-04 の判断待ち」で表す（§2）
- [x] **M8-12** 不一致の永続化と API 露出 — イベントは通知であって不一致そのものではない（`EventEnvelope` に全体を入れる場所が無い）。UI には不一致キューの型とロジックがあるが、API に資源が無く、知識グラフにも投影表にも不一致が書かれていない。**通知だけでは UI が中身を読めない**（ADR-0010 の未決） → 完了（ADR-0013。alembic 0006 `discrepancies`（問い合わせ列 + `payload jsonb`）、`DiscrepancyStore`、GraphQL の `discrepancies(...)`。照合ワーカーが書き、**learning-worker が本体を読む**ので M8-08 の再構成回避を解消。`status_rank` でライフサイクルの後退を SQL で弾く（所見 80）。単体 7 件 + 統合 14 件。**UI の差し替えは M8-16**）
- [x] **M8-13** `make dev` と API の実配線 — **`scripts/dev.py` が存在せず `make dev` が落ちる**（CLAUDE.md のコマンド表にあるのに）。`create_app` を実物の依存（`AssertionStore` / SPARQL / ストリーム / コマンド / ABAC の要求者 / `MetricStore`）で組む経路がどこにも無く、API はテストの中でしか起動したことがない。M8-06 の Prometheus 取り込み先もこれが無いと存在しない → 完了（`sbp.api.factory.build_app` + `scripts/dev.py`。**開発用の要求者は `SBP_ENV=dev` 限定**で、それ以外では解決器を渡さない＝全拒否。購読とコマンドは未決なので渡さない（503）。単体 15 件 + 統合 8 件。**実測**: metrics 表 → `/metrics` → Prometheus の 3 系列が取り込まれ、ラベル付きでクエリできることを確認）
- [x] **M8-14** 指標の書き込み配線 — 表・エクスポート・ダッシュボードは通ったが、**`MetricStore.write` を呼ぶ運用コードが 1 つも無い**（PROGRESS.md 所見 71）。25 指標の値は各所で計算されているのに `metrics` 表に行かない。ワーカーと再接地ジョブに書き込みを足す → 完了（`services/metrics.MetricRecorder`（溜めて周期ごとに書く）を 3 ワーカーに配線。belief: `update_latency` / `contrib_observed|record|plan`（正規化）/ `belief_entropy` / `event_log_lag`、bridge: `lifting_delay`、reconcile: `discrepancies_raised`（類型別）/ `suppression_rate`。単体 20 件。**実測**: 実物の Postgres に 6 系列が入り読み戻せることを確認。**配線済みは 25 指標中 9**（`contrib_*` を 3 と数える。2026-10-04 に走査で再確認）、**残り 16 が未配線** → M8-15
- [ ] **M8-15** 残り 16 指標の書き込み配線（**2026-10-25: M8-25-5 で `cdc_lag` / `mapping_violations` を配線し、残り 14**） — M8-14 で配線したのは 9 指標（belief 6 / grounding 1 / reconciliation 2。2026-10-04 に走査で再確認）。残りは**計算する場所が運用ループの外**にある: プローブ評価（`probe_auroc` / `probe_ece`）、WM の `health()`（`latent_shift` / `pred_error` / `align_residual` / `inference_latency`）、予測サービス（`forecast_coverage` / `forecast_crps`）、再接地ジョブ（`regrounding_duration`）、統合層（`cdc_lag` / `mapping_violations` / `reliability_updates`）、同一性（`identity_bind_rate` / `identity_revoke_rate`）、照合（`resolution_time` / `learning_yield`）。**どれも「誰が呼ぶか」が M8-08 以降の配線に依存する**
- [x] **M8-16** UI を不一致キューの資源に向ける — `apps/ui` の `fetchDiscrepancies` は `/stream/{topic}` を読み、**各イベントの `payload` が `Discrepancy` そのものだと仮定している**（成立しない。所見 62）。GraphQL の `discrepancies(...)` に向け、UI の型を API の契約に揃える → 完了（**UI と API の契約が 5 箇所ずれていた**（所見 83）: 存在しない類型 `QUANTITY` / 状態の大文字 / API が受け付けない解決語彙 / 埋め込み証拠 / 単数主体。すべて API の契約に揃え、**契約テスト 7 件**（`tests/contract/test_ui_api_contract.py`）でずれを機械検出できるようにした（陽性対照で確認）。UI 69 件緑。**三源の並置は M8-17**）
- [ ] **M8-17** 証拠の三源並置 — 設計書 §8.2 の「証拠並置」を実現する。必要なのは (a) アサーション ID で信念を引く API（`AssertionStore.query` は主語・述語でしか絞れない。ADR-0013 の未決）、(b) **来歴の `contrib` から源を判定する経路**——業務記録由来の信念も推定を経ると `inferred` になるので、**三源は様相では分かれない**（所見 84）。つまり**並置の単位は「様相」ではなく「寄与」**である。ここを間違えると成立しない並置を作る。いまの UI は参照 IRI の一覧までを出している
- [x] **M8-18** `Expectation` の永続化と API 資源 — UI の型は `{expectationId, status(大文字), actionType, subject, deadline, …}` だが backend は別物。**不一致と同じずれ**（所見 86）。M8-12 と同じ形（作業項目表 + GraphQL 資源）で揃える → 完了（alembic 0007 `expectations`、`ExpectationStore`、`expectation_store.to_record/from_record`、GraphQL の `expectations(...)`、UI の型合わせ。`ExpectationStatus.rank` で後退を SQL で弾く。期限は効果の最小値を列に出す（`Created` は `None`）。**UI はストリームを 1 本も読まなくなり `VITE_SBP_SITE_ID` が不要になった**（所見 88）。単体 8 件 + 契約 3 件 + 統合 11 件）
- [x] **M8-19** `ConceptProposal` の永続化と API 資源 — **トピックも資源も無かった**（UI は存在しない `central.reasoning.concept` を読んでいた。所見 85）→ 完了（ADR-0015。alembic 0009 `concept_proposals`、`ConceptProposalStore`、`concept_store.to_record/from_record`、GraphQL の `proposals(...)`、UI の型合わせ。**潜在の重心を表に入れない**（潜在索引のもので、置くと再接地で食い違う）代わりに**判断材料 2 つ**（`support_count` / `coverage`）を書き込み時に導出して列に出す——UI の curation 検査（§11.2）が使い、読み出しでは計算できない。**往復が完全でないことをテストで固定**（所見 90 の反省）。`_apply_curation` が**提案の状態を進める**ようにした——初版は記録するだけで、同じ提案が永久に画面に残っていた。`curate` の rank 比較が `<=` で二重判断を許していたのを統合テストが検出（所見 96）。**UI はストリームを 1 本も読まなくなった**（契約テストで固定）。単体 15 件 + 契約 4 件 + 統合 11 件）
- [x] **M8-20** 契約 → `Expectation` の変換を production に出す — 契約 + `ActionEvent` → `Expectation` を作るコードが**テストの中にしかなかった**（`tests/integration/test_g5_closed_loop.py` がインラインで組んでいた。所見 89）。**実行経路の配線は M8-21 に分けた**（1 日粒度を超える。§2） → 完了（`sbp.actions.expectations.expectation_for` / `expected_effect_for`。**区間は契約の境界から作る**（`belief_at_least` → `[threshold, 1.0]`、期待値は境界そのもの＝契約が満たされる最弱の値）。業務効果の下端は `Settings.actions.record_effect_floor`（**関数に既定を埋めない**——二重定義になる）。重複した `planned_expectation_deadlines` を削除（所見 91）。G5 の fixture を production の関数に差し替えた。単体 11 件）
- [x] **M8-21** アクション実行の配線（M8-20 から分割） — 実行自体が API コマンドとして未配線だった。前提検証 → 承認 → 実行 → `ActionEvent` → `Expectation` 永続化を運用経路に出す → 完了（ADR-0014。実行は**判断キューを通る**（alembic 0008 で `kind='action'` を追加）——承認は判断であり、実行はその適用である。`ExecutionCommand` は `sbp.actions.requests`（**`sbp.api` に置くとワーカーが API を import することになる**。import-linter が落ちた）。`sbp.knowledge.current_beliefs` で前提の物理面を引く（様相は `inferred` に絞る——`planned` を混ぜると安全ガードが計画の追認になる）。**冪等の鍵を `decision_id` から導く**（既定の `uuid4` では再試行で別の命令になる。所見 93）。単体 14 件 + 統合 3 件）
- [ ] **M8-22** UI からアクションを承認する経路 — API に `/commands/execute_action` が立ったが、**UI に押す場所が無い**。不一致キューから「緊急点検を起票する」を承認する画面と、落ちた理由（前提未達・レート制限）を人に返す経路が要る。ADR-0014 の未決 3 つ（`docs/HITL.md` の **H-06** レート制限の永続化 / **H-07** `ActionEvent` の永続化 / **H-08** 補償の入口）も合わせて検討する

以下 M8-23〜M8-29 は 2026-10-17 の棚卸しで起票した。設計書 §2.5 の三つのデータパスを `services/` と
`scripts/` から辿り直したところ、**各パスの先頭を運用で書くコードが無かった**（`docs/PROGRESS.md`
所見 101〜104）。どれも M8 の目的「供給元と周期実行を埋める」の範囲だが、**M8-11 の前提にするかは
H-22 の決定次第**である。

- [ ] **M8-23** プローブ束を置き場に入れる経路 — 学習 → 評価 → `promote()`（AND ゲート）→ `bundle_probe` → `ArtifactStore.put` を運用の手順（`scripts/` のコマンド）にする。**いまは `put` を呼ぶ運用コードが無く、新規環境では bridge-worker が構築で落ちる**（空の束は拒否、ADR-0009）。学習の弱ラベルは記録由来（`TimeBag`）なので M8-25 に依存する。スクリプトから真値を読まない（不変条件 10。import-linter の対象に `scripts` が入っている）
- [ ] **M8-24** 推定パスの先頭（**ADR 先行**） — 観測窓 → `WorldModel.encode` → `LatentIndex.write` を運用で回す。**いまは潜在索引に書くコードがテストにしか無い**（`test_bridge_worker_e2e` / `test_g4_worldmodel`）。エンコーダをどこで動かすか（6 番目のワーカー / bridge-worker の前段 / 本リポジトリ外のエッジ。設計書 §12.1 はエッジにエンコーダを置く）を ADR で決める。前提として WM 本体の重みの直列化（PROGRESS 既知の制約）と観測の入口の契約が要る。1 日を超えるなら分割する
- [x] **M8-25** 記録パス・計画・資産台帳の周期実行（**ADR 先行**） — `Connector.pull(since)` → 写像 → `IngestionPipeline` → `<site>.integration.business`、`PlanItem` → `planned`、資産台帳の同期（設計書 §7.1 の「マスタ 定期同期」）を運用で回す。**いまは `IngestionPipeline` を呼ぶのが G1 の統合テストだけで、`make seed` はオントロジーしか入れない**——新規環境では `entities_of_type` が 0 件を返し、reconcile-worker の記録側の入力は空のまま。置き場（ワーカーかジョブか）を ADR で決める。**2026-10-25 追記**: 閾値・ワーカー設定を `.env` で拠点別に与えるには X-11 が先に要る（所見 108。2026-10-25 に解消）。資産台帳を同期すると、照合ワーカーが実体の型を引けるようになり業務価値が効き始める（M8-30 で配線済み、所見 113）。**2026-10-25 に ADR-0016 で設計を決め、1 日粒度に分割した**（P-11）。計画中に、取込を周期実行しても照合ワーカーには何も届かないことが分かった——取込イベントは記録型・`p = 1.0`（存在）で、照合ワーカーはフルーエント型・記録由来の確率（主張）を読む（所見 115）。親タスクは枝番がすべて終わったら `[x]` にする
  - [x] **M8-25-1** ADR-0016 と所見 115 → 完了（2026-10-25。取込は 6 番目のワーカー `ingest-worker`、記録の主張を写像 DSL の `claims` から主張イベントにする、資産台帳はアサーション + 投影グラフ `urn:sbp:g:master:<site>`（利用者が選択）、決定的な ID とカーソルで冪等、1 周の順序は 台帳 → 解決器 → 業務記録 → 計画）
  - [x] **M8-25-2** オントロジー 0.6.0（`ent:AssetRegisterEntry ⊑ sbp:Record`）と台帳の入口 — オントロジー変更手順を先に回す（TTL → CHANGELOG → `make gen`（生成差分は別コミット）→ `make ontology-check`）。モックに `asset_register` 表と台本の行、`mappings/asset_register.yaml`、信頼性モデル `(CmmsSource, AssetRegisterEntry)` → 完了（2026-10-25。オントロジー → 生成物 → コードの 3 コミット。台本は LINE-3 / P-101 / P-102（ポンプ 2 台がラインの部分）。ラインも資産タグの IRI（`…/asset/LINE-3`）にした——既存のテストと同じ規約で、業務価値は型で引くので経路の名前は効かない。写像の数を固定していた契約テストを 5 本に直した。テストに直書きされた `0.5.0` はデータとしてのリテラルで、版上げで落ちるものは無かった）
  - [x] **M8-25-3** 記録の主張イベントと決定的 ID — 写像 DSL の `claims`（フルーエントの実在とフィールドの存在を読み込み時に検査）、`IngestionPipeline` の主張イベント（`p = σ(log_likelihood_ratio)`）、決定的な UUIDv7（`sbp.core.ids`、性質テスト）。**不具合修正**: `reliability.for_record` が行ごとの `try` の外にあり、1 行の未登録で一括が落ちる（再現テスト先行） → 完了（2026-10-25。`Mapping.claims` / `claims_of`（真偽として読めない値は推測せず `MappingError`）、`operation_log.running → ent:Running`、主張イベントの型は `<core#BusinessEvent>/<フルーエント型>`・様相 `observed`・`p = σ(対数尤度比)`（MES の稼働 ≈ 0.79、停止 ≈ 0.06）・有効時間の不確実幅は情報源の時刻ずれの標準偏差。`sbp.core.ids.stable_event_id`（時刻部 = 記録時刻、乱数部 = 鍵の SHA-256）で、鍵は `拠点 | 情報源 | 表 | 外部 ID | updated_at`——**拠点を入れた**（冪等記録の鍵は `(consumer_group, event_id)` なので、拠点が違う同じ記録が弾かれる）。**「不具合修正」は取りやめた**: 既存の単体テスト `test_unknown_source_has_no_reliability_model` が「信頼性モデルの無い情報源は一括で例外」を意図として固定しており、行の誤りではなく設定の誤りとして大きく落とす挙動だった。代わりに「主張を宣言した写像は混同行列を持つ信頼性モデルと対になる」を契約テストで静的に検査する。照合・信念ワーカーの `fluent_of_event` が主張イベントからフルーエントを取り出せることを単体テストで固定（所見 115 の段間の契約）。operation_log のイベント数を 1 と固定していた既存テスト 3 か所（単体 2・G1 1）を 2 に直した。性質 4 件・単体 18 件・契約 1 件）
  - [x] **M8-25-4** 資産台帳の同期と計画の `planned` 化 — `sbp.integration.master.MasterDataSync`（台帳の差分 → アサーションの追記と `tx_to` の閉鎖）、`sbp.knowledge` の投影グラフ、投影から読む解決器、計画種 → フルーエントの対応表と決定的な `assertion_id`・置き換え → 完了（2026-10-25。`MasterDataSync.sync` は台帳の**全行**を受け取り（差分の取込では消えた行が分からない）、台帳の来歴を持つ現在の文と突き合わせて追記・`tx_to` の閉鎖をする。冪等は決定的 ID ではなく**差分で**保つ（同じ台帳の再同期は追記 0）。型名がオントロジーに無い行は推測せず `skipped`。投影は `sbp.knowledge.rebuild_master_projection` が `urn:sbp:g:master:<site>` を**1 つの更新要求で**作り直す（削除と追加の間に空の台帳を読ませない）。`AssertionStore.query` の拠点スコープ（`:<site>:`）に入らない名前にした。**解決器は新しく作らなかった**——同期結果の資産タグを既存の `MasterDataResolver.register` に渡せば足りる（毎周期全行を読むので投影を読み直すのと同じ情報になる）。計画: `INTENDED_FLUENTS`（遵守率の表の隣）と `intended_fluent_for`（未知の計画種は `None`）、来歴を計画 ID の uuid5 から導く `plan_provenance`（`plan_item_id` は uuid4 で読み直すたびに変わっていた）、`PlanSync`（同じ計画は `unchanged`、変わったら旧文を閉じて `replaced`）。plans.py の docstring の層の向きを直した。単体 15 件）
  - [x] **M8-25-5** 取込カーソル（alembic 0010 `ingest_cursors`）と `services/ingest_worker.py` — `IntegrationSettings`、`.env.example`、`scripts/dev.py` の `WORKERS`、ワーカー一覧の文書（CLAUDE.md は X-15 のブランチに足す） → 完了（2026-10-25。`IngestWorker.step` は 台帳（全行、`asyncio.to_thread` で同期）→ 資産タグを解決器に登録 → 業務記録の表を `(情報源, 表)` のカーソルから差分で読み、送信（`send_and_wait`）してからカーソルを進める → 計画の表は `PlanSync`。カーソルは読んだ行の `updated_at` の最大で、1 行でも読めなければ進めない（推測で進めない）。送り先が無いときもカーソルを進めない。渡されなかった依存は `unwired`（M8-30 の形）。指標 `cdc_lag` / `mapping_violations` を書く（M8-15 の残りが 16 → 14）。`IntegrationSettings`（`SBP_INTEGRATION_POLL_PERIOD_S` / `SBP_INTEGRATION_MOCK_DB_PATH`）は `_DotenvSettings` を継承——**X-11 と X-13 で入れた検査が、層の一覧と `.env.example` の更新漏れをその場で落とした**。主張イベントが送信前の JSON Schema 検証を通ることを確認。単体 9 件 + 設定の検査 2 件の更新）
  - [x] **M8-25-6** 統合テスト — 外界（SQLite のモック）→ ingest-worker → `integration.business` / 知識グラフ → 照合ワーカーの記録列。2 周目は何も増えない、送信が失敗したらカーソルが進まない → 完了（2026-10-25。`tests/integration/test_ingest_worker_e2e.py` 2 件。テストが置くのは外界（SQLite に §14 の台本）と、照合の窓に要る物理側のイベント（推定パスの先頭は M8-24）だけで、ingest-worker と reconcile-worker は production の `build_worker`。確かめたこと: 業務トピックに存在 4 + 主張 1（p ≈ 0.79）、`entities_of_type(Asset)` と `part_whole_edges` が台帳の設備を返す、`planned` の Running(P-101)、カーソルが送信の後に進む、2 周目は 0、照合ワーカーが主張イベントを記録列に入れる。送信失敗でカーソルが進まないことは単体で固定。**統合テストが不具合を 1 つ見つけた**——完全 IRI のイベント型（記録の存在イベント）が `fluent_of_event` でフルーエントに化け、照合ワーカーの窓が 5 個できていた。信念ワーカーなら述語が無いと例外で落ちる（M8-26 で業務トピックを購読した瞬間に落ちるところだった）。再現テスト先行で `fluent_of_event` を直し（頭が既知の事象クラスで末尾が FluentType のときだけ）、統合テストに「窓は 1 つ」を足した（所見 116）。統合 190 件緑）
- [x] **M8-26** 信念更新を三源にする — belief-worker が `integration.business` と計画を読み、`BeliefFilter.step(records=…, plan=…)` に渡す。記録尤度に `AuditMask` を適用する（不変条件 6）。**いまは `bridge.physical` だけを購読し、全イベントをプローブ確率として渡す一源**で、`contrib_record` / `contrib_plan` は構造的に 0（所見 102）。docstring（「業務記録・計画を読み」）も実装に合わせる。M8-25 に依存。**2026-10-25 追記**: `BeliefIngest` には `reliability` も渡していない（`None`。所見 109）ので、記録尤度を入れるときに `reliability/*.yaml` を読んで渡す → 完了（2026-11-07。belief-worker が `bridge.physical` と `integration.business` の 2 トピックを購読する。取込の主張イベント（`BusinessEvent/<fluent>`、p は取込が信頼性モデルを通した σ(LLR)）は `BeliefFilter.step(claim_probabilities=…)` の記録尤度に、`planned` の文は `GraphPlans`（周期ごとに読み直す）経由で事前に入る。**`reliability` は渡さない**（所見 109 は、信頼性を取込側で適用済みのため belief 側では不要と整理）。計画は `PlanView` Protocol にして `PlanItem` 全体を要求しない。主張は観測の時刻順検査に数えない（業務記録は計上が遅れて届く）。監査隔離は主張イベントの `RecordEvidence` に `BeliefIngest` が適用（単体で固定）。単体 22 件 + 統合 1 件（ingest-worker → 業務トピック → production の belief-worker で P-101 の `inferred` が p>0.5、記録の存在イベントを読み飛ばして落ちない））
- [x] **M8-27** 期待の周期検証 — `verify_expectation` を期限と証拠の到着で回し、`expectations` 表の状態を進める。`Violated` は不一致（類型 2 / 3）の起票、`Undetermined` は観測要求（設計書 §8.5）。**いまは learning-worker が `Active` で書くだけで、検証する運用コードが無い**（所見 103）。置き場は ADR-0012 / ADR-0014 の延長で決める。**2026-11-07 に ADR-0017 で設計を決め、枝番に分割した**（親は枝番がすべて終わったら `[x]`）
  - [x] **M8-27-1** ADR-0017 と所見 118（文書のみ。置き場は learning-worker の周期処理、検証は期限切れのみ、記録の存在は投影表 `business_records`） → 完了（2026-11-07）
  - [x] **M8-27-2** 記録の存在の投影 — alembic 0011、`sbp.knowledge.RecordIndex`、ingest-worker が送信後に書く → 完了（2026-11-07。`business_records` 表（PK = 拠点・記録型・外部 ID・記録時刻、検索索引）、`RecordIndex.add`（冪等、対象未解決は入れない）/ `present_types(entity, since)`。ingest-worker は送信の後・カーソルの前に書き、`unwired` に `records` を足した。単体 2 件 + 統合 3 件）
  - [x] **M8-27-3** 検証の純粋な部分 — `ExpectationStore.overdue_active`、`sbp.reasoning.expectation_runner`（期限切れ判定・入力の組み立て・結果 → 観測要求 / 起票対象） → 完了（2026-11-07。`overdue_active(at)`（Active かつ期限 < at、期限の古い順）、`verify_if_overdue`（期限前・Active 以外は `None`。記号側の「無い」は違反、物理側の「無い」は観測不能 → 観測要求。違反は類型 1 / 2 の不一致、ID は期待から決まる）。**検証器の不具合を 2 つ直した**（所見 119。再現テスト先行）。単体 11 + 2 件、統合 1 件）
  - [x] **M8-27-4** learning-worker の検証パス — 検証周期の設定、`step()`、`build_worker` の配線と `unwired` → 完了（2026-11-07。`SBP_REASONING_EXPECTATION_VERIFY_PERIOD_S`（既定 60 秒、`.env.example` 同時更新）、`ExpectationVerification`（依存の束）、`step()` = 判断の適用 + 期限切れの検証。書く順序は 不一致・観測要求 → 期待の状態（失敗したら `Active` のまま再試行）。深刻度は対象の型の業務価値（型が引けなければ実体自身を型とみなす照合と同じ規約）。信念・期待・観測要求のどれかが無ければ検証しない（`verification_unwired`）。単体 10 件）
  - [x] **M8-27-5** 統合テスト — 期限切れの `Active` 3 本（満たす / 外れる / 観測なし）を production の `build_worker` で検証、2 周目は何も変わらない → 完了（2026-11-07。`tests/integration/test_expectation_verification_e2e.py`。テストが置くのは外界（期待 3 本・信念・記録の存在）だけで、learning-worker は production の `build_worker`。満たす → `Fulfilled`（副作用なし）、記録なし → `Violated` + 類型 1 の不一致、信念なし → `Undetermined` + 観測要求、2 周目は 0。統合 196 件緑）
  - [x] **M8-27-6** レビュー・評価・記録 — 独立レビュー、全件テスト、TASK / STATUS / README / PROGRESS / HITL → 完了（2026-11-07。独立レビュー（別のサブエージェント）が 6 件を指摘し、実コードで確かめて全部直した（所見 120）。最重要は `current_beliefs` が確信度の最大を採っていた件（M8-21 からの不具合）。`make check` 1572 件・統合 196 件緑）
- [x] **M8-28** Situation の参加関係を `hasParticipant` で読む — `sbp.knowledge.relations.situation_members` は `<実体> sbp:inSituation <Situation>` を読むが、オントロジーの `inSituation` は**定義域が `Assertion`**（文の所属）で、`AssertionStore` は reifier にこれを書く。参加は `sbp:hasParticipant`（Situation → PhysicalEntity、設計書 §3.4。定義済みで未使用）。読み出しと単体テストの fixture を直す（所見 104）。オントロジー変更は不要。書く側（照合・説明）は別タスク → 完了（2026-10-25。`situation_members` を `<Situation> sbp:hasParticipant <実体>` で読むように直し、`SituationLink` の docstring も揃えた。単体テストの fixture を正しい向きにし、**文の所属（reifier の `inSituation`）を参加者として返さない**回帰テストを足した（修正前は reifier が参加者として返り赤）。単体 1 件追加。運用に参加関係を書く者はまだいないので、結合の入力が空であることは変わらない）
- [x] **M8-29** 人の確認回答を `observed` で戻す経路 — 設計書 §14 の手順 4（人の回答が最上位の事実性を持ち、記録訂正と信頼性更新に変わる）。スケジューラが選んだ「人への質問」の回答を API コマンドで受け、`observed` のイベントにして観測要求を充足する。**いまは回答の入口が無い**（コマンドは `resolve_discrepancy` / `curate_concept` / `execute_action` の 3 つ）。**H-13**（充足の判定）と合わせて設計する。**2026-11-07 に ADR-0018 で設計を決め、枝番に分割した**（親は枝番がすべて終わったら `[x]`）
  - [x] **M8-29-1** ADR-0018 と H-13 の注記、所見 121（文書のみ。入口は 4 つ目のコマンド `answer_observation`、回答は `observed` の主張イベントにして業務トピックへ、要求の充足は鍵の一致）→ 完了（2026-11-07）
  - [x] **M8-29-2** コマンドと種別 — `AnswerCommand`、`DecisionKind.ANSWER`、alembic 0012、`ObservationQueue.get` → 完了（2026-11-07。`answer_observation` コマンド（`discrepancy_id` は UUID に限らない文字列、`state` は厳密な真偽、確信度は引数にしない）、`DecisionKind.ANSWER`、alembic 0012、`ObservationQueue.get`（満たされた要求も返す）。単体 6 件 + 統合 2 件）
  - [x] **M8-29-3** 回答イベントと適用 — `sbp.integration.human_answer`、`human_answer_confidence`、learning-worker の `_apply_answer` と配線 → 完了（2026-11-07。`sbp.integration.human_answer`（`answer_event` は取込の主張と同形の `observed` イベント、`event_id` は判断から決定的、確信度は 0.5 超 1 未満を強制、FluentType 以外は拒否）、`SBP_REASONING_HUMAN_ANSWER_CONFIDENCE`（0.98）/ `_UNCERTAINTY_S`（300）、learning-worker の `_apply_answer`（送信 → 充足。未知の要求・実体の食い違い・配線なしは失敗）、`AnswerPath`、`aclose()`。**コマンドの型 `AnswerCommand` は `sbp.integration` に置いた**（import-linter が services → api を禁じるため。api が再輸出）。単体 23 件）
  - [x] **M8-29-4** 統合テスト — API ハンドラ → キュー → learning-worker → 業務トピック → belief-worker → 完了（2026-11-07。`tests/integration/test_human_answer_e2e.py` 2 件。テストが置くのは外界（人のコマンド）だけで、learning-worker / belief-worker は production の `build_worker`。「稼働している」の回答が `inferred` の信念を 0.9 超に引き上げ、観測要求が満たされ、再送は 1 回だけ積まれる。存在しない要求への回答は失敗として残りイベントを送らない）
  - [x] **M8-29-5** レビュー・評価・記録 → 完了（2026-11-07。独立レビュー（別のサブエージェント）は重大な不具合なし。指摘 5 件のうち、認証の欠落（コマンド API は他の 3 つと同じく `answered_by` が自称。**H-25 を起票**、ADR-0018 の未決に追記）、組み立て失敗時の producer の取りこぼし、docstring 2 件（再投入は人の操作・`mark_satisfied` の呼び手）、送信後の充足失敗のテストを直した。`make check` 1604 件・統合 200 件緑）

以下 M8-30 は 2026-10-25 の棚卸しで起票した。10-17 の「この段の入力を誰が書くか」に対し、
「production が組むとき何を渡していないか」を問うて見つかった（`docs/PROGRESS.md` 所見 109）。

- [x] **M8-30** 照合ワーカーに信頼性モデル・業務価値・記録を渡す — `services/reconcile_worker.build_worker` は `reliability` も `values` も渡さず、構築子の既定（空の `ReliabilityRegistry` / `BusinessValueRegistry`）で組まれる。`reliability/*.yaml` と `value/*.yaml` は運用のどこからも読まれない。さらに周期処理が `reconcile()` に `records` を渡さないので、**抑制の判定は一度も走らず（`suppression_rate` は構造的に 0）、深刻度の業務価値項は全件が中間値になる**。(a) 両レジストリを `from_directory` で読んで渡す（M8-25 を待たずにできる）、(b) 窓に溜めた記録を証拠 IRI → `Record` の写像にして `reconcile(records=…)` に渡す（M8-25 に依存）、(c) 渡さなかった場合は `driver` / `sink` と同じく警告ログを出す。統合テストで「系統誤差で説明できる不一致が運用の配線で抑制される」ことを固定する → 完了（2026-10-25。**(b) と抑制の統合テストは外した**——照合ワーカーが回す類型 2 を抑制できる系統誤差は `template_copy` だけで、`SystematicBias.explains` はそれに対して常に偽を返すので、記録を渡しても抑制は起こらない（所見 113）。記録を抑制判定に渡す配線は、類型 1・3 を照合ワーカーで回すとき（窓が `Record` を持つように設計し直すとき）に扱う。**代わりに見つかった欠落を埋めた**: `build_worker` は実体の型（`entity_types`）も渡しておらず、レジストリを読むだけでは全件が基準額の半分のままだった。(a) `load_registries` が `value/` と `reliability/` を読み（基準額は `SBP_REASONING_SEVERITY_VALUE_SCALE`）、`entity_types_provider` が実体の型を知識グラフから毎周期引く（`sbp.knowledge.most_specific_types`——個体が当たる登録型のうち最も具体的なもの。`Pump` と上位の `RotatingEquipment` の両方が登録されているため）。照合する列が無い周期はグラフを読まない。(c) 渡されなかった依存は `ReconcileWorker.unwired` に数えて `worker.registries_not_wired` で警告する。readiness に oxigraph を足した。単体 6 件（entities）+ 11 件（照合ワーカー。実際の `ontology/` と `value/` の組で `Pump` に解決されることを含む））
- [ ] **M8-11** 統合テスト G8 — 4 ワーカーを実物のサービス上で回し、各 `step()` が非ゼロを返すことを確認する。**入力（潜在・プローブ束・業務記録・実体の登録・判断）をテストが直接置いてよいかは H-22 の判断待ち**

**ゲート G8**: G0〜G7 維持 + M8-11 緑 + `TODO(question)` が 0 件（解決した ADR へのリンク、または「意図的に残す理由」が PROGRESS.md に記録されている）。

#### ゲート G8 の現在地（2026-10-25 再測定）

2026-10-04 以降コードの変更は無い（HEAD は `f05e141`。2026-10-17 と本日の文書の更新は未コミット）。
数値は再測定で同じ値を得た。**統合テストは本日実際に実行した**（2026-10-17 は件数の収集だけだった）。

| 条件 | 実測 | 判定 |
|---|---|---|
| G0〜G7 を維持 | `make check` 緑（2026-10-25 の棚卸し時点で単体 1332 / 性質 21 / 契約 67 = 1420、41.5 秒）。**統合 187 件緑（実行、12.8 秒）**。UI 77 件緑（tsc + vitest）。**同日のタスク実行（X-11〜X-14、M8-28、M8-30、M8-25）の後も `make check` 1514 件（単体 1421 / 性質 25 / 契約 68）・統合 190 件が緑** | 満たしている |
| `TODO(question)` が 0 件 | 再走査でコード（src / services / scripts / tests / ontology / infra / apps）に 0 件。文書中の出現は規約の説明と解決済みの記録だけ。M8-02〜M8-05 の 4 件は ADR-0009〜0012 に解決先が書かれている | 満たしている |
| M8-11 緑 | 未着手 | **未達** |

**G8 の条件の字面には穴がある。** 「4 ワーカーが実物のサービス上で非ゼロ」は、潜在・プローブ束・
業務記録・判断をテストが直接置けば、いまの実装のまま書ける。しかし運用のコードには各データパスの
先頭が無く（所見 101）、そのまま通すと G5 の「完走させたのはテストコード」（所見 89）を繰り返す。
**入力を誰が置いてよいかを `docs/HITL.md` H-22 で人に返した**。推奨は「外界（物理の観測・業務
システム・人の操作）だけをテストが模擬し、基盤の内側の段は運用コードで通す」で、その場合
M8-23 / M8-24 / M8-25 が M8-11 の前提になる。なお G8 が名指しするのは 4 ワーカーで、5 番目の
learning-worker（ADR-0012）は条件に入っていない。

**M8 の残り 11 件**: M8-09（発見ループの周期実行）/ M8-10（LLM の live 経路。**H-04 の判断待ち**）/
M8-15（残り 14 指標）/ M8-17（三源並置）/ M8-22（UI からの承認）/ M8-23（プローブ束の投入）/
M8-24（推定パスの先頭。ADR 先行）/ M8-26（三源の信念更新）/
M8-27（期待の周期検証）/ M8-29（人の確認回答）/ M8-11（G8 の統合テスト。**H-22 の判断待ち**）。

**M8 の前に片付けるもの**（2026-10-25 追加。いずれも小さい）: 未コミットの棚卸しのコミット（所見 107）→
X-10（ブランチを main に統合）→ X-11（入れ子の設定が `.env` を読まない）/ X-12（`make up` の
`$(comma)`）。X-11 / X-12 はバグ修正なので再現テストを先に書く（§1.1、CLAUDE.md）。

**着手順の推奨**（2026-11-07 改訂。M8-26 / M8-27 / M8-29 は完了）: M8-23 → M8-09 →
M8-17 → M8-22 → M8-15 → M8-24 → M8-11。

- **M8-28 を先頭に置いた**（2026-10-25 完了）のは小さく、Situation の所属を書く側が現れる前に直さないと、書く側が誤った
  述語に合わせてしまうため。
- **M8-25（完了）→ M8-26** の順は依存の向き（記録が流れないと三源にできない）。M8-26 の入力（主張イベントと
  `planned` の文）は揃っている。**M8-23 は M8-27 の後にした**——弱ラベルの出所（記録）は運用に乗ったが、学習に要る
  潜在が運用に無い（M8-24）ので、運用の手順としては潜在がある環境でしか通らない。
- 旧推奨（M8-09 → M8-17 → M8-22 → M8-15）より前に新タスクを置いたのは、**新タスクが無いと連鎖が
  始まらず、M8-09・M8-17・M8-15 が読む入力も空のまま**だからである。
- M8-24 は ADR でエンコーダの置き場を決めるまで形が決まらない（本リポジトリ外のエッジと決めれば、
  入口の契約だけになる）。
- M8-10 は H-04 が決まるまで着手できない（どの SDK を任意依存に入れるかが決まらない）。M8-09 の前に
  H-10（却下した提案の再提案を抑えるか）を決めると手戻りが減る——周期実行を入れると実際に繰り返しが
  起こる。

**M8 の目的の外にある運用の穴（タスク未起票。扱う里程は `docs/HITL.md` H-24 の判断待ち）**: 再接地の
実行（`run(plan)` が無い。所見 110）、予測・What-if・説明の入口（GraphQL / コマンド / 周期ジョブのいずれも
無い）、LLM を使う機能の入口（概念提案・文書抽出・NL→クエリ・説明の文章化。運用コードは `sbp.llm` を
import しない）、SHACL 検証・同一性束縛・空間束縛の運用経路（`quarantine` 表に書く者がいない）。どれも
ライブラリと統合テストはあり G5〜G7 で「組めること」は確かめたが、運用で呼ぶ者がいない。H-24 が決まったら
該当する里程にタスクとして起票する（スコープ変更なので ADR を伴う。§2）。

---

## 4. 横断・継続タスク（マイルストーンに属さない）

- [x] **X-01** CI 整備 — `.github/workflows/ci.yml`。3 ジョブ（`check` / `integration` / `ui`）。`check` が落ちたら `integration` を走らせない（型が通らないのに Compose の起動を待っても情報が増えない）。`integration` は `.env.example` の既定ポートを使い、**healthy の期待件数を明示して待つ**——「healthy でないものが 0 件」を成功条件にすると、コンテナが 1 つも起きていないときも通る
  - **2026-10-17 追記**: X-05 で `audit` ジョブが足されて **4 ジョブ**（check / integration / audit / ui）になっている。**`git remote` が空なので、このワークフローは一度も実行されていない**——`[x]` は「CI の定義を書いた」までの意味で、GitHub のランナー（amd64）上で緑になったことは観測されていない（所見 105）。実行場所は `docs/HITL.md` H-23
- [x] **X-02** import 境界 lint — import-linter の契約 7 本（`make check` の `deps-check`）。層依存の向き、オラクル境界（不変条件 10）、LLM SDK、LLM のホットパス（不変条件 11）、worldmodel の記号非決定、bridge の非永続化、ワーカーの API 非経由。**`root_packages` に `services` / `scripts` を入れた**——`sbp` だけを見ていると、実運用で動くワーカーと投入スクリプトからオラクルを読む経路が検査されない。各契約が実際に落ちることを違反を仕込んで確認済み
- [ ] **X-03** ADR の継続運用 — 設計逸脱・閾値変更・ライブラリ選定のたびに `docs/adr/NNNN-*.md`
- [ ] **X-04** `docs/STATUS.md` 更新 — ゲート通過時のみ（§2 の更新手順）
- [x] **X-05** 依存更新・脆弱性対応 — `make audit`（pip-audit）と CI の専用ジョブを用意し、2026-10-03 に実施。`uv lock --upgrade` は差分なし（既に最新解）、既知の脆弱性 0 件。**`make check` には入れない**（脆弱性 DB の照会にネットワークが要り、`make check` は無ネットワークで通る方針）。`--strict` は使えないので（sbp 自身が editable で必ず skip される）、**skip されたのが sbp だけであることを検査する**——skip を数えずに「脆弱性なし」と読むと、照会できなかったパッケージが緑に見える。npm 側（`make ui-audit`）では **5 件の実在の脆弱性を検出**（critical 1 / high 1 / moderate 3。すべて開発ツール側）し、`vitest` を 5 系に上げて 0 件にした。以後は月次で `make audit` と `make ui-audit`。コンテナイメージは未照会
- [ ] **X-06** development-rules.md / 本書の保守 — 規約・計画の変更はコード PR と同じレビューを通す
- [ ] **X-07** ドキュメントの数値の自動検査 — 2026-10-04 の棚卸しで、ドキュメントの数値が実装から
  **静かに乖離していた**ことが分かった（所見 99）: `docs/PROGRESS.md` のテスト件数は 1151 / 108 / 61
  （実際は 1420 / 187 / 77）、ADR 件数は同じ節の中で「7 件」と「6 件」の両方が書かれていた（実際は 15）、
  M8 の行が無い。**人が書いた数値は人が直すまで古い。** 契約テストで検査できるものを検査する:
  ADR のファイル数、マイグレーションの連番の連続性、ワーカーの数、`METRICS` の件数と配線済みの数、
  `docs/HITL.md` が参照する ADR 番号の実在。**テスト件数そのものは検査しない**（テストを足すたびに
  ドキュメントが落ちるのは本末転倒なので、「どこに書くか」を 1 箇所に絞るほうが先）
- [ ] **X-08** `docs/HITL.md` の棚卸し — ゲート通過時と §7 を触るとき。決定済みの項目を §4 に移し、
  ADR 起票を確認してから消す。**ADR の「未決」は書き換えない**（決定の記録なので履歴として残す）
- [x] **X-09** 規約文書を ADR に追従させる — ADR-0003 / 0005 / 0006 / 0007 が「影響」に挙げた文書更新の
  うち、次が未了（所見 106）: `docs/development-rules.md` §3・§6.2・§6.3・§6.5・§6.6（WM Protocol の
  `probe` / `explain`、`minio`、RML、RDF-star）、`.claude/rules/bridge-worldmodel.md`（`probe` / `explain`、
  `minio://models/...`）、`.claude/rules/knowledge.md`（`latent_index_v<N>` → 実装は `latent_index_d<次元>`
  + 登録表）、`.claude/rules/infra-testing.md` と `docs/runbooks/local-setup.md`（ポート退避の置き場、所見 60）、
  `docs/development-rules.md` §2.1 と `README.md`（ルートの `settings.json` へのシンボリックリンクは**存在しない**）、
  `README.md` と `docs/HITL.md` §4 の「CI 3 ジョブ」（実際は 4）。**層別ルールは該当ファイルを開くと自動で
  読み込まれるので、古いルールは次のセッションに古い指示として届く。** X-07 と合わせ、ADR の「影響」に
  書いた追従を検査できる形を検討する
  - **2026-10-25 再確認**: 上記はすべて未修正（HITL.md §4 の「4 ジョブ」だけは 10-17 の未コミットの変更で
    直っている。TASK.md X-01 の本文は「3 ジョブ」のまま注記で補っている）。加えて **設計書 §4.3** が
    `probe` / `explain` を持ち ADR-0007 への参照が無い（ADR-0007 の帰結が求めている。所見 106 の「設計書は
    更新済み」は誤りだった、所見 112）。`infra/sql/versions/0001_initial_projections.py` のコメントも
    `latent_index_v<N>` のまま
  → 完了（2026-10-25。`docs/development-rules.md` §2.1（ルート直下のファイルを実態の 7 つに直し、存在しない
  `settings.json` のリンクの記述を訂正として残した）・§3 の構成図・§6.2（RDF 1.2 + `rdf:reifies`、潜在索引の
  `latent_index_d<次元>` + 登録表）・§6.3（Protocol と `s3://models/...`）・§6.5（写像 DSL）・§6.6 / §7（objectstore）、
  `.claude/rules/bridge-worldmodel.md`（Protocol と「`probe` / `explain` を WM に足さない」、`s3://`）、
  `.claude/rules/knowledge.md`（潜在索引の表と登録表）、**設計書 §4.3 に ADR-0007 の注記**、README（CI 4 ジョブと
  未実行であること、`settings.json` のリンク）、`0001` マイグレーションのコメント、`docs/runbooks/local-setup.md`
  （Oxigraph の記法）。**ポート退避の項目は文書ではなく実装を直した**——「`.env` で退避する」が設計の意図で、
  compose が直下の `.env` を読んでいなかった（X-14、所見 114）。runbook には経緯を 1 段落足した。
  CLAUDE.md に残る ADR 由来の記述（MinIO・RML・RDF-star）は規約そのものの変更なので X-13 で扱う）
- [x] **X-11** 入れ子の設定が `.env` を読まない不具合を直す（**バグ修正。再現テストを先に書く**） —
  `BridgeSettings` / `ReasoningSettings` / `WorldModelSettings` / `ActionsSettings` は `env_file` を持たず
  `default_factory` で別に構築されるので、`SBP_BRIDGE_*` / `SBP_REASONING_*` / `SBP_WM_*` / `SBP_ACTIONS_*` は
  環境変数からしか読まれない。`.env` に書いた閾値は警告無しに既定値になる（所見 108。一時ディレクトリの
  `.env` で再現済み）。再現テストは「一時ディレクトリの `.env` に入れ子の項目を書くと `Settings()` に反映される」。
  直し方（入れ子にも `env_file` を持たせる / 親から `env_nested_delimiter` で渡す / 親の構築時に `.env` を読んで
  入れ子に渡す）は pydantic-settings の挙動を確かめて選ぶ。H-01 で鍵や設定の置き場を `.env` に寄せる前提になる
  → 完了（2026-10-25。**入れ子にも `env_file` を持たせる**案を採った: 共通基底 `_DotenvSettings` に
  `env_file` / `env_file_encoding` / `extra` を置き、`Settings` と 4 つの層が継承する。`env_nested_delimiter` は
  変数名が `SBP_BRIDGE__…` に変わり `.env.example` と運用の設定を全部書き換えることになるので棄却。
  再現テスト `tests/unit/core/test_config.py` 12 件（修正前は 9 件が赤、対照の `SBP_SITE_ID` だけ緑）——各層から
  1 項目ずつ `.env` で効くこと、`SBP_BRIDGE_PROBE_IDS` のカンマ区切りが `.env` からも分割されること、環境変数が
  `.env` に勝つこと、**入れ子の層がすべて親と同じ `env_file` を持つ**ことを構造で検査（層を足して基底を
  継承し忘れると落ちる）。`.env.example` の有効行（`SBP_WM_TORCH_DEVICE=mps` / `SBP_ACTIONS_RECORD_EFFECT_FLOOR=0.5`）は
  既定値と同じなので、CI が `.env.example` を `.env` にコピーしても挙動は変わらない）
- [x] **X-12** `make up PROFILES=a,b` が基本 4 サービスだけを起こす不具合を直す（**バグ修正**） — Makefile が
  `$(comma)` を定義していないので `--profile ui,observability` の 1 引数になり、compose はそれを未知の profile
  として扱う（所見 111。`docker compose ... config --services` で確認）。`comma := ,` を定義し、`make -n up
  PROFILES=ui,observability` が `--profile ui --profile observability` を出すことを確かめる
  → 完了（2026-10-25。`comma := ,` を定義。再現テスト `tests/unit/test_makefile.py` 5 件（修正前は 3 件が赤）——
  `make -n up` の出力で、複数指定が `--profile` 2 つになること・空白や空要素が落ちること・単一指定と無指定を検査。
  修正後に `docker compose ... --profile ui --profile observability config --services` が 7 サービスに解決されることを確認）
- [x] **X-13** 入口の文書のコマンドと設定の記述を実装に合わせる — 所見 111 / 112 の乖離のうち X-09（ADR 追従）に
  入らないもの。**CLAUDE.md**（`make up --profile` → `PROFILES=`、`make dev` は API だけ、`make bootstrap` は
  `brew bundle` を呼ばない、`make check` の中身、`make gen` はダッシュボードも生成、`make seed` の「MinIO
  バケット」、「写像（RML）」、モデル族の `gnn`、Oxigraph の「RDF-star」、Compose 6 GB の範囲、
  `ontology/imports/` の「固定版同梱」）、**README**（同じコマンドの記述、ルートの `settings.json`、CI のジョブ数）、
  **層別ルール**（`vocab.prov_activities`、`run(plan)`、`tenacity` / `ruptures`、LLM のプロバイダ実装ファイル、
  写像ごとの契約テストファイル、ポート 9001、`make lint` の Turtle 検査）、**runbook**（`backup-restore.md` の
  `make ontology-check` の説明と「G0〜G6」、`version-promotion.md` の「G0〜G6」）、**PROMPT.md**（「TASK.md §7」の
  参照、`test_mapping_workorder.py`、P-30 の日次点検 runbook）、**`.env.example`**（`Settings` の 15 項目と
  compose の 4 変数が無い、`SBP_ENV` に `prod`）、**docstring**（`core/errors.py`、`integration/connectors.py`、
  `integration/mock/business_system.py`、`services/scheduler_worker.main`、`services/belief_worker` 冒頭）、
  **development-rules.md** §2.1（ルート直下 4 ファイル）。`ontology/imports/` は文書を直すか
  `scripts/fetch_imports.sh` を実行して同梱するかを先に決める。CLAUDE.md の変更はそれ自体が規約の変更なので、
  変更点を PR で人が確認する。**機械で検査できるもの**（Makefile ターゲットと文書のコマンド表、`Settings` と
  `.env.example` のキー、層別ルールが名指しする記号の実在）は X-07 の契約テストに入れることを検討する
  → 完了（2026-10-25。**CLAUDE.md を除く全項目**。README のコマンド（bootstrap / up の profile / dev）、層別ルール 5 本
  （PROV の列挙と `run(plan)` は「未実装」と明記、LLM のプロバイダ実装は無いと明記、`tenacity` / `ruptures` は依存に
  無いと明記、写像の契約テストは 1 本、ポートと `.env` の渡し方、`make lint` は Turtle を正規化しない、層別の閾値を
  足すときは `_DotenvSettings` を継承）、runbook 2 本、PROMPT.md（P-00 / P-01 は HITL.md を見る、P-30 は取り込みの
  健全性から見て日次点検の runbook を作る、M1-03 は実施済みの注記）、`.env.example`（`Settings` の 15 項目と compose の
  4 変数をコメントで追加、`SBP_ENV` に `prod`、`make up PROFILES=`）、docstring 5 か所、Makefile のヘルプ 2 行、
  development-rules.md §3 の `imports/` と §6.2 の PROV と §7 のコマンド表。`ontology/imports/` は**取得せず、未取得で
  あることを文書に書いた**（取得はネットワークを使い、いま必要としている処理が無い）。**機械検査を 1 つ入れた**:
  `tests/unit/core/test_config.py` が `Settings` の全項目（入れ子を含む）が `.env.example` に載っていることを検査する
  （項目を消すと落ちることを確認済み）。CLAUDE.md は X-15 に分けた）
- [ ] **X-15** CLAUDE.md を実装に合わせる（**規約の変更なので人が確認する**） — X-13 のうち CLAUDE.md の項目
  （`make up --profile` → `PROFILES=`、`make dev` は API だけ、`make bootstrap` は `brew bundle` を呼ばない、`make check` と
  `make gen` の中身、`make seed` の「MinIO バケット」、「写像（RML）」、モデル族の `gnn`、Oxigraph の「RDF-star」、
  Compose 6 GB の範囲、`ontology/imports/` の「固定版同梱」）。**変更案をブランチ `docs/claude-md-sync` に置いた
  （main に未統合）**。CLAUDE.md は毎セッション読み込まれる規約の正典なので、内容を人が確認してから main に入れる
- [x] **X-10** M8 のブランチを main に統合する — M8-01〜M8-21 と棚卸しの 20 コミットが
  `feat/bridge-probe-serialization`（M8-01 のブランチ名）に積まれ、main は M7 完了直後（`74974d8`）のまま
  （所見 105）。§2 の「1 タスク = 1 ブランチ」から外れている。統合したうえで、以後のタスクは
  `feat/<module>-<topic>` に戻す。リモートへの push は H-23 の決定後。**2026-10-25 時点も同じ**（main より
  20 コミット先行、リモート無し）。統合の前に、未コミットの棚卸し（2026-10-17 / 10-25、所見 107）をこのブランチに
  コミットしておく
  → 完了（2026-10-25。棚卸しを `431ce14` でコミットし、main を `--ff-only` で統合。履歴は線形のまま。
  統合済みのブランチは削除した。以後のタスクは `fix/…` / `feat/…` で切り、`make check` 緑で main に fast-forward する。
  リモートが無いので PR は作れない——PR 記載事項はコミット本文に書く）
- [x] **X-14** compose がリポジトリ直下の `.env` を読まない不具合を直す（**バグ修正**。2026-10-25 に X-09 の
  着手中に発見） — `docker compose -f infra/compose.yaml` は compose ファイルのディレクトリ（`infra/`）の `.env`
  しか読まないので、直下の `.env` に書いたポート退避（`SBP_PORT_*`）・プラットフォーム・`SBP_PG_*` は compose の
  変数展開に効いていなかった。runbook と層別ルールの「ポートの衝突は `.env` で退避する」は、この Makefile では
  成立していなかった（所見 114。開発機の退避が `.envrc` にあった理由、所見 60）
  → 完了（2026-10-25。`COMPOSE` に `.env` があるときだけ `--env-file .env` を付けた。無いときに付けると compose が
  起動前に落ちる。環境変数は `--env-file` より優先されるので、CI のワークフロー全体の `SBP_PLATFORM=linux/amd64`
  はそのまま効く（一時ディレクトリで確認）。再現テスト 2 件（`tests/unit/test_makefile.py`。`.env` の有無で
  `--env-file` を付け外しすることを、一時ディレクトリで `make -n -f <Makefile>` を走らせて検査）。修正後、開発機の
  `.env` でも `docker compose ... config` が解決できることを確認）

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
| OPS-09 | 月次 | `docs/HITL.md` の棚卸し: `期限到来` の項目を判断し、`暫定` のまま放置されているものを確認する（X-08） |

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

## 7. 未決事項

**本節は `docs/HITL.md` に移した**（2026-10-04）。人の判断を待つ事項は 1 箇所で管理する——
本書（タスク）と §7（未決）に分かれていると、「タスクが止まっている理由」と「誰の判断待ちか」が
突き合わせられなかった。

`docs/HITL.md` は 24 件を優先度順に持つ（2026-10-17 に H-22 / H-23、2026-10-25 に H-24 を追加）。
本書のタスクをブロックしているのは次の 6 件である:

| HITL | 内容 | ブロックするタスク |
|---|---|---|
| **H-22** | G8 の統合テストで、ワーカーの入力をテストが直接置いてよいか | **M8-11**（と、M8-23〜M8-25 を M8-11 の前提にするか） |
| **H-24** | M8 の目的の外にある運用の穴（再接地の実行・問い合わせの入口・LLM 機能の入口・SHACL / 同一性 / 空間）をどの里程で扱うか | 該当タスクの起票そのもの（§3 M8 の末尾） |
| **H-04** | LLM の live 経路で使うプロバイダ | **M8-10** |
| H-13 | 観測要求が「満たされた」ことの判定 | M8-29（決めなくても入口は作れるが、充足の判定が残る） |
| H-10 | 却下した提案の再提案を抑えるか | M8-09（決めなくても着手できるが手戻りが出る） |
| H-23 | リモートリポジトリと CI の実行場所 | X-01 の実質（CI が一度も走っていない）、X-10 の push |

本節にあった 5 件の決着:

1. ~~リポジトリ独立化（M0-01）~~ → 決定済み: 独立リポジトリ化（ADR-0001、2026-10-03）
2. ~~初期ドメインの選定~~ → 決定済み: 製造・設備保全（ADR-0002、2026-10-03）
3. ~~CI 基盤~~ → 決定済み: GitHub Actions 3 ジョブ（X-01 完了）。**ADR を起票していない**ので
   HITL.md §4 に残してある
4. ~~UI の技術選定（M7-04）~~ → 決定済み: Vite + React、実行時依存を入れない（ADR-0008）
5. **法令上の消去要求への対応** → 未決のまま HITL.md の **H-05** へ（運用開始前までに決める）
