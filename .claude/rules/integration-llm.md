---
paths:
  - "src/sbp/integration/**"
  - "src/sbp/llm/**"
---
# 業務統合層 / LLM のルール

## コネクタ（`sbp.integration.connectors`）
- 1 コネクタ 1 ファイル、`Connector` Protocol（`pull(since)`, `stream()`）。開発用の業務モックは `src/sbp/integration/mock/`（SQLite + 台本）。実システム接続は設定でのみ有効化する。
- 取り込んだ生レコードは `Record`（ICE）として保持し、`BusinessEvent` に変換する。`is_about` の対象解決は `resolver.py`。未解決は `UnresolvedRef` として残す（捨てない）。

## 写像（`mappings/`）
- RML / R2RML（`*.rml.ttl`）。写像ごとに `tests/contract/test_mapping_<name>.py` でキー保存・必須制約・多重度を検査する。
- 業務スキーマの変更は起動時のスキーマ指紋比較で検知し、例外ではなく `MappingDriftDetected` イベントとして出す。

## 情報源信頼性モデル（`reliability.py`）
- `DataSource × 記録型` ごとに時刻ずれ分布、誤記率、欠落率、系統誤差（月末一括・丸め・テンプレ複写）を持つ。初期値は `reliability/*.yaml`、更新は `update_from_resolution()`。
- 尤度は `likelihood(record, state) -> log p`。

## 計画・規範
- `PlanItem` → `planned`（有効時間 = 計画区間、確信度 = 遵守率の事前分布）。`Norm` → `.lp` 規則 + 出典（文書・節）。

## 書き戻し（`writeback.py`）
- 冪等 `command_id`。応答は `BusinessEvent` として取り込む。補償アクションを必ず定義する。既定は `SBP_WRITEBACK_MODE=dry`。

## LLM（`sbp.llm`）
- `Provider` 実装は `anthropic.py`（anthropic SDK）、`openai.py`（openai SDK）、`gemini.py`（google-genai SDK）。共通 IF は `complete_structured(messages, schema, task)` と `embed(texts)`。
- `task` 別の既定プロバイダは `routing.yaml`。モデル ID は `.env` からのみ読む。
- 出力は pydantic → SHACL の順で検証し `inferred` として登録する。文書抽出のスキーマは根拠スパン（文書内位置）を必須にする。
- レート制限・リトライ・タイムアウトは `Provider` 内で一元管理（tenacity）。トークン使用量を structlog に出す。
- `SBP_LLM_MODE=replay` ではネットワークを使わない。fixture が無ければ失敗させる（黙って live に落ちない）。fixture は `tests/fixtures/llm/<task>/<hash>.json`。
- プロンプトは `src/sbp/llm/prompts/<task>/<version>.md`。変更は version を上げ、hash を来歴に残す。
