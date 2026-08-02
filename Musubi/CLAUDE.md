# CLAUDE.md — Musubi 開発運用マニュアル

このファイルは **Claude Code（および開発者）がこのリポジトリで作業するための実務マニュアル（HOW）** である。プロジェクトの目的・要件・アーキテクチャの定義（WHAT/WHY）は `PROJECT.md`、意味の正典は設計書・実験計画・シナリオカタログにある。**本書は操作・規約・手順に徹する。定義を知りたくなったら PROJECT.md を読む。**

> このファイルは毎セッション読み込まれる。高信号・低冗長を保つこと。概念の再説明はしない（PROJECT.md へリンク）。本書を肥大させない。

---

## 0. まず守る掟（Golden Rules）

違反はレビューで即差し戻し。これらは §7 の不変条件の要約であり、最優先。

1. **LLM を `clients/` の外で呼ばない（単一クラウド境界）。** ER・エージェント推論・埋め込みへのアクセスは必ず VCR 経由（`clients/`）。他モジュールから `google.genai` / `google.adk` / `anthropic` を直接 import しない（`clients/guard.py` が強制）。**D-0012**：エージェント推論 LLM は registry で選択（`llm.provider`：gemini | claude、既定 claude）。Claude を主エンジンに採用（Gemini-only の緩和・ユーザ承認済）だが出口は `clients/` 一点のまま。ER・埋め込みは Gemini 継続。オフラインは Fake/カセットで決定的（replay で API 呼出 0）。
2. **モデルID・単価・予算・閾値をコードに書かない。** すべて `config/registry.yaml`。ハードコードされたモデル名を見たら直す。
3. **Claim は追記のみ。** 更新は `supersede`、削除はしない。真値スナップショットをシステム経路に混ぜない（採点専用）。
4. **オントロジーは単一ソース。** 型・スキーマ・語彙を手書きしない。`ontology/src/`（LinkML）を編集し `make gen` で再生成する。
5. **決定性を壊さない。** 素の `time.time()` / `datetime.now()` / 未シードの `random` を本番経路で使わない。時刻はシムクロック／シナリオから、乱数はシード付き RNG から取る（→ NFR-DETERM）。
6. **人物を同定しない。** 人に対する `IdentityBinding` を作らない。匿名の所在・姿勢のみ（→ NFR-P）。
7. **秘密をコミットしない。** APIキーは `.env`。`config/registry.yaml` にキー実体を書かない。
8. **完了前に `make check` を通す。** lint・型・テスト（VCR replay）が緑でなければ「完了」ではない。
9. **依存の向きを守る。** `ontology → 各所`、クラウド出口は `clients` のみ、`scoreboard` は読み取り専用。循環を作らない（→ PROJECT.md §6.2）。
10. **変更したら追跡を更新する。** 機能を足したら trace matrix（PROJECT.md §15.3）とテストを更新。WHAT/WHY が変わったら PROJECT.md、HOW が変わったら本書。

---

## 1. 技術スタックとツールチェーン

| 目的 | ツール | 備考 |
|---|---|---|
| 言語 | Python 3.11+ | 型ヒント必須 |
| 環境・依存 | `uv` | `uv venv` / `uv pip` / `uv.lock`。pip でも可だが uv を標準とする |
| Lint・整形 | `ruff` | `ruff check` ＋ `ruff format` |
| 型検査 | `mypy` | strict 寄せ（下記設定） |
| テスト | `pytest` | 既定は VCR **replay** モード（ネット不要） |
| フック | `pre-commit` | commit 時に ruff・秘密検出・mermaid 検証 |
| タスク実行 | `make` | **正規インターフェースは make ターゲット**（§3） |
| 物理 | `mujoco` | オフスクリーンは `mujoco.Renderer` |
| エージェント | `google-adk` | `LlmAgent` ＋カスタム FunctionTool |
| LLM/埋め込み | `google-genai` | ただし呼出は `clients/` 内のみ |
| オントロジー | `linkml`（生成）・`pyshacl`（検証） | 生成物は Git 管理 |
| 保存 | SQLite（Claim/カセット/埋め込み）・DuckDB（指標） | ローカル完結 |
| モック | `FastAPI`＋SQLite | 外部業務システム |

---

## 2. 環境セットアップ

```bash
# 1) 依存とツールの導入
make setup            # uv venv 作成 → 依存インストール → pre-commit 設定

# 2) APIキー（VCR replay だけなら不要）
cp .env.example .env
# .env に GOOGLE_API_KEY=... を記入（コミット禁止）

# 3) オントロジー生成物を作る
make gen              # ontology/src → ontology/generated

# 4) 動作確認（ネット不要）
make check            # lint + types + test(replay)
```

### macOS の要点（`mjpython` vs `python`）

- **対話ビューア／デモ録画**は `mjpython` 必須（`viewer.launch_passive()` の制約）。
- **ヘッドレスの実験ラン**（`mj_step` ループ・`Renderer` のオフスクリーン）は通常の `python` でよい。
- make ターゲットが自動で使い分ける：`make demo` は `mjpython`、`make experiment` / `make scenario` は `python`。手で起動するときだけ意識する。

---

## 3. よく使うコマンド（正規インターフェース）

`make` ターゲットを正とする。裏で走る実コマンドは Makefile 参照。

| コマンド | 何をするか |
|---|---|
| `make setup` | 環境構築（venv・依存・pre-commit） |
| `make gen` | LinkML からオントロジー生成物を再生成（編集後は必ず） |
| `make fmt` | `ruff format` で整形 |
| `make lint` | `ruff check`（＋整形差分チェック） |
| `make types` | `mypy` 型検査 |
| `make test` | `pytest`（**VCR replay**・ネット不要・CI と同じ） |
| `make test-live` | ライブ API を叩く（**record** モード・鍵と予算が要る・§8） |
| `make check` | `lint + types + test` — **PR 前ゲート／完了条件** |
| `make scenario S=<name> [MODE=replay|record] [ARM=A4]` | シナリオ1本を実行 |
| `make experiment E=<E0..E7>` | 実験プログラム1本（アーム×シード展開） |
| `make dashboard` | 意味的可観測性ダッシュボードを生成・表示 |
| `make demo S=<name>` | `mjpython` ビューア＋GIF録画でデモ実行 |
| `make docs-check` | Markdown 内の Mermaid を構文検証 |
| `make report E=<E>` | 実験結果レポートを生成（`scoreboard/reports/`） |

---

## 4. リポジトリの歩き方

全体構成は PROJECT.md §13。開発者としての勘所だけ：

- **新しい意味（型・関係）** → `ontology/src/` を編集 → `make gen`。手書きの型定義を増やさない。
- **物理の挙動・撹乱** → `sim/`（`worlds/` MJCF、`skills/`、`invisible_hand/`、`render/`）。
- **信念・調停・規範・説明** → `core/`。
- **ロボット知覚** → `perception/`（ダイヤル・pixel→world）。ER 呼出自体は `clients/`。
- **エージェントの思考・道具** → `agents/`（`tools/`、`capability_compiler/`）。
- **業務システム・ポータル・文書** → `external/`。
- **クラウド呼出** → `clients/`（VCR）。ここ以外に置かない。
- **実験の回し方・シナリオ** → `bench/`（`scenarios/` DSL、`runner/`、`PREREG.md`）。
- **指標・可視化・レポート** → `scoreboard/`。
- **可変値** → `config/registry.yaml`。

---

## 5. コーディング規約

- **命名はユビキタス言語に従う**（PROJECT.md §8）。`Claim`、`IdentityBinding`、`ReversibilityClass`、`Realm` などはコード識別子でもそのまま使う。同義語を作らない（`Assertion`≠`Claim`、勝手に言い換えない）。
- **型ヒント必須**。公開関数・データ構造は完全注釈。`mypy` 緑を維持。
- **裸の数値・裸の参照を作らない**（設計原則 P1/P2）。量は単位（QUDT）付き、座標はフレーム付き、インスタンス横断参照は IRI。数値リテラルの直書きは `config` か定数モジュールへ。
- **境界メッセージは意味エンベロープ**（JSON-LD、`@context` は生成物）。`sim ⇄ core` はこれで話す。
- **時刻はバイテンポラル**（validTime/transactionTime）を意識。「いつ真だったか」と「いつ記録したか」を混同しない。
- **エラーは握り潰さない**。想定内の逸脱は `Exception`（ドメイン概念、§設計書3.8）としてバスに出す。実行時例外はログに出所付きで残す。
- **ログは構造化**。Case/Episode の IRI をトレースキーに含める（可観測性）。
- **副作用の局在**：`scoreboard/` は観測専用（本番経路に書き込まない）。純粋関数を優先。
- **docstring は「何を・なぜ」**。実装の how はコード自身に語らせる。設計判断は trace matrix か決定ログへ。

---

## 6. テスト戦略

| 層 | 内容 | 実行 |
|---|---|---|
| ユニット | モジュール内ロジック（調停規則、減衰、包摂判定、pixel→world 等） | `make test` |
| スキーマ検証 | 生成 JSON Schema / SHACL に対するサンプルデータ適合 | `make test` |
| 決定性 | 同一シードで2回実行 → `qpos` 軌跡 bit 一致／replay で **API 呼出 0** をアサート | `make test` |
| シナリオ oracle | DSL の oracle（シム真値・SHACL・植え付け正解）で成否判定 | `make scenario` |
| 契約 | ER 座標規約・出力スキーマ、能力マッチング、ADK ツール I/O | `make test` |
| ドキュメント | Markdown の Mermaid 構文 | `make docs-check` |

規律：

- **CI と `make test` は必ず VCR replay**（ネットに出ない）。カセットはリポジトリにコミットする。
- 新しい LLM 入力を要するテストは、一度 `make test-live`（record）でカセットを作り、生成物をコミットしてから replay に固定する。
- LLM 出力を前提に**断定的アサートをしない**。構造（スキーマ適合・不変条件・IRI 解決可能性）を検証する。生成の揺れは統計で扱う（実験計画 §8）。
- 不可逆・安全に関わるテスト（未承認不可逆 0 件）は**必達アサート**として扱い、flaky を許さない。

---

## 7. アーキテクチャ不変条件（詳細）

掟（§0）の背景と適用。

- **クラウド境界は一点**：Gemini 三種は `clients/` の VCR インターフェースの背後にのみ存在する。これが再現性・コスト・モデル移行（ER 1.6→2）を成立させる。破ると全部壊れる。
- **オントロジーが上流**：`ontology/` は何にも依存しない。型・スキーマ・語彙集・SHACL はここから生成し、全モジュールが同一版を参照する。生成物もコミット（再現性のため）。
- **Claim の不変性**：追記のみ・supersede で置換・削除なし。これがバイテンポラル照会（フォレンジック、S5/E2）を可能にする。
- **決定性の局在**：非決定は LLM のみ。それ以外（物理・調停・採点）は決定論的で、シードで完全再現できる。ゆえに時刻・乱数の出所を厳格に管理する。
- **権威はアスペクト単位**：グローバルマスタを作らない。位置の権威と在庫数量の権威は別（PROJECT.md §8 Authority）。
- **能力で疎結合**：エージェントは特定ロボットを知らず、能力（Capability）で話す。新機体追加でエージェントコードを変えない（E4 の検証対象）。

---

## 8. 外部 API の実務（`clients/` 内で）

### VCR モード

- `replay`（既定）：カセットのみ。未収録呼出は **`MissingCassette` を投げて顕在化**（CI で事故を検出）。
- `record`：未収録のみライブ呼出して保存。既存は再生。
- `passthrough`：常にライブ（デバッグ限定）。
- キー＝`hash(モデルID, 正規化リクエスト)`。画像はコンテンツハッシュで重複排除。**プロンプトに時刻・乱数・実行ごとに変わる値を混ぜない**（キャッシュが効かなくなる）。

### Gemini Robotics（ER）

- モデルID は `config/registry.yaml`（例 `gemini-robotics-er-1.6-preview`。ER 2 は差し替えのみ）。
- 座標は**正規化 `[y,x]`（0–1000）**、箱は `[ymin,xmin,ymax,xmax]`。world 化はシム深度で持ち上げ（実験計画 §4.2、`perception/`）。
- 出力は**構造化出力（生成 JSON Schema 指定）で受け、即検証**。検証失敗は「知覚例外」Claim にする（LLM を信用しない）。
- 検出は thinking budget 低め、関係推論は高め。budget も registry 管理。
- プレビュー仕様。座標規約・スキーマの**契約テスト**を常設し、破壊的変化を検出する。

### ADK（エージェント）

- `LlmAgent` ＋**カスタム FunctionTool のみ**。組込みツール（検索・コード実行）と混在させない（制約回避）。必要なら別エージェントに分離。
- ツールの入出力スキーマはオントロジー生成物から。手書きしない。
- 429 は `HttpRetryOptions`＋レートリミッタ＋日次予算ガード。ベンチは**ステートレス呼出**（Interactions API を使わない＝VCR 再生を壊さない）。対話デモのみ例外可。
- 委任は ADK 内部呼出と同時に Musubi の `Delegation` Claim を記録（説明責任チェーンと一致させる）。

### Embedding

- `gemini-embedding-2`（マルチモーダル）。既定次元 768（MRL、切詰め自動正規化）。`gemini-embedding-001` と**混在禁止**（空間非互換）。
- 全埋め込みは `hash(モデル,次元,内容)→ベクトル` でローカルキャッシュ。**二度課金しない**。
- 大量埋め込みは Batch API。タスク指示はプロンプト接頭辞方式。

---

## 9. 開発プレイブック（頻出手順）

### 9.1 シナリオを1本足す

1. `bench/scenarios/<name>.yaml` を DSL に従い記述（初期世界・帳簿乖離・見えざる手・故障・`norms_active`・oracle・arms・repeats・seed）。
2. 必要なら `ontology/shapes/` に受け入れ SHACL、`external/` にモックを追加。
3. `make scenario S=<name> MODE=record ARM=A4` を一度回してカセット生成 → コミット。
4. oracle が機械判定で通ることを確認 → `make scenario S=<name>`（replay）。
5. `make check`。
6. シナリオカタログ §4 マトリクスと trace matrix を更新。

### 9.2 ロボット／能力を足す（コード0行が目標）

1. `sim/worlds/` に MJCF（body/アクチュエータ/カメラ）を追加（include 推奨）。
2. 能力記述（Capability YAML）を `core/registry`（または config）に追加。
3. **`agents/` のコードは変更しない** — capability compiler が道具を生成する。
4. 契約テスト：その actionType の要求が能力に包摂されることを確認。
5. `git diff` がエージェントロジックに触れていないことを確認（E4 の不変条件）。

### 9.3 オントロジー概念を足す／変える

1. `ontology/src/`（LinkML）を編集。
2. **二重表現**：SHACL 制約＋自然言語定義（定義文・使用例・反例）を必ずセットで。
3. `make gen` → 生成物を再生成しコミット。
4. SemVer 更新（追加=minor、破壊=major）。互換チェックを通す。
5. 影響分析：その概念を使うモジュール／エージェントを確認。

### 9.4 エージェントツールを足す

1. `agents/tools/` に FunctionTool を定義。入出力スキーマは生成物から。
2. `LlmAgent` に登録（組込みツールと混ぜない）。
3. replay モードのテストを追加（想定入力→構造アサート）。

### 9.5 外部システムモックを足す

1. `external/<system>/` に FastAPI＋SQLite で実装。**意図的な不完全さ**（帳簿乖離を張れる口）を持たせる。
2. バスとは意味エンベロープで接続。
3. シナリオ DSL から初期状態を宣言できるようにする。

### 9.6 アブレーション実験を回す

- `make experiment E=E7` が A0–A4 × シードを展開し、`scoreboard/` に集計。`make report E=E7` で図表化。
- 主要評価項目は事前に `bench/PREREG.md` に記載してからコミット（後出し禁止）。

---

## 10. Git／ブランチ／コミット／PR

- ブランチ：`feat/<topic>` `fix/<topic>` `exp/<Ex>` `onto/<change>` `docs/<topic>`。`main` へ直接 push しない。
- コミットは Conventional Commits（`feat:` `fix:` `test:` `onto:` `exp:` `docs:` `chore:`）。小さく・意味単位で。
- **生成物（`ontology/generated/`）とカセットはコミットする**（再現性）。秘密・大容量バイナリはコミットしない。
- PR 前に `make check` 緑。PR 説明に「対応する FR/NFR・実験E・シナリオ」を書く（トレーサビリティ）。
- 安全・不可逆・プライバシーに関わる変更は説明を厚く。

---

## 11. Claude Code 作業規約（あなたへ）

このリポジトリで作業するときの振る舞い。

- **大きな変更は計画してから**。3モジュール以上に跨る、またはオントロジー破壊的変更を含む場合、着手前に方針を1〜2段落で示す。
- **掟（§0）を毎回確認**。特に「Gemini は clients のみ」「モデルIDは registry」「Claim は追記のみ」「決定性」「人物非同定」。
- **モデル名・API 仕様を推測で書かない**。registry を見る。不明ならライブ確認は `make test-live` を提案し、勝手に本番へ出さない。
- **完了の定義**（§13）を満たすまで「完了」と言わない。特に `make check` 緑・テスト追加・ドキュメント整合。
- **コスト意識**。既定は replay。ライブ呼出（record）は必要最小限で、予算（registry）と PREREG を確認してから。
- **PROJECT.md との境界を守る**。WHAT/WHY を書きたくなったら PROJECT.md へ。本書には HOW だけ。
- **既存の語彙・パターンに合わせる**。新しい抽象を足す前に、ユビキタス言語と既存モジュールを確認。
- **不明点は臆測で埋めない**。設計文書に根拠がなければ、選択肢と推奨を添えて確認する。
- **ドキュメントの Mermaid は `make docs-check` で検証**してから出す。

---

## 12. コスト・安全・プライバシーのガードレール

- **予算**：全 Gemini 呼出は registry の単価・日次上限で計上。上限超過で停止。カセット・埋め込みキャッシュを再課金しない。
- **秘密**：`.env` のみ。pre-commit の秘密検出を無効化しない。
- **プライバシー**：人物の同定情報を作らない・保存しない（匿名の所在・姿勢のみ）。
- **安全**：信頼できない外部入力（通知・文書のメモ欄）を不可逆アクションの単独根拠にしない。不可逆は必ずゲート（SHACL＋規範＋可逆性＋承認）を通す。テストの「未承認不可逆 0 件」は必達。
- **ネット到達先は Gemini のみ**。他の外部通信を足さない（外部系は全ローカルモック）。

---

## 13. 完了の定義（Definition of Done）

作業を「完了」と呼ぶ前のチェックリスト。

- [ ] `make check` が緑（lint・型・test replay）。
- [ ] 追加・変更ロジックにテスト（決定性・oracle・契約のいずれか該当）がある。
- [ ] オントロジーを変えたなら `make gen` 済み・生成物コミット・SemVer 更新・二重表現あり。
- [ ] Gemini 呼出は `clients/` 内・モデルIDは registry・カセットをコミット。
- [ ] Claim は追記のみ／決定性を壊していない／人物非同定を守っている。
- [ ] 関連ドキュメント（PROJECT.md の trace matrix、シナリオカタログ §4、PREREG）を更新。
- [ ] PR 説明に対応する FR/NFR・実験E・シナリオを記載。

---

## 14. トラブルシュート早見

| 症状 | 主な原因 | 対処 |
|---|---|---|
| replay テストで `MissingCassette` | 新しい LLM 入力が未収録 | `make test-live`（record）で一度収録 → コミット → replay 固定 |
| 対話ビューアが起動しない（macOS） | `python` で `launch_passive` を起動 | `mjpython` で起動（`make demo` を使う） |
| 決定性テストが落ちる | 素の時刻／未シード乱数の混入 | 時刻はシムクロック、乱数はシード RNG に（§0-5） |
| SHACL 検証失敗 | 生成物が古い／データが規約違反 | `make gen` 後に再試行。裸の数値・参照を疑う |
| コストが急増 | replay でなく record/passthrough で回している | 既定 replay に戻す。registry の予算ガード確認 |
| 型検査が通らない | 生成型の未反映 | `make gen` → import 見直し |

---

*本書は開発運用 v0.1。ツール選定（uv/ruff/mypy/make 等）は本書の裁量事項なので、実態に合わせて更新してよい。ただし §0 の掟と §7 の不変条件はプロジェクト定義（PROJECT.md）に根ざすため、変更時は PROJECT.md と整合を取ること。*