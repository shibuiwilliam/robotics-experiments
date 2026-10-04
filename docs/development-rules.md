# SBP 開発ルール — ディレクトリ構成・ファイルの役割・開発規約

- 日付: 2026-10-03
- 対象: SBP（Semantic Bridge Platform）を本ディレクトリで開発するすべての開発者・Claude Code セッション
- 正典: `docs/design/sbp-design.md`（詳細設計書 v0.1、全 18 章 + 付録）。本書は設計書・`CLAUDE.md`・`.claude/rules/*.md` の内容を「ディレクトリとファイルの観点」から統合整理したもので、**矛盾があれば設計書 → CLAUDE.md → 本書の順に優先する**。

---

## 1. SBP とは何か（30 秒版）

ワールドモデル（WM）× オントロジーで、物理世界の観測（センサ・カメラ・設備）と業務システムの記録（ERP/MES/WMS/CMMS・計画・規程）を、**同じ語彙・同じ時間軸・同じ確信度尺度**の上で突き合わせる「信念維持システム」。

設計の背骨（設計書 §0.3）:

1. **真実ではなく信念を管理する** — 「正」フラグは存在しない。すべて確信度と来歴付きの主張。
2. **すべての事実は誰かの主張である** — 業務記録も WM の推定も予測も ICE（情報内容実体）。
3. **接地は双方向の写像の対** — grounding（記号→潜在）と abstraction（潜在→記号）の可換性（整合残差 ‖A∘f − T∘A‖）が整合条件。
4. **イベントが第一、状態は投影** — 状態ストアは常にイベントログから再構築可能。
5. **不一致は例外ではなく製品** — 物理と業務の食い違い（Discrepancy、五類型）を一級オブジェクトとして検出→起票→解決→学習する。

データパスは三つ: **推定パス**（物理→潜在→プローブ→イベント→信念）、**記録パス**（業務 CDC→写像→BusinessEvent→信念）、**アクションパス**（信念→意味契約検証→実行→Expectation 検証→学習）。

---

## 2. リポジトリ配置の規約

### 2.1 ルート直下は 4 ファイルのみ

ルートディレクトリ（`./`）に置くファイルは **`CLAUDE.md` / `README.md` / `.env.example` / `settings.json` の 4 つだけ**。それ以外のドキュメントは `docs/`（人が読む文書）または `.claude/`（Claude Code 設定・層別ルール）に置く。

| ルートのファイル | 役割 |
|---|---|
| `README.md` | 人間向けの入口（概要・クイックスタート・ドキュメントマップ） |
| `CLAUDE.md` | 毎セッション読み込まれる基幹ルール |
| `.env.example` | 全環境変数の一覧（`.env` はコミットしない） |
| `settings.json` | **`.claude/settings.json` へのシンボリックリンク**。Claude Code が実際に読むのは `.claude/` 配下であり、実体を二重管理しないためリンクにしている |

配布キット `sbp-claude-code-kit/` は 2026-10-03 に上記構成へ展開済みで、重複解消のため削除した（内容は全ファイル diff 一致を確認のうえ `.claude/` と `docs/` に移設。キットの README.kit.md が担っていた展開手順は役目を終え、読み込み確認手順は `README.md` に引き継いだ）。

### 2.2 現在地

里程の状態は `docs/STATUS.md`、タスクの状態は `docs/TASK.md`、実装中に判明した事実は
`docs/PROGRESS.md` にある。本書はそれらを参照するだけで、進捗を二重に持たない。

- 独立リポジトリ化は完了済み（ADR-0001）。
- セットアップ確認: Claude Code で `/context` を実行し Memory files に `CLAUDE.md` が出ていること。`.claude/settings.json` が効いていれば、`.env` の読み取り・生成物（`schemas/`, `vocab.py`）の編集は自動的に拒否される。

---

## 3. 目標ディレクトリ構成と各部の役割

以下が CLAUDE.md の定める実装後の構成。**M0 以降、コードはこの構造に従って作る。**

```
sbp/
├── README.md               # 人間向けの入口（概要・クイックスタート・ドキュメントマップ）
├── CLAUDE.md               # 毎セッション読み込まれる基幹ルール（進捗は書かない）
├── settings.json           # → .claude/settings.json へのシンボリックリンク
├── .claude/
│   ├── settings.json       # 許可/拒否コマンド、生成物・.env の編集禁止、PostToolUse フック
│   ├── rules/*.md          # 層別詳細ルール（frontmatter の paths に合致するファイルを開くと自動読込）
│   └── hooks/post-edit.sh  # 編集後: .py は ruff format+fix、.ttl は rdflib 構文検査（警告のみ、非ブロック）
├── .env / .env.example     # 全接続情報・鍵・モード。.env は gitignore。.env.example を同一コミットで同期
├── Makefile                # 全ターゲット .PHONY + 1 行説明（make help）。Python は必ず uv run 経由
├── pyproject.toml          # uv 管理、Python 3.12
│
├── ontology/               # ★ 概念の正典。コードより先にここを変更する
│   ├── core/*.ttl          #   L1 SBP 中核（Situation, Discrepancy, Expectation, IdentityClaim, Track, Anchor, Probe, ModelVersion…）
│   ├── imports/            #   L0 標準（BFO/IAO/SOSA/PROV/OWL-Time/GeoSPARQL/QUDT）。固定版同梱・改変禁止・fetch_imports.sh で取得
│   ├── domain/*.ttl        #   L2/L3 ドメイン・企業概念
│   ├── shapes/             #   SHACL（Violation=構造制約→隔離 / Warning=整合制約→不一致候補）
│   ├── process/*.lp        #   事象計算（clingo）: initiates/terminates。FluentType/EventType と対応必須
│   ├── norms/*.lp          #   規範規則（出典文書・節を来歴に持つ）
│   ├── VERSION             #   semver（MAJOR=削除・意味変更 / MINOR=加法 / PATCH=注釈）
│   └── CHANGELOG.md        #   変更ごとに semver 判定を記す。MAJOR は ADR + 再接地計画必須
│
├── schemas/                # 生成物: EventType 由来 JSON Schema。手編集禁止（make gen で再生成）
│
├── src/sbp/                # 層の依存: core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge ← reasoning ← actions ← api
│   ├── core/               #   型の根: Assertion, EventEnvelope, Belief, Modality, IRI（pydantic v2）/ vocab.py（生成物・手編集禁止）
│   │                       #   / time（UTC・右開区間）/ ids（UUIDv7）/ config（Settings, SBP_ プレフィクス）/ errors（例外階層）
│   ├── eventlog/           #   Redpanda producer/consumer、スキーマ検証、event_id 冪等化。リトライ許可箇所その 1
│   ├── knowledge/          #   Oxigraph クライアント、二時制アサーション API（assert_/retract/query のみ公開）、
│   │                       #   名前付きグラフ、PROV バンドル、潜在索引（pgvector+HNSW）、ハイブリッドクエリ、再接地
│   ├── integration/        #   コネクタ（1 コネクタ 1 ファイル）、RML 写像、BusinessEvent 化、情報源信頼性モデル、
│   │                       #   計画・規範取込、書き戻し（既定 dry）
│   ├── worldmodel/         #   WorldModel Protocol（encode/step/rollout/probe/explain/embed/health）、
│   │                       #   families/（koopman, jepa_ts, gnn）、context.build（AuditMask 必須）、レジストリ、train/
│   ├── bridge/             #   probes（線形優先・ProbeSpec・promote は AND 条件）、較正、lifting（BOCPD 事象昇格）、
│   │                       #   identity（Track→IdentityClaim）、spatial（shapely ゾーン包含）、validate（pyshacl）
│   ├── reasoning/          #   belief（三源ベイズ・半マルコフ・log 空間）/ reconciliation（五類型・状態機械）/
│   │                       #   prediction / whatif / expectation / discovery / scheduler / explain / rules（clingo）
│   ├── actions/            #   ActionType（actions/*.yaml から読込）、意味契約、承認、実行、補償
│   ├── llm/                #   Provider 抽象（anthropic/openai/gemini）、routing.yaml、prompts/<task>/<version>.md、
│   │                       #   replay/record/live モード。リトライ許可箇所その 2
│   ├── api/                #   FastAPI: GraphQL（strawberry）、SPARQL プロキシ、ストリーム購読、コマンド API
│   └── testing/            #   SynthWorld（合成データ・唯一の真値 oracle 保持者）。ユースケース検証ではない
│
├── actions/*.yaml          # ActionType 定義（設計書 §9.1 形式）。compensation 未定義は登録拒否
├── reliability/*.yaml      # 情報源信頼性モデルの初期値（設計書 §7.3）。更新は update_from_resolution()
├── value/*.yaml            # 不一致の深刻度に使う業務価値（設計書 §8.2）
├── mappings/*.yaml         # 業務記録 → Record / BusinessEvent の写像（ADR-0006）
├── observation/*.yaml      # 観測手段の割り当て（フルーエント → kind / reliability / cost。ADR-0011）
├── services/               # ワーカー: bridge / belief / reconcile / scheduler / learning（ADR-0012）
│                           # 1 ワーカー 1 プロセス、SIGTERM graceful shutdown、起動時に Settings と疎通検査
├── infra/
│   ├── compose.yaml        # 4 サービス固定: redpanda / postgres(timescaledb-ha+pgvector) / oxigraph / minio。
│   │                       # 全サービスに platform・healthcheck・mem_limit、合計 6 GB 以内。追加は ADR
│   ├── sql/versions/       # alembic マイグレーション（hypertable 化も含む。DDL 手流し禁止）
│   └── prometheus/         # 任意 profile
├── docs/
│   ├── design/sbp-design.md  # 正典。実装前に該当章だけ Read（@ で全文 import しない）
│   ├── adr/NNNN-<slug>.md    # 設計判断の記録（MADR 形式、テンプレは 0000）
│   ├── runbooks/             # 運用手順（再接地差分レポート形式など）
│   └── STATUS.md             # マイルストーン進捗。進捗はここだけに書く
└── tests/
    ├── unit/<module>/      # 外部 I/O は Protocol でモック。pytest-socket でネットワーク遮断
    ├── integration/        # -m integration、Compose 必須。未起動は skip でなく明示エラー
    ├── contract/           # schemas↔pydantic 往復、envelope 後方互換、写像不変条件、オントロジー互換
    ├── property/           # hypothesis: 二時制 as-of/valid-at、様相格子、区間代数、冪等性
    └── fixtures/llm/<task>/<hash>.json  # LLM replay 用
```

### 3.1 作業領域 → 読むべき設計書の章

| 触るディレクトリ | 設計書の章 | 自動読込されるルール |
|---|---|---|
| `ontology/`, `schemas/` | §3 オントロジー設計 | `.claude/rules/ontology.md` |
| `src/sbp/eventlog/`, `src/sbp/knowledge/`, `infra/sql/` | §6 信念・知識層 | `.claude/rules/knowledge.md` |
| `src/sbp/worldmodel/`, `src/sbp/bridge/` | §4 WM 層、§5 接地層 | `.claude/rules/bridge-worldmodel.md` |
| `src/sbp/reasoning/`, `src/sbp/actions/`, `actions/` | §8 推論層、§9 アクション | `.claude/rules/reasoning-actions.md` |
| `src/sbp/integration/`, `src/sbp/llm/` | §7 業務統合層 | `.claude/rules/integration-llm.md` |
| `infra/`, `tests/`, `services/`, `Makefile` | §12 配備・運用、§13 評価 | `.claude/rules/infra-testing.md` |
| `src/sbp/api/` | §10 API・エージェント・UI | — |
| セキュリティ・権限に触れるとき | §11 | — |

---

## 4. 層アーキテクチャと依存の向き（違反禁止）

```
core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge ← reasoning ← actions ← api
```

- 下位は上位を import しない。層をまたぐ結合は `core` の型と `eventlog` のイベントのみ。
- 責務の境界線（これを破る PR はレビューで止める）:
  - `worldmodel` は**記号を決めない**（プローブは `bridge` の仕事）。
  - `bridge` は**永続化しない**（TypedProbabilisticEvent をイベントログに出すだけ）。
  - `reasoning` は `knowledge` の公開 API（`assert_`/`retract`/`query`）経由でのみ永続化する。SPARQL 文字列は `knowledge` 内に閉じる。
  - 業務システムは system of record のまま。**基盤が所有するのは信念だけ。**

---

## 5. 絶対不変条件（違反する PR はマージしない）

CLAUDE.md の 12 箇条。全コード・全テスト・全レビューの判定基準:

1. 全アサーションは `(subject, predicate, object, valid_from, valid_to, tx_from, tx_to, modality, belief, provenance)` + `ontology_version` / `schema_version` を欠かさない。
2. **追記専用**。訂正は旧文の `tx_to` を閉じて新文を追加。アサーションの UPDATE / DELETE 禁止。
3. `modality` は `observed | inferred | predicted | planned | normative | hypothetical` のみ。プローブ・規則・LLM・エージェントの出力は必ず `inferred`。`hypothetical` はシナリオ用名前付きグラフに隔離し、`promote_scenario()` 以外で主信念に入れない。
4. 全イベントは UUIDv7 の `event_id` と `schema_version` を持ち、`event_id` で冪等。順序保証は実体単位のみ（グローバル順序を仮定しない）。
5. IRI の直書き禁止。`sbp.core.vocab`（生成物）からのみ参照。
6. **監査隔離**: ある記録を監査する推定（プローブ・信念更新・照合）に、その記録自身と派生信念を入力しない。条件付けベクトルは必ず `AuditMask` を通す。
7. `belief` には較正済みの値だけ。生スコアは `evidence` に残す。
8. 時刻は timezone-aware UTC の `datetime` のみ。期間は `timedelta`、区間は右開 `[start, end)`。
9. 生映像・音声を永続化しない。開発中は人の映るデータを扱わず合成データのみ。永続化は潜在と記号のみ。
10. 真値（オラクル）は `sbp/testing` 以外から読まない（import 境界を lint で禁止）。
11. LLM を信念更新・照合・事象昇格のホットパスから呼ばない。用途は概念提案・文書構造化抽出・説明の文章化・NL→クエリ変換に限る。
12. シークレットは `.env` のみ。コード・テスト・ログ・fixture に出さない。

---

## 6. 層別の詳細ルール（`.claude/rules/` の要点）

### 6.1 オントロジー（コードより先に変更する）

- 名前空間: `sbp-core:`=`https://sbp.example/core#`、`ent:`=`https://sbp.example/ent#`、`site-<id>:`。新規名前空間は ADR。
- 必須注釈: `rdfs:label`（ja/en）、`skos:definition`、`sbp-core:layer`（L1..L4）、`sbp-core:owner`。欠けると `make ontology-check` が落ちる。
- `FluentType`/`EventType` は `ontology/process/*.lp` の initiates/terminates と対応が取れていること。排他フルーエントは `mutuallyExclusiveWith` 宣言 + 対応する `sh:Warning` 形状を必ず追加。
- SHACL の使い分け: `sh:Violation`=構造制約（登録拒否→隔離キュー）、`sh:Warning`=整合制約（登録して不一致候補）。**検証は拒否ではなく証拠生成。**
- 版管理: MINOR までは既存プローブ・クエリ・schemas の後方互換を `tests/contract/test_ontology_compat.py` で証明。非推奨は `owl:deprecated` + `dcterms:isReplacedBy` で 2 MINOR 版残す。
- 標準にある概念を再定義しない。独自定義は「物理と業務を結ぶためだけに必要な接続概念」（Situation, Discrepancy, Expectation, IdentityClaim, Track, Anchor, Probe, ModelVersion, DataSource）のみ。
- 変更手順: ① TTL + 形状 + 必要なら .lp → ② CHANGELOG に semver 判定 → ③ `make gen` → `make ontology-check`（生成差分は生成専用コミット）→ ④ その後にコード。

### 6.2 イベントログ・知識層

- トピック名 `<site>.<layer>.<class>`、キーは subject IRI。トピック追加は `scripts/topics.py` + `make seed`（手作業禁止）。
- Producer は `EventEnvelope`（pydantic）のみ送信可、送信前に JSON Schema 検証。Consumer は at-least-once、`processed_events(event_id)` で冪等化。`causation_id`/`correlation_id` を必ず引き継ぐ。
- 二時制 API の公開関数は `assert_` / `retract` / `query` のみ。物理削除 API を作らない。名前付きグラフは `urn:sbp:g:<modality>:<site>:<ontology_version>`、hypothetical は `urn:sbp:scenario:<id>`。
- 文メタデータは RDF-star（pyoxigraph の Quad/Triple term）。rdflib は TTL 読み書き専用。`query` は必ず `ontology_version` を返す。
- 予測は `horizon_origin` 違いで共存（既定クエリ=最新起点、精度追跡=全起点）。
- 潜在索引: `latent_index(entity, ts, level, wm_version, vec)` + HNSW(cosine)。`level`/`wm_version` で絞ってから近傍検索。次元変更は新テーブル + 再接地ジョブで移行。
- PROV 活動種別は列挙のみ（probe-run, rule-run, belief-update, reconciliation, llm-extraction, human-review, regrounding）。来歴はバンドル単位、証拠は参照。
- SQL は alembic のみ（DDL 手流し禁止）。時刻は `timestamptz`、区間は `tstzrange`。

### 6.3 ワールドモデル・接地層

- 全モデル族が Protocol（encode/step/rollout/probe/explain/embed/health）を実装。`context` は `sbp.worldmodel.context.build(entity, mask: AuditMask)` のみで作る（マスクなし呼び出しを作らない）。
- モデル族は初期 `koopman` と `jepa_ts`。**数百万パラメータ以下、MPS で学習できるサイズ**。重みは `minio://models/...`、ロードは version 指定必須（`latest` 禁止）。
- 損失は `L_pred + λ_phys + λ_probe + λ_logic + λ_align` の合成、各項を個別ログ。`λ_align` は後期有効化が既定。
- プローブはまず線形。非線形が必要なら「潜在が線形分離できない」ことをログに残し WM 改善候補へ。`promote()` は **AUROC ≥ 0.85 ∧ ECE ≤ 0.05 ∧ 整合残差 ≤ 既存 active 中央値** の AND、満たさなければ例外。
- 弱ラベルは `TimeBag(concept, entity, interval, weight)`（重みは信頼性モデル由来）、MIL 損失で学習。出力は必ず `Calibrator.apply` を通し、認識的/偶然的不確実性を分けて返す。
- 事象昇格: BOCPD（自前 `bocpd.py`）→ ヒステリシス（0.7/0.3）→ 最小持続 → clingo 逆写像 → 重複抑制 → `TypedProbabilisticEvent`。区間に `uncertainty_s` 必須（点時刻イベント禁止）。
- Track はまず `UnidentifiedPhysicalObject` として登録。束縛は `linear_sum_assignment`（コスト = −log p）、多重仮説が必要になったら ADR。`IdentityClaim` 撤回時は `Discrepancy(kind=4)` を必ず起票。Person の Track を個人識別に使わない。

### 6.4 推論・アクション層

- 信念更新は実体単位の前向きフィルタ（半マルコフ）、log 空間・numpy。実体間結合は strong_relations 限定の loopy BP を周期実行。来歴に三源寄与率 `contrib: {observed, record, plan}` を必ず入れる。
- 照合は五類型を `rules/kind1.py`〜`kind5.py` に 1 つずつ。閾値 θ・持続 τ は Settings（拠点別上書き可）。`Discrepancy` の状態遷移は `transition()` 経由のみ（`Open → Triaged → Assigned → Resolved → Learned` と `Open → Suppressed`）。
- 解決結果→学習信号: 物理正=記録訂正提案+信頼性更新 / 記録正=負例 TimeBag / 両誤=概念候補 / 判定不能=観測要求。
- What-if は必ず `hypothetical` + `scenario`、昇格は `promote_scenario()` のみ。反実仮想には妥当性スコア併記。
- Expectation は記号側期待（T 由来）と潜在側期待（rollout 由来）を別々に検証し、どちらが外れたか切り分ける。
- 規則は clingo（`.lp` 側にテンプレートを置き、Python で規則文字列を組み立てない）。確信度伝播（min/積/noisy-OR）を規則ごとに宣言。
- ActionType は `actions/*.yaml` から読み pydantic 検証。実行フローは 前提検証→承認→実行→ActionEvent→Expectation 生成 を省略しない。`riskClass ≥ medium` は承認必須。compensation 未定義の ActionType は登録拒否。

### 6.5 業務統合・LLM

- 1 コネクタ 1 ファイル、`Connector` Protocol（`pull(since)`, `stream()`）。開発は `mock/`（SQLite+台本）、実接続は設定でのみ有効化。`is_about` 未解決は `UnresolvedRef` として残す（捨てない）。
- 写像は RML/R2RML、写像ごとに契約テスト。業務スキーマ変更はスキーマ指紋比較で検知し `MappingDriftDetected` イベントとして出す（例外にしない）。
- 信頼性モデルは `DataSource × 記録型` ごと（時刻ずれ分布・誤記率・欠落率・系統誤差）。初期値 `reliability/*.yaml`、更新は `update_from_resolution()`。尤度は `log p`。
- 観測手段（フルーエント → `kind` / `reliability` / `cost`）は `observation/*.yaml`。**オントロジーに置かない**——コストと信頼度は拠点が持つ計測器の性質で、拠点ごとに違う（ADR-0011）。宣言したフルーエントがオントロジーに実在することを読み込み時に検査する。
- 書き戻しは冪等 `command_id`、既定 `SBP_WRITEBACK_MODE=dry`。
- LLM の入口は `sbp.llm.Provider` のみ。モデル ID はコードに書かず `.env` + `routing.yaml`。出力は pydantic → SHACL の順で検証し `inferred` 登録、来歴に provider/model/prompt_version(hash)。文書抽出スキーマは根拠スパン必須。
- `SBP_LLM_MODE=replay` が既定。replay でネットワークを使わず、fixture が無ければ**黙って live に落ちず失敗**させる。プロンプトは `prompts/<task>/<version>.md` で版管理。

### 6.6 インフラ・テスト・ワーカー

- Compose は 4 サービス固定（redpanda / postgres / oxigraph / minio）+ 任意 profile（ui / observability / console）。全サービスに `platform: ${SBP_PLATFORM:-linux/arm64}`・healthcheck・mem_limit、合計 6 GB 以内。ボリューム削除は `make nuke` のみ。
- 環境変数を追加したら `.env.example` を**同じコミットで**更新。
- pytest マーカー: `unit`（既定）/ `integration` / `slow` / `llm_live`。`make test` は `-m "not integration and not llm_live"`。時刻は freezegun、乱数は seed 固定、ネットワークは pytest-socket で遮断。`llm_live` は CI で走らせない。
- ワーカーは 1 プロセス 1 ワーカー、SIGTERM で graceful shutdown（オフセットコミット後に終了）、起動時に Settings と疎通を検査。

---

## 7. 開発環境とコマンド

- 前提: macOS 14+ / Apple Silicon、Docker Desktop または OrbStack、Homebrew、uv、Python 3.12。Node 20 は `apps/ui` のみ。
- ML は PyTorch MPS（`PYTORCH_ENABLE_MPS_FALLBACK=1`）。**CUDA 前提のコードを書かない。**
- 初回は `make bootstrap`。日常は:

| コマンド | 用途 |
|---|---|
| `make up` / `make down` | Compose 起動/停止 |
| `make seed` | オントロジー投入・トピック作成・alembic・MinIO バケット |
| `make dev` | API（:8000）とワーカーをホットリロードで起動 |
| `make test` / `make test-int` | 単体（ネットワーク不要・5 分以内）/ 統合（Compose 必須） |
| `make lint` / `make typecheck` | ruff / pyright strict |
| `make gen` | ontology → `vocab.py`・`schemas/*.json` 再生成 |
| `make ontology-check` | TTL 構文・SHACL 自己検証・.lp 対応・semver 互換・生成物差分 |
| `make check` | lint + typecheck + ontology-check + test。**コミット前に必ず緑** |

コーディング規約（抜粋）: `from __future__ import annotations` + 型ヒント必須（pyright strict）、契約は pydantic v2 / 内部一時構造は `dataclass(frozen=True)`、ログは structlog（`event_id`/`correlation_id`/`entity` を bind、`print` 禁止）、例外は `sbp.core.errors` 階層（リトライは eventlog consumer と `llm.Provider` の 2 箇所だけ）、CPU 重い処理は同期関数 + `asyncio.to_thread`、確率は log 空間、乱数関数は `seed` 引数必須、1 ファイル 400 行 / 1 関数 60 行で分割検討、閾値定数は `Settings`（`SBP_` プレフィクス）。

---

## 8. 開発フロー

1. **マイルストーン順守**: M0 骨格 → M1 統合 → M2 接地 → M3 推論 → M4 WM → M5 閉ループ → M6 進化 → M7 運用。**M(n) の統合テストが緑になるまで M(n+1) に着手しない。** タスク粒度の計画・ゲート定義は `docs/TASK.md`、里程粒度の現在地は `docs/STATUS.md`（CLAUDE.md に進捗を書かない）。
2. **着手前**: 設計書の該当章だけ Read（§3.1 の対応表）。大きな変更（新モジュール・オントロジー MAJOR・ストア変更・サービス追加）は plan mode で計画→承認→実装。
3. **概念が必要になったら**: コードで定義せず、オントロジー変更手順（§6.1）を先に回す。
4. **設計から逸れる判断**: 先に `docs/adr/NNNN-<slug>.md`（テンプレは `0000-template.md`。触る層・不変条件への影響・semver 判定・再接地要否・未決を必ず書く）。
5. **Git**: ブランチ `feat/<module>-<topic>` / `fix/...` / `ont/...`、Conventional Commits（`feat(bridge): ...`）。PR には触った層・不変条件への影響・オントロジー版・テスト種別・ADR リンクを必ず書く。
6. **バグ修正**: 再現テストを先に追加。カバレッジより「不変条件が壊れたら落ちる」テストを優先。
7. **完了の定義**: 型が通る・テストがある・`make check` 緑・docstring / ADR / CHANGELOG 更新・ログに相関 ID。
8. **不明な業務仕様**: 推測で埋めず `TODO(question)` + ADR の「未決」に残して質問する。

---

## 9. ガードレール（`.claude/settings.json` が機械的に強制するもの）

| 区分 | 内容 |
|---|---|
| 許可 | `make *`, `uv *`, `docker compose *`, `git status/diff/log/add/commit/checkout -b`, リポジトリ内の Read/Edit/Write |
| 拒否 | `.env` の Read/Edit、`ontology/imports/**`・`schemas/**`・`src/sbp/core/vocab.py` の Edit/Write（生成物・標準の保護）、`git push --force`、`docker volume rm`、`rm -rf` |
| フック | Edit/Write 後に `post-edit.sh`: `.py` は ruff format + fix、`.ttl` は rdflib 構文検査（失敗は警告のみ） |

---

## 10. やってはいけないこと（CLAUDE.md 最終節）

- 「正」フラグや `is_true` 列を作る — 真実ではなく信念を管理する。
- 状態テーブルを直接更新して整合させる — 状態はイベントの投影。
- 静的精度（AUROC）だけでプローブを active にする — ECE と整合残差も AND 条件。
- CUDA 前提・大規模モデルを持ち込む — MacBook で学習・推論できる数百万パラメータ級まで。
- 便利だからと Redis 等の新サービスを増やす — 既存 4 サービスで足りないことを ADR で示す。
- オントロジーを飛ばしてコードで概念を定義する。
- ユースケース検証（実機・サンドボックス実験）を本リポジトリに入れる — 別リポジトリで行う。

---

## 付録: 一次ドキュメント一覧（出典マップ）

| ファイル | 役割 | 本書での反映箇所 |
|---|---|---|
| `README.md` | 人間向けの入口（概要・クイックスタート・文書マップ） | §2.1 |
| `CLAUDE.md` | 毎セッション読込の基幹ルール（約 140 行） | §4, §5, §7, §8, §10 |
| `docs/design/sbp-design.md` | 正典・詳細設計書（§0〜18 + 付録 A〜C。要求・オントロジー・WM・接地・知識・統合・推論・アクション・API・セキュリティ・運用・評価・ロードマップ） | §1, §3.1 |
| `.claude/rules/ontology.md` | ontology/・schemas/・vocab.py 向け詳細ルール | §6.1 |
| `.claude/rules/knowledge.md` | knowledge/・eventlog/・infra/sql 向け | §6.2 |
| `.claude/rules/bridge-worldmodel.md` | worldmodel/・bridge/ 向け | §6.3 |
| `.claude/rules/reasoning-actions.md` | reasoning/・actions/ 向け | §6.4 |
| `.claude/rules/integration-llm.md` | integration/・llm/ 向け | §6.5 |
| `.claude/rules/infra-testing.md` | infra/・tests/・services/・Makefile 向け | §6.6 |
| `.claude/settings.json` + `hooks/post-edit.sh` | 機械的ガードレール | §9 |
| `docs/adr/0000-template.md` | ADR テンプレート | §8-4 |
| `docs/TASK.md` | 開発計画・タスク管理（スコープ・品質ゲート・M0〜M7 分解・運用・リスク） | §8-1 |
| `docs/PROMPT.md` | 開発遂行用プロンプト集（TASK.md を 1 セッションずつ消化するための定型指示文） | §8 |
| `docs/PROGRESS.md` | 開発経過（実装中に判明した事実、ADR に至らなかった判断、既知の制約） | §8 |
| `docs/STATUS.md` | マイルストーン進捗サマリ（現在: M0 着手前） | §2.2, §8-1 |
| `.env.example` | 全環境変数の一覧（Kafka/PG/SPARQL/S3/MPS/LLM/書き戻し） | §7 |
