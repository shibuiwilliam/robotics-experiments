---
paths:
  - "src/gtwm/llm/**"
  - "configs/llm.yaml"
---

# LLM の使い方（Anthropic / OpenAI / Gemini）

## 原則
- LLM は制御ループと物理予測に入れない。許可されるタスクは次の6つだけ：(1) オントロジーのラベル・コメント下書き、(2) SPARQL 候補生成、(3) 概念候補の命名と説明、(4) 自然言語→WHAT-IF クエリ変換、(5) レポート下書き、(6) 生成エピソードの QA（映像を見て意図したシナリオが起きているかの確認）と LLM-as-judge（人によるサンプル監査付き）。
- 呼出は `gtwm.llm.client.LLMClient` のみ。`anthropic` `openai` `google-genai` の SDK を他のモジュールから直接 import しない。

## クライアント設計（`src/gtwm/llm/client.py`）
- プロバイダは `anthropic` `openai` `gemini` `mock` の4つ。テストは常に `mock`。
- モデル名はコードに書かず `configs/llm.yaml` の `tasks.<task>.provider/model` で決める。起動時に `gtwm llm ping` で各プロバイダの疎通と設定モデルの実在を確認し、失敗したら他のプロバイダに自動で切り替えず、エラーにする。
- 既定の割当（変更可）：文章・推論系タスクは anthropic、JSON スキーマ厳格出力が要るタスクは openai、映像・画像の QA は gemini。
- 出力は pydantic モデルで検証する。JSON を期待するタスクは温度 0、`max_tokens` 明示、再試行は2回まで。
- キャッシュ：SQLite（`.data/llm_cache.sqlite`）、キーは (provider, model, prompt hash, schema hash)。同一入力で再課金しない。
- 記録：`runs/llm_usage.jsonl` に task, provider, model, 入出力トークン数, 概算費用, 所要時間を追記。費用単価は `configs/llm.yaml` の `pricing` から読む（不明なら null で記録し、推定しない）。
- 上限：`LLM_MONTHLY_BUDGET_USD`（`.env`）を超えたら呼出を拒否する。月次集計は `gtwm llm usage`。

## プロンプト
- プロンプトは `src/gtwm/llm/prompts/<task>.md` にファイル化し、コードに文字列で書かない。変更は差分が見えるようにコミットする。
- 概念命名（タスク 3）は「候補名（ja/en）、定義文、既存クラスとの関係、根拠となる残差の要約」を JSON で返させ、`gt:ConceptCandidate` に保存する。オントロジーへの追加は人が行う。
- LLM-as-judge を使う指標は、少なくとも 30 件を人が再採点して一致率（Cohen の κ）を `docs/results/` に書く。κ < 0.6 なら判定に使わない。

## 秘密情報
- API キーは環境変数（`ANTHROPIC_API_KEY` `OPENAI_API_KEY` `GEMINI_API_KEY`）からのみ読む。ログ・例外メッセージ・キャッシュにキーを含めない。
- 生成データに含まれる映像は自前のシミュレーション出力に限る。外部から得た映像・個人情報を LLM に送らない。
