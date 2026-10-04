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
| runbooks に従って第三者が再接地できる | **部分的** — runbook が参照する記号（`plan` / `run` / `RegroundingReport` 等）が `sbp.knowledge` に実在することをテストで検査した。**「別セッションの人間が読んで実行できるか」は自動では検査できない** | 条件つき通過 |

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
- [ ] **M8-05** scheduler-worker の要求供給配線 — `ObservationRequest` の供給元（イベントログ / 投影テーブル）を決め（ADR）、観測手段の割り当てを実装する。`TODO(question)` を消す
- [ ] **M8-06** Grafana ダッシュボード — `infra/prometheus/` と observability profile のダッシュボード JSON（6 領域 25 指標。指標名は列挙で固定済み）
- [ ] **M8-07** `SituationLink` による実体間結合 — 同一 Situation に属する実体の結合（現在は部分−全体のみ）。Situation の所属を引くクエリを `sbp.knowledge.relations` に足す
- [ ] **M8-08** 学習信号の適用ワーカー — UI/API に入った人の判断を受けて、書き戻し・プローブ再学習・概念採択を実行する。**人の承認が前提**（自動適用しない）
- [ ] **M8-09** 発見ループの周期実行 — 概念発見（検出 → 提案 → curation）を周期ジョブにする。採用後のオントロジー MINOR 起票は人の手作業のまま（意図的）
- [ ] **M8-10** LLM の live 経路 — 任意依存として SDK を 1 つ追加し、`live_factory` を実装する。fixture が無いときに黙って live に落ちないことをテストで固定
- [ ] **M8-12** 不一致の永続化と API 露出 — イベントは通知であって不一致そのものではない（`EventEnvelope` に全体を入れる場所が無い）。UI には不一致キューの型とロジックがあるが、API に資源が無く、知識グラフにも投影表にも不一致が書かれていない。**通知だけでは UI が中身を読めない**（ADR-0010 の未決）
- [ ] **M8-11** 統合テスト G8 — 4 ワーカーを実物のサービス上で回し、各 `step()` が非ゼロを返すことを確認する

**ゲート G8**: G0〜G7 維持 + M8-11 緑 + `TODO(question)` が 0 件（解決した ADR へのリンク、または「意図的に残す理由」が PROGRESS.md に記録されている）。

---

## 4. 横断・継続タスク（マイルストーンに属さない）

- [x] **X-01** CI 整備 — `.github/workflows/ci.yml`。3 ジョブ（`check` / `integration` / `ui`）。`check` が落ちたら `integration` を走らせない（型が通らないのに Compose の起動を待っても情報が増えない）。`integration` は `.env.example` の既定ポートを使い、**healthy の期待件数を明示して待つ**——「healthy でないものが 0 件」を成功条件にすると、コンテナが 1 つも起きていないときも通る
- [x] **X-02** import 境界 lint — import-linter の契約 7 本（`make check` の `deps-check`）。層依存の向き、オラクル境界（不変条件 10）、LLM SDK、LLM のホットパス（不変条件 11）、worldmodel の記号非決定、bridge の非永続化、ワーカーの API 非経由。**`root_packages` に `services` / `scripts` を入れた**——`sbp` だけを見ていると、実運用で動くワーカーと投入スクリプトからオラクルを読む経路が検査されない。各契約が実際に落ちることを違反を仕込んで確認済み
- [ ] **X-03** ADR の継続運用 — 設計逸脱・閾値変更・ライブラリ選定のたびに `docs/adr/NNNN-*.md`
- [ ] **X-04** `docs/STATUS.md` 更新 — ゲート通過時のみ（§2 の更新手順）
- [x] **X-05** 依存更新・脆弱性対応 — `make audit`（pip-audit）と CI の専用ジョブを用意し、2026-10-03 に実施。`uv lock --upgrade` は差分なし（既に最新解）、既知の脆弱性 0 件。**`make check` には入れない**（脆弱性 DB の照会にネットワークが要り、`make check` は無ネットワークで通る方針）。`--strict` は使えないので（sbp 自身が editable で必ず skip される）、**skip されたのが sbp だけであることを検査する**——skip を数えずに「脆弱性なし」と読むと、照会できなかったパッケージが緑に見える。npm 側（`make ui-audit`）では **5 件の実在の脆弱性を検出**（critical 1 / high 1 / moderate 3。すべて開発ツール側）し、`vitest` を 5 系に上げて 0 件にした。以後は月次で `make audit` と `make ui-audit`。コンテナイメージは未照会
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
