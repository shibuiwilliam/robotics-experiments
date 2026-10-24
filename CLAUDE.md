# SBP — Semantic Bridge Platform

ワールドモデル（WM）×オントロジーで、物理世界の観測と業務システムの記録を同じ語彙・時間軸・確信度で突き合わせる「信念維持システム」。
正典は `docs/design/sbp-design.md`（詳細設計書、約 25k 字）。実装前に該当章だけ Read する（`@` で全文 import しない。毎セッションの context を圧迫する）。設計から逸れる判断は先に `docs/adr/` に ADR を書く。
本リポジトリは基盤本体のみ。ユースケース検証（実機・サンドボックス実験）は別リポジトリで行い、ここには入れない。

## 開発環境（macOS / Apple Silicon）
- 前提: macOS 14+、Apple Silicon（Intel は `SBP_PLATFORM=linux/amd64` で可）、Docker Desktop または OrbStack、Homebrew、uv、Python 3.12。Node 20 は `apps/ui` のみ。
- 初回: Homebrew で前提を入れてから `make bootstrap`（`uv sync` → `.env` を `.env.example` から生成 → `docker compose pull`。Brewfile は無い）。
- ML は PyTorch の MPS バックエンド。`PYTORCH_ENABLE_MPS_FALLBACK=1` 前提で、非対応 op の CPU フォールバックを許容。CUDA 前提のコードを書かない。
- Compose の既定 4 サービスのメモリは合計 6 GB 以内（Docker Desktop の上限もそれに合わせる。任意 profile は別枠で、全部起こすと約 7.6 GB）。サービス追加は ADR 必須。

## よく使うコマンド
| コマンド | 内容 |
|---|---|
| `make up` / `make down` | Compose 起動/停止。profile は `PROFILES=ui,observability,console` で任意に指定（`make up --profile …` は make のオプションと解釈される）。直下の `.env` は `--env-file` で compose に渡る |
| `make seed` | オントロジー投入（Oxigraph）、トピック作成（Redpanda）、DB マイグレーション（alembic）、objectstore（SeaweedFS）のバケット作成 |
| `make dev` | API（FastAPI, :8000）をローカルプロセスで起動（ホットリロード）。ワーカーは既定で起こさない——`uv run python -m scripts.dev --workers belief,reconcile` で選ぶ |
| `make test` | 単体テストのみ（外部サービス・ネットワーク不要、5 分以内） |
| `make test-int` | 統合テスト（Compose 必須、`-m integration`） |
| `make lint` / `make typecheck` | ruff（format + lint）/ pyright strict |
| `make gen` | `ontology/` → `src/sbp/core/vocab.py`、EventType → `schemas/*.json`、`METRICS` → Grafana ダッシュボードを再生成 |
| `make ontology-check` | TTL 構文、SHACL 形状の自己検証、事象計算規則の対応、semver 互換、生成物の差分確認 |
| `make check` | lint + typecheck + 層依存（import-linter）+ ontology-check + シークレット走査 + test。コミット前に必ず緑にする |

## リポジトリ構成
```
ontology/      core/ imports/（標準、改変禁止。未取得——必要になったら scripts/fetch_imports.sh） domain/ shapes/（SHACL） process/（clingo .lp） norms/ VERSION CHANGELOG.md
schemas/       生成物: EventType 由来の JSON Schema（手編集禁止）
src/sbp/
  core/        型（pydantic v2）: Assertion, EventEnvelope, Belief, Modality, IRI / vocab.py（生成物）/ time / ids / config / errors
  eventlog/    Redpanda producer・consumer、スキーマ検証、冪等性
  knowledge/   Oxigraph クライアント、二時制アサーション API、名前付きグラフ、PROV、潜在索引（pgvector）、再接地
  integration/ コネクタ、写像（写像 DSL、ADR-0006）、BusinessEvent 化、情報源信頼性モデル、計画・規範取込、書き戻し
  worldmodel/  WorldModel Protocol（ADR-0007）、モデル族（koopman / jepa_ts。gnn は未実装）、記号条件付け、レジストリ、学習
  bridge/      プローブ、較正、事象昇格、同一性束縛、空間束縛、検証
  reasoning/   belief / reconciliation / prediction / whatif / expectation / discovery / scheduler / explain / rules
  actions/     ActionType（YAML）、意味契約、承認、実行、補償
  llm/         Provider 抽象（anthropic / openai / gemini）、構造化出力、プロンプト版管理、replay
  api/         FastAPI: GraphQL（strawberry）、SPARQL プロキシ、ストリーム購読、コマンド
  testing/     合成データ生成 SynthWorld（テスト用フィクスチャ。ユースケース検証ではない）
services/      ワーカーのエントリポイント（bridge-worker, belief-worker, reconcile-worker, scheduler-worker, learning-worker）
infra/         compose.yaml、sql/（alembic）、prometheus/（任意）
docs/          design/（正典）、adr/、runbooks/、STATUS.md（進捗。CLAUDE.md に進捗を書かない）、HITL.md（人の判断待ち）
tests/         unit/ integration/ contract/ property/ fixtures/
```

## 層と依存の向き（違反禁止）
`core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge ← reasoning ← actions ← api`
- 下位は上位を import しない。層をまたぐ結合は `core` の型と `eventlog` のイベントだけ。
- `worldmodel` は記号を決めない（プローブは `bridge`）。`bridge` は永続化しない（イベントを出すだけ）。`reasoning` は `knowledge` の公開 API 経由でのみ永続化する。
- 業務システムは system of record のまま。基盤が所有するのは信念だけ。

## 不変条件（違反する PR はマージしない）
1. 全アサーションは `(subject, predicate, object, valid_from, valid_to, tx_from, tx_to, modality, belief, provenance)` と `ontology_version` / `schema_version` を欠かさない。
2. 追記専用。訂正は旧文の `tx_to` を閉じて新文を追加する。アサーションを UPDATE / DELETE しない。
3. `modality` は `observed | inferred | predicted | planned | normative | hypothetical` のみ。プローブ・規則・LLM・エージェントの出力は必ず `inferred`。`hypothetical` はシナリオ用名前付きグラフに隔離し、`promote_scenario()` 以外で主信念に入れない。
4. 全イベントは UUIDv7 の `event_id` と `schema_version` を持ち、`event_id` で冪等。順序保証は実体単位。グローバル順序を仮定しない。
5. IRI をコードに直書きしない。`sbp.core.vocab`（`make gen` の生成物）からのみ参照する。
6. 監査隔離: ある記録を監査する推定（プローブ、信念更新、照合）に、その記録自身と派生信念を入力しない。条件付けベクトルは `AuditMask` を通す。
7. `belief` には較正済みの値だけを入れる。生スコアは `evidence` に残す。
8. 時刻は timezone-aware UTC の `datetime` のみ（float epoch・naive datetime 禁止）。期間は `timedelta`、区間は右開 `[start, end)`。
9. 生映像・音声を永続化しない。開発中は人の映るデータを扱わず合成データを使う。永続化は潜在と記号のみ。
10. 真値（オラクル）データは `sbp/testing` 以外から読まない（import 境界を lint で禁止）。
11. LLM を信念更新・照合・事象昇格のホットパスから呼ばない。用途は概念提案、文書からの構造化抽出、説明の文章化、NL→クエリ変換に限る。
12. シークレットは `.env`（gitignore 済み）のみ。コード・テスト・ログ・fixture に出さない。

## コーディング規約
- Python 3.12、`from __future__ import annotations`、型ヒント必須、pyright strict を通す。公開関数は 1 行要約＋引数の意味の docstring。
- 契約（イベント、アサーション、API 入出力、LLM 構造化出力、設定）は pydantic v2。内部の一時構造は `dataclass(frozen=True)`。
- ログは structlog。`event_id` / `correlation_id` / `entity` を bind する。`print` 禁止。
- 例外は `sbp.core.errors` の階層。握りつぶさない。リトライは eventlog の consumer 層と `llm.Provider` の 2 箇所だけ。
- 非同期は asyncio（aiokafka、httpx.AsyncClient、psycopg async）。CPU 重い処理（WM 推論・学習、clingo）は同期関数にして `asyncio.to_thread` で呼ぶ。
- 数値は numpy / torch。確率は log 空間の関数を優先（`logsumexp`）。乱数を使う関数は必ず `seed` 引数を取る。
- 命名: フルーエント・事象型はオントロジーのローカル名と一致させ（`Running`, `StopCmd`）、Python では `vocab.ent.Running` で参照する。
- 1 ファイル 400 行、1 関数 60 行を超えたら分割を検討。閾値・上限などの定数は `sbp.core.config.Settings`（pydantic-settings、`SBP_` プレフィクス）に置く。

## オントロジー変更の手順（コードより先）
1. `ontology/core|domain/*.ttl` に定義、`ontology/shapes/` に形状、必要なら `ontology/process/*.lp` に initiates / terminates。
2. `ontology/CHANGELOG.md` に semver 判定を記す（MAJOR: 削除・意味変更 / MINOR: 加法 / PATCH: 注釈）。MAJOR は ADR と再接地計画が必須。
3. `make gen` → `make ontology-check`。生成物（`vocab.py`, `schemas/`）はコミットに含め、生成差分は別コミットにする。
4. その後にコード。標準（BFO / IAO / SOSA / PROV / OWL-Time / GeoSPARQL / QUDT）にある概念を再定義しない。独自定義は接続概念（Situation, Discrepancy, Expectation, IdentityClaim, Track, Anchor, Probe, ModelVersion）に限る。

## テスト方針
- 単体: `tests/unit/<module>/`。外部 I/O は Protocol でモック。`make test` は Compose もネットワークも不要で通ること（`pytest-socket` で遮断）。
- 性質テスト（hypothesis）: 二時制の as-of / valid-at、様相格子、区間代数、冪等性。
- 契約テスト: `schemas/*.json` と pydantic モデルの往復、イベント envelope の後方互換、写像の不変条件。
- 統合（`-m integration`）: Compose 上で eventlog → knowledge → reasoning を `SynthWorld` の合成データで通す。
- 実データ・実 LLM をテストで使わない。LLM は `SBP_LLM_MODE=replay` の fixture。
- バグ修正は再現テストを先に追加する。カバレッジより「不変条件が壊れたら落ちる」テストを優先。

## LLM 利用ポリシー
- 入口は `sbp.llm.Provider` のみ（`complete_structured(messages, schema, task)`, `embed(texts)`）。他モジュールから SDK を直接呼ばない。
- プロバイダ・モデルは `.env`（`SBP_LLM_DEFAULT`, `ANTHROPIC_MODEL`, `OPENAI_MODEL`, `GEMINI_MODEL`, `SBP_EMBEDDINGS`）と `src/sbp/llm/routing.yaml`（task 別）で決める。コードにモデル ID を書かない。
- 出力は pydantic → SHACL の順に検証してから `inferred` で登録。来歴に `provider / model / prompt_version(hash)` を残す。
- `SBP_LLM_MODE=replay | record | live`。既定は `replay`。`live` は開発者が明示したときだけ。fixture が無ければ黙って live に落ちず失敗させる。

## インフラ（Docker Compose、arm64 イメージ）
| サービス | イメージ | ポート | 役割 |
|---|---|---|---|
| redpanda | `redpandadata/redpanda` | 9092 / 9644 | イベントログ（Kafka API） |
| postgres | `timescale/timescaledb-ha:pg16` | 5432 | 時系列（hypertable）、潜在索引（pgvector）、投影・メトリクス |
| oxigraph | `oxigraph/oxigraph`（healthcheck なし。ADR-0004） | 7878 | 知識グラフ（SPARQL 1.1 + RDF 1.2 の triple term。ADR-0005） |
| objectstore | `chrislusf/seaweedfs`（ADR-0003: MinIO が匿名取得不可になったため置換） | 9000 | raw 層・モデル重み（S3 互換） |
| console / grafana+prometheus | 任意 profile | 8080 / 3000 | 運用可視化 |
- SHACL は pyshacl、OWL RL は owlrl、事象計算・規範は clingo をアプリ側で実行する（ストアの推論機能に依存しない）。
- 単体テストでは pyoxigraph を組み込みで使う（サーバ不要）。
- 接続情報は `.env`（`.env.example` を必ず同期）。ローカル以外の URL・鍵をコミットしない。

## Git / PR / ADR
- ブランチ: `feat/<module>-<topic>`, `fix/...`, `ont/...`（オントロジー変更）。Conventional Commits（`feat(bridge): ...`）。
- PR に必ず書く: 触った層、不変条件への影響、オントロジー版、テスト種別、ADR リンク。
- 設計判断（ストア選定、モデル族、閾値既定値の変更、サービス追加）は `docs/adr/NNNN-<slug>.md`（MADR 形式）。判断理由は CLAUDE.md ではなく ADR に書く。

## 実装順序（マイルストーン）
- M0 骨格: core 型、vocab 生成、eventlog、knowledge（二時制 API）、Compose、`make check` 緑
- M1 統合: BusinessEvent 化、写像、信頼性モデル、計画・規範取込、書き戻し（dry-run）
- M2 接地: プローブ IF、較正、事象昇格、検証（潜在は `SynthWorld` の合成）
- M3 推論: 信念更新（三源）、照合（五類型）、説明
- M4 WM: koopman / jepa_ts、レジストリ、記号条件付け、health
- M5 閉ループ: アクション意味契約、Expectation 検証、予測・What-if、API
- M6 進化: 概念発見、エピステミック・スケジューラ、LLM 連携
- M7 運用: 再接地ジョブ、UI、可観測性
- 完了条件と現在地は `docs/STATUS.md`。M(n) の統合テストが緑になるまで M(n+1) に着手しない。

## Claude Code での進め方
- 大きな変更（新モジュール、オントロジー MAJOR、ストア変更、サービス追加）は plan mode で計画 → 承認 → 実装。
- 着手前に `docs/design/sbp-design.md` の該当章を読む。層ごとの詳細ルールは `.claude/rules/*.md`（paths 付き）にあり、該当ファイルを開くと自動で読み込まれる。
- 完了の定義: 型が通る、テストがある、`make check` 緑、docstring / ADR / CHANGELOG 更新、ログに相関 ID。
- 不明な業務仕様は推測で埋めず、`TODO(question)` と ADR の「未決」に残して質問する。**人の判断を待つ事項は `docs/HITL.md`** に集約する（どこにも全体が無い状態を作らない）。

## やってはいけないこと
- 「正」フラグや `is_true` 列を作る。真実ではなく信念を管理する。
- 状態テーブルを直接更新して整合させる。状態はイベントの投影。
- 静的精度（AUROC）だけでプローブを active にする（ECE と整合残差も AND 条件）。
- CUDA 前提・大規模モデルを持ち込む（MacBook で学習・推論できる数百万パラメータ級まで）。
- 便利だからと Redis 等の新サービスを増やす（既存 4 サービスで足りないことを ADR で示す）。
- オントロジーを飛ばしてコードで概念を定義する。
