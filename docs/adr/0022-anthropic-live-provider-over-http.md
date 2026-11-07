# ADR-0022: LLM の live 経路は anthropic のみとし、SDK を足さず HTTP で呼ぶ

- 日付: 2026-11-08
- 状態: 採択（H-04 の判断。利用者が「anthropic のみ・HTTP 直」を選んだ）
- 触る層: llm（プロバイダ）、core（設定）、services（learning-worker の配線）

## 文脈

`sbp.llm` は Provider 抽象・構造化出力・プロンプト版管理・replay を持つが、**live のプロバイダ実装が無い**
（`resolve_provider` に渡す `live_factory` を作る者がいない）。M8-09 の発見ループは replay の fixture があるときだけ
提案を出し、本番の概念提案・文書抽出・説明の文章化は動かせない。M8-10 は H-04（どのプロバイダを使うか）の判断待ちだった。

判断（2026-11-08）: **anthropic のみ。SDK を足さず HTTP で呼ぶ。** `SBP_LLM_DEFAULT` の既定が anthropic であること、
契約・コスト・データの所在の確認を 1 社分に絞れること、依存を増やさないこと（`httpx` は既に依存）が理由。

## 選択肢

### 問い 1: SDK か HTTP か

1. **HTTP 直（`httpx`）。** 依存が増えず、`httpx.MockTransport` でネットワーク無しにテストできる。API の形
   （Messages API）への追従は自前だが、使う面は小さい（構造化出力 1 本）。
2. 公式 SDK を任意依存にする。型と再試行は付くが、依存・ロックファイル・import-linter の契約（SDK は `sbp.llm`
   以外から import しない）の管理が増える。使う面が小さいので見合わない。

### 問い 2: 構造化出力の取り方

1. **tool use で強制する。** 期待する JSON Schema を `tools[].input_schema` にし、`tool_choice` で 1 つのツールを
   強制する。応答の `tool_use` ブロックの `input` が構造化出力。自由文の JSON をパースするより崩れにくい。
2. 自由文で JSON を返させてパースする。崩れた出力の扱いが増える。

### 問い 3: 再試行

**このプロバイダの中だけで行う**（CLAUDE.md: リトライは eventlog の consumer 層と `llm.Provider` の 2 箇所だけ）。
429 と 5xx（および接続エラー・タイムアウト）を上限つきの指数バックオフで再試行する。4xx（429 以外）は即失敗する
——入力が悪いので再試行しても直らない。

### 問い 4: 鍵

`ANTHROPIC_API_KEY` は `.env` にだけ置く（不変条件 12）。設定では `SecretStr` で持ち、**ログ・例外・fixture・来歴に出さない**。
鍵が無いのに `record` / `live` で起動したら失敗する（黙って replay や別経路に落ちない）。

## 決定

問い 1: HTTP 直。問い 2: tool use。問い 3: プロバイダ内で上限つきバックオフ。問い 4: `SecretStr`、無ければ失敗。

- `sbp.llm.anthropic.AnthropicProvider`（`name = "anthropic"`）。`embed` は**未対応を明示して失敗**する
  （Anthropic は埋め込み API を持たない。`SBP_EMBEDDINGS` は別のプロバイダを指す）。
- `sbp.llm.build_provider(settings)`: `replay` は fixture のみ（SDK も鍵も不要）。`record` / `live` は
  `AnthropicProvider` を `ReplayProvider(live=…)` に包む。fixture が無いときに黙って live に落ちない挙動（M6-02）は維持する。
- モデル ID は `.env` の `ANTHROPIC_MODEL`（コードに書かない）。ルーティング（`routing.yaml`）の `default` は `SBP_LLM_DEFAULT`。
  `SBP_LLM_DEFAULT` が anthropic 以外のとき、`record` / `live` は**失敗する**（他社の live は未実装）。

## 帰結

- **不変条件 11**: LLM を使う用途は変わらない（概念提案・文書抽出・説明の文章化・NL→クエリ）。ホットパスから呼ばない。
- **不変条件 12**: 鍵は `.env` のみ。`SecretStr` で、ログに出さないことをテストで固定する。
- **LLM 利用ポリシー**: 出力は pydantic で検証してから登録、来歴に `provider / model / prompt_version(hash)`、`record` は
  既存の fixture を上書きしない、`replay` は既定。
- **テスト**: `httpx.MockTransport`。成功、再試行、即失敗、出力崩れ、鍵が出ない、replay で transport を叩かない。
  実 LLM はテストで使わない（`llm_live` マーカーのテストも今回は作らない）。

## 未決

- **openai / gemini の live。** 未実装。使うなら別の ADR で、それぞれの構造化出力の方式を決める。
- **埋め込みの live。** `SBP_EMBEDDINGS` のプロバイダ（openai / gemini / local）。本リポジトリの 4 用途は埋め込みを使わない。
- **コストの上限。** 周期ごとの呼び出し回数の上限はあるが（M8-09）、トークン予算の上限は無い。
- **データの所在。** どの文書・不一致の説明が API に送られるか（個人データを送らない確認）は運用の判断。プロンプトに
  実体の IRI を載せない方針は M8-09 で守っている。
