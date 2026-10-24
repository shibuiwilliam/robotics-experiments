# SBP — Semantic Bridge Platform

ワールドモデル（WM）× オントロジーで、物理世界の観測（センサ・カメラ・設備）と業務システムの記録（ERP/MES/WMS/CMMS・計画・規程）を、同じ語彙・同じ時間軸・同じ確信度尺度の上で突き合わせる「信念維持システム」。真実ではなく信念（確信度・来歴・様相付きの主張）を管理し、物理と業務の不一致を一級オブジェクトとして検出→起票→解決→学習する。

本リポジトリは基盤本体のみを扱う。ユースケース検証（実機・サンドボックス実験）は別リポジトリで行う。

## クイックスタート

前提: macOS 14+ / Apple Silicon、Docker Desktop または OrbStack、Homebrew、uv、Python 3.12。

```sh
make bootstrap   # brew bundle → uv sync → .env 生成 → docker compose pull
make up          # Compose 起動（redpanda / postgres / oxigraph / objectstore）
make seed        # オントロジー投入・トピック作成・マイグレーション
make dev         # API（:8000）とワーカーをホットリロードで起動
make check       # lint + 型 + 層依存 + ontology-check + シークレット走査 + 単体（コミット前に必ず緑）
make test-int    # 統合テスト（Compose 必須）
```

UI（`apps/ui`、Node 20+）を触るときは `make ui-install` → `make check-all`
（`make check` + UI の型検査とテスト）。`make check` に UI を入れていないのは、
**追加のインストール無しで必ず走る**状態に保つためである。

コマンド一覧と開発規約は `CLAUDE.md`、詳細は `docs/development-rules.md` を参照。

> 現在地: **M0〜M7 完了**（ゲート G0〜G7 通過）/ **M8「配線」進行中（16 / 30）**（2026-10-25 棚卸し）。
> ゲート G8 の 3 条件のうち 2 つ（G0〜G7 維持、`TODO(question)` 0 件）は満たしている。
> 残るのは M8 の 14 件（うち 7 件は各データパスの先頭を運用で書く経路）、**人の判断を待つ 24 件**
> （`docs/HITL.md`）、「実データに当てるまで確かめられない」種類の制約である（`docs/STATUS.md`、
> `docs/PROGRESS.md` の既知の制約）。コマンドの記述の一部は実装とずれている（`docs/TASK.md` X-12 / X-13）。

CI は `.github/workflows/ci.yml`。3 ジョブに分かれ、`check`（Compose 不要）が落ちたら
`integration`（Compose 必須）は走らない——型が通らないのに 5 分待っても情報は増えない。

## ドキュメントマップ

| パス | 内容 |
|---|---|
| `docs/design/sbp-design.md` | **正典**となる詳細設計書（v0.1、全 18 章 + 付録）。実装前に該当章だけ読む |
| `CLAUDE.md` | 毎セッション読み込まれる基幹ルール（層依存・不変条件 12 箇条・規約） |
| `docs/development-rules.md` | ディレクトリ構成・ファイルの役割・開発規約の統合整理 |
| `docs/TASK.md` | 開発計画・タスク管理（スコープ・品質ゲート・M0〜M8 タスク分解・運用・リスク） |
| `docs/PROMPT.md` | 開発遂行用プロンプト集（セッション運営・タスク実行・マイルストーン別・横断・運用・リカバリ） |
| `docs/PROGRESS.md` | 開発経過の記録（実装中に判明した事実、ADR に至らなかった判断、既知の制約） |
| `docs/adr/` | 設計判断の記録（MADR 形式、15 件。テンプレは `0000-template.md`） |
| `docs/HITL.md` | **人の判断を待つ事項の管理簿**（優先度順。決まったら ADR を起票して本書から消す） |
| `docs/STATUS.md` | マイルストーン進捗（M0〜M8）と規模の実測。里程の粒度はここだけに書く |
| `.claude/rules/*.md` | 層別の詳細ルール（paths 連動で該当ファイルを開くと自動読込） |
| `.claude/settings.json` | Claude Code のガードレール（ルートの `settings.json` はここへのシンボリックリンク） |
| `docs/runbooks/` | 運用手順（再接地 / 版昇格 / 障害対応 / バックアップ・リストア / ローカル環境） |
| `apps/ui/` | UI（不一致キュー・信念ビュー・期待ボード・概念 curation。ADR-0008） |
| `.env.example` | 全環境変数の一覧（`.env` はコミットしない） |

優先順位: 矛盾があれば 設計書 → CLAUDE.md → その他ドキュメント の順に従う。

## Claude Code での開発

セットアップ確認: Claude Code を起動して `/context` を実行し、Memory files に `CLAUDE.md` が出ていること。`.claude/settings.json` が有効なら、`.env` の読み取りと生成物（`schemas/`, `src/sbp/core/vocab.py`）の編集は自動的に拒否される。
