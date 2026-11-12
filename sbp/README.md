# SBP — Semantic Bridge Platform

[English](README.en.md) | 日本語

**物理世界の観測と、業務システムの記録を、同じ土俵で突き合わせる「信念維持システム」。**

工場や倉庫では、センサ・カメラが見ている「現実」と、ERP / MES / WMS / CMMS が記録している「帳簿」が、しばしばずれます。
SBP はこのずれを見つけ、起票し、人が解決し、そこから学ぶ基盤です。

- **真実ではなく信念を持つ。** すべての主張に、確信度・来歴・様相（観測 / 推論 / 予測 / 計画 / 規範 / 仮説）が付きます。
- **物理と業務を同じ尺度で比べる。** ワールドモデル（WM）とオントロジーで、語彙・時間軸・確信度をそろえます。
- **不一致を一級のオブジェクトにする。** 検出 → 起票 → 解決 → 学習の流れを、基盤が持ちます。

> 本リポジトリは**基盤本体だけ**です。実機やサンドボックスでのユースケース検証は、別リポジトリで行います（ADR-0001）。

## 目次

[しくみ](#しくみ) ・ [はじめる](#はじめる) ・ [よく使うコマンド](#よく使うコマンド) ・ [ディレクトリ](#ディレクトリ) ・ [現在地](#現在地) ・ [ドキュメント](#ドキュメント) ・ [開発に参加する](#開発に参加する)

## しくみ

```
 物理の観測 ──► encode ──► 潜在索引 ──► プローブ ──► 事象昇格 ──┐
 (センサ等)    (WM)       (pgvector)   (較正済み)              │
                                                              ▼
                                                    ┌──── 信念更新 ────┐ ──► 知識グラフ
 業務システム ─► ingest ──► 写像 ──► 業務イベント ───────────►│  三源を合成     │      (Oxigraph)
 (ERP/MES…)    (DSL)                                          └────────┬────────┘
 計画・規範 ───────────────────────────────────────────────────────────┘
                                                                       │
                                  不一致の検出（照合）◄─────────────────┘
                                          │
                       人が解決（API・UI）◄┘──► 予測 / What-if / アクション
```

- 三つのデータ経路: **記録**（業務システム → イベント）、**推定**（観測 → 潜在 → プローブ → 事象）、**信念更新**（三源を合成）。
- 土台は Docker Compose の 4 サービス: Redpanda（イベントログ）、Postgres + Timescale + pgvector、Oxigraph（知識グラフ）、SeaweedFS（オブジェクト置き場）。
- 層の依存は一方向です（import-linter で機械検査）。

  ```
  core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge ← reasoning ← actions ← api
  ```

詳しくは `docs/design/sbp-design.md`（正典）を、必要な章だけ読んでください。

## はじめる

**前提**: macOS 14+ / Apple Silicon（Intel は `SBP_PLATFORM=linux/amd64`）、Docker Desktop または OrbStack、Homebrew、[uv](https://docs.astral.sh/uv/)、Python 3.12。UI を触るときだけ Node 20+。

```sh
make bootstrap   # 依存の同期 → .env を .env.example から生成 → イメージ取得
make up          # Compose 起動（redpanda / postgres / oxigraph / objectstore）
make seed        # オントロジー投入・トピック作成・マイグレーション・バケット作成
make dev         # API を起動（http://localhost:8000、ホットリロード）
```

動いたか確かめる:

```sh
make ps                          # 4 サービスが healthy か
curl -s localhost:8000/health    # API の応答
make check                       # lint + 型 + 層依存 + オントロジー + シークレット走査 + 単体テスト
```

ワーカーは既定では起こしません。必要なものを選びます。

```sh
uv run python -m scripts.dev --workers belief,reconcile
```

停止は `make down`（データは残る）。データごと消すのは `make nuke`（唯一の削除経路）。

> **シークレット**: API キーなどは `.env`（gitignore 済み）だけに書きます。コード・テスト・ログ・fixture には出しません。LLM は既定で `replay`（fixture のみ）で、`live` は明示したときだけ動きます。

## よく使うコマンド

| やりたいこと | コマンド |
|---|---|
| コミット前の総合確認（必ず緑にする） | `make check` |
| UI も含めて確認 | `make ui-install` → `make check-all` |
| 単体テスト（Compose・ネットワーク不要） | `make test` |
| 統合テスト（Compose 必須） | `make test-int` |
| オントロジーから `vocab.py` / `schemas/` を再生成 | `make gen` → `make ontology-check` |
| 観測を潜在にして索引へ書く | `make encode` |
| WM（Koopman）を学習する | `make train-wm SEED=…` |
| プローブを学習する（ゲートを通ったものだけ置く） | `make train-probe FLUENT=… PROBE_ID=… SEED=…` |
| 過去の信念を新しい版で再計算する（既定は dry-run） | `make reground ARGS='--entity … --apply'` |
| 予測を作って採点する | `make forecast ARGS='--entity … --fluent … --probe-id … --seed N'` |
| 介入あり／なしの推移を並べる（What-if） | `make whatif ARGS='--entity … --action-type … --seed N'` |
| 依存の脆弱性を照会する | `make audit` / `make ui-audit` |
| コマンド一覧 | `make help` |

運用の手順は `docs/runbooks/`（ローカル環境、再接地、版昇格、What-if、障害対応、バックアップ）にあります。

## ディレクトリ

```
ontology/    オントロジー（core / domain / shapes=SHACL / process=clingo / norms）
schemas/     生成物: EventType 由来の JSON Schema（手編集しない）
src/sbp/     本体（core, eventlog, knowledge, integration, worldmodel, bridge,
             reasoning, actions, llm, api, testing）
services/    ワーカー 6 本（bridge / belief / reconcile / scheduler / learning / ingest）
scripts/     ジョブ（encode, train-*, reground, forecast, whatif）
apps/ui/     UI（不一致キュー・信念ビュー・期待ボード・概念 curation）。localhost 限定
infra/       compose.yaml、DB マイグレーション（alembic）
mappings/ reliability/ …   写像・信頼性などの設定
docs/        設計書・ADR・運用手順・進捗・人の判断待ち
tests/       unit / integration / contract / property
```

## 現在地

**M0〜M10 完了**（ゲート G0〜G10 通過、2026-11-08）。

| 項目 | 状況 |
|---|---|
| 自動テスト | `make check` 1952 件、統合 236 件、UI 126 件が緑 |
| 運用の入口 | 記録・推定・信念更新・照合・再接地・予測・隔離キュー・What-if が API／ジョブから通る |
| 未検証 | **実データでは検証していません**（合成データと業務システムのモックのみ） |
| 残り | 人の判断待ち 23 件（`docs/HITL.md`）。同一性束縛・LLM を使う機能の入口など |

CI の定義は `.github/workflows/ci.yml`（`check` → `integration`、`audit`、`ui`）にありますが、**一度も実行されていません**。
このリポジトリは別プロジェクトを含むリポジトリの `sbp/` に置かれており、GitHub Actions はリポジトリ直下の `.github/` しか読まないためです（`docs/HITL.md` H-23）。

経過と数字は [`REPORT.md`](REPORT.md)（開発・検証のまとめ）と `docs/STATUS.md` を見てください。

## ドキュメント

| まず読む | 内容 |
|---|---|
| [`REPORT.md`](REPORT.md) | これまでの開発と検証の要約（英語: `REPORT.en.md`） |
| `docs/design/sbp-design.md` | **正典**の詳細設計書（全 18 章 + 付録）。実装前に該当章だけ読む |
| `docs/STATUS.md` | マイルストーン進捗と規模の実測 |
| `docs/HITL.md` | **人の判断を待つ事項**の管理簿 |
| `docs/adr/` | 設計判断の記録（MADR 形式、36 件。テンプレは `0000-template.md`） |
| `docs/runbooks/` | 運用手順 |

英語版は `README.en.md`、`REPORT.en.md`、`docs/en/`（設計書・ADR・runbook・STATUS・HITL）にあります。**日本語が正典**で、数字は遅れることがあります。

<details>
<summary>その他の開発文書</summary>

| パス | 内容 |
|---|---|
| `CLAUDE.md` | 毎セッション読み込まれる基幹ルール（層依存・不変条件 12 箇条・規約） |
| `docs/development-rules.md` | ディレクトリ構成・ファイルの役割・開発規約の整理 |
| `docs/TASK.md` | 開発計画・タスク管理（品質ゲート、タスク分解） |
| `docs/PROMPT.md` | 開発遂行用プロンプト集 |
| `docs/PROGRESS.md` | 開発経過の記録（判明した事実、既知の制約） |
| `.claude/rules/*.md` | 層別の詳細ルール（該当ファイルを開くと自動で読み込まれる） |
| `.env.example` | 全環境変数の一覧 |

矛盾があるときの優先順位: 設計書 → `CLAUDE.md` → その他。

</details>

## 開発に参加する

- 1 タスク 1 ブランチ。コミットは Conventional Commits（`feat(bridge): …`）。`make check` を緑にしてからコミットします。
- 設計から逸れる判断は、先に `docs/adr/` に ADR を書きます。オントロジーの変更はコードより先です（手順は `CLAUDE.md`）。
- 守る不変条件（抜粋）: アサーションは追記専用で UPDATE / DELETE しない／`modality` は 6 種のみで LLM・規則・プローブの出力は必ず `inferred`／LLM を信念更新の経路から呼ばない／IRI は `sbp.core.vocab` からだけ参照する。全 12 箇条は `CLAUDE.md`。
- 生成物（`schemas/`、`src/sbp/core/vocab.py`、`ontology/imports/`）は手で編集せず、`make gen` で作ります。
- Claude Code で開発する場合: 起動して `/context` を実行し、Memory files に `CLAUDE.md` が出ていることを確認します。`.claude/settings.json` が有効なら、`.env` の読み取りと生成物の編集は自動的に拒否されます。
