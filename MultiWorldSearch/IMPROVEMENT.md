# IMPROVEMENT.md — 包括レビュー結果と修正計画（2026-06-11）

> **本書の位置づけ**
> コードベースとドキュメント全体を「プロジェクトの目的を満たしているか」の観点で網羅的に再レビューした結果（2026-06-11 実施）。
> 前版 IMPROVEMENT.md（2026-06-10 監査・修正計画）の C1–C6 / H1–H2 / M1–M3 / D1–D2 は**実装・検証済み**（mock 187 tests green・ruff clean・pyright 0 errors・`scenario-all` 7/7 PASS、live 実走は REPORT.md）。本書はその**完了を前提に、今回のレビューで新たに発見した未解決の修正必要箇所**を記録する。
> 検証方法: 全モジュール・全シナリオ・全ドキュメントの読解＋並行サブエージェント監査（doc整合・孤児コード・プロトコル迂回）＋疑義箇所の実行による裏取り（融合重みバグの再現、テスト数の実測、direnv 環境での失敗再現）。

---

## 0. 総合判定：プロジェクトは目的を満たしているか

PROJECT.md §2.1 の4目的と、ブログ（blog.ja.md）の中心主張に照らした判定。

| 目的 | 判定 | 根拠 |
|------|------|------|
| 1. 中核機構がシミュレーションで実証可能 | **◯（条件付き）** | 7/7 シナリオが live で完走、反証可能テスト付き。ただし第6インデックス（relational）が実際にはどこからも発火しない（R2）、H3 未計測（M7） |
| 2. 複数世界・複数インスタンスの記憶共有 | **◯（条件付き）** | S3 で FederatedStore 横断検索を実証。ただし QoR ルーティング・予測プリフェッチは未配線/未実装（R8） |
| 3. 業務価値の定量・定性提示 | **◯** | live 実測（$0.0059・985KB・検索品質表）を REPORT.md に記録。指標は反証可能にゲート済み |
| 4. クラウド埋め込みレイテンシへの設計解（教師/生徒, H7） | **✗ 未達** | 生徒（EmbeddingGemma）はスタブのまま。H7 は未検証（明示済みだが、4目的の1つが空白） |
| ブログの主張「検索＝共通プロトコル」 | **△** | 5/7 シナリオは判定を検索結果で駆動。**S2 の融合入力と S3 の consolidation 入力はローカル状態を直読みしており、自らの主張を迂回**（R3/R4） |

**結論**: PoC として目的の大半を「反証可能な形で」満たしている。ただし (a) 融合重みの実バグ（R1）、(b) 死んでいる第6インデックス（R2）、(c) 共通プロトコル主張に反する S2/S3 の迂回（R3/R4）、(d) 4つ目の目的 H7 の空白、の4点は目的適合性に直結するため修正を要する。

---

## ✅ 実施状況（2026-06-11 完了）

**全項目 R1–R12・M4–M7・H7 を実装・検証済み。** `uv run pytest` **209 passed**（mock・決定的、direnv live シェルでも素のまま緑 = R5）、ruff clean、pyright 0 errors、`make scenario-all` **live 7/7 PASS**（$0.0058、`runs/` に成果物）。REPORT.md は本日の live 実走（run IDs 記載）で全面更新済み。

| ID | 実装の要点 / 受け入れ確認 |
|----|---------------------------|
| R1 | engine 既定重みに `relational: 0.1` 追加。明示重み辞書で未登録インデックスは**除外**（`weights.get(name, 0.0)`）。`search_with_indices` は ALL_INDICES 全体を制御。キー集合テスト＋「未登録が支配しない」テスト追加 |
| R2 | S1 の ops クエリに `entity_id=pump_07` → relational が実発火（live `relational_hits=1`）。golden relational テスト＋アブレーションに `all_except_relational` 対照行（nDCG 0.927→0.931 の寄与を分離） |
| R3 | S2 融合入力を `recon_results` 経由に（`n_observations_retrieved` を記録）。観測を抑止すると融合失敗→ H9 不成立（反証テスト） |
| R4 | S3 クラスタリング入力を `defect_results`＋federated 結果に。`defect_top_k=1` で発見失敗（反証テスト） |
| R5 | `tests/conftest.py` autouse fixture で `MWS_*`/`GOOGLE_API_KEY` を scrub（live マーカー除く）。direnv live シェルで素の pytest が全緑 |
| R6 | S4・S1 を `RetrievalAugmentedPolicy` 経由に統一（`_replay_similarity` 重複を削除、policy は `get_atom` 使用・skill_fields 返却）。S1 の転移成功は「再生可能な軌道の想起」でゲート |
| R7 | S3 に consolidate→dedup→TTL→再索引の代謝を実装し H5 を計測: **圧縮 25.8%・lot_L recall 保持 1.00**（巨大 TTL で圧縮≤0 の反証テスト付き）。engine.ingest は既存埋め込みを再利用（再索引の二重コスト防止） |
| R8 | S3 の federated 検索を `QoRRouter` 経由に（strict→ローカル先行 1 件＋広域 1 件、`qor_router` stats を metrics 化）。router 単体テスト3件。federated 統合キーの `rsplit` 潜在バグも修正 |
| R9 | README 120→**209** に修正（blog の 195→187→209 も追随）。計測は `N passed` サマリ行で実施 |
| R10 | IMPLEMENTATION_PLAN.md に「歴史的文書」注記＋現状サマリ。SCENARIOS.md §2.2 に business/ ライブラリ注記。PROJECT.md の prefetch/A2A に実装状況注記 |
| R11 | `index_builder` を実装（設定どおり **LanceDB/DuckDB** に sim アトムを実索引、サマリ返却）。business/ はライブラリと明示 |
| R12 | structlog の出力先を **stderr** に変更し、`eval multi-seed` の stdout JSON が `json.load` 可能（パイプ検証済み） |
| M4 | エンジンに per-call レイテンシバケット（`local_ann`/`local_embed`/`gemini_embed` ＋ S1 live の `gemini_infer`）。live 実測: ANN 0.02ms / embed p50 391ms / infer p50 3.8s |
| M5 | per-call サンプリングにより p95 が実分布（embed n=27, infer n=8 等）。フェーズ単位は従来どおり1サンプル（注記済み） |
| M6 | AuditLogger.entry_count を累積カウンタ化（flush 後も保持）。最終ログが実数を表示（テスト付き） |
| M7 | `mws/eval/oracle.py`（特権オラクル）で **知覚税を計測**: S1 live で Recall@10 tax **0.10** / nDCG@10 tax 0.069（オラクル=パイプラインで tax 0 のテスト付き） |
| H7 | 実ローカル student（sentence-transformers, `student` extra・`is_stub=False`・空間 `minilm-384-v1`）＋ `eval h7` CLI。**事前登録基準で A/B 実施 → H7 支持**: speedup **27.4×**・quality ratio 1.06（EmbeddingGemma は HF gated のため MiniLM 代替、正直に記載）。stub フォールバックは警告付き |

**残課題（既知・文書化済み）**: federation/prefetch と A2A は未実装（PROJECT.md に注記）。EmbeddingGemma 本体での H7 再実行には HF トークンが必要。symbolic/structured の転置インデックス化（任意・スケール時）。

---

## 🆕 P7 — 2026-06-11 live 再検証（`make scenario-all`）で判明したデータ収集の残課題

> live 実走（run IDs は REPORT.md 冒頭）は 7/7 PASS・$0.0058。検証の過程で、metrics に**まだ取れていない/曖昧なデータ**が4点判明した。

### ✅ P7 実施状況（2026-06-11 完了）

**M8–M11・prefetch・転置インデックス（stretch b）を実装・検証済み。** mock スイート **220 passed**（両シェル）、ruff clean、pyright 0 errors、mock scenario-all 7/7。live 検証は REPORT.md（live multi-seed seeds 0–2 含む）。

| ID | 実装の要点 / 受け入れ確認 |
|----|---------------------------|
| M8 | `record_llm_call(real: bool=False)` 追加、`llm_calls_real`/`llm_calls_modeled` を分離出力（`llm_calls` は互換用の和）。S1/S5/S7 の live 経路のみ `real=True`。mock 全シナリオ real=0 を確認（単体テスト付き） |
| M9 | `mws/agents/scenario_agent.py`（汎用 step-agent factory: mock 既定・live は名前/instruction 付き ADK）。**S5 の8ステップと S7 の顧客通知文生成が live で実 ADK 化**（`gemini_infer` バケット＋real=True）。全7シナリオの metrics に `agent_mode: live|mock|modeled` を記録。ステップ完了は従来どおり検索エビデンスでゲート（mock 挙動は不変）。live マーカーテスト追加 |
| M10 | multi-seed 集計にフェーズレイテンシが分布として流れることを検証（例: act_p50 n=3 ±CI）。per-call バケットは従来から実分布 |
| M11 | `make scenario-multi-seed-live`（コスト見積もり表示＋`MWS_CONFIRM_LIVE_SPEND=1` 必須、無しでは拒否を確認）。live seeds 0–2 を実走し REPORT に live CI を記載 |
| prefetch | `mws/federation/prefetch.py`（タスクタグ→federated 検索→ローカル InstanceStore へ冪等コピー、既存埋め込み再利用でクラウド呼び出しゼロ）。S3 配線: **prefetch 有効でローカル即答カバレッジ=3観測者 / 無効で1**（反証テスト）。InstanceStore.ingest にも埋め込み再利用を追加。PROJECT.md 注記を「実装済み」へ更新 |
| stretch(b) | symbolic/structured を**転置インデックス化**（tag→ids / (field,value)→ids、挿入順タイブレークで旧線形走査と**完全同一の結果順**を保証するテスト付き）。2,000 アトム疎コーパスで線形走査比 3 倍超の高速化をテストで確認 |
| stretch(a) | EmbeddingGemma 再実行は**スキップ**: HF トークンは存在するが Gemma ライセンス未承諾で 401 gated（実行ログで確認）。H7 は引き続き MiniLM-384 代替の結果を正とする |

**残（既知）**: A2A 直接通信は未実装のまま（PROJECT.md 注記が正）。フェーズ単体ランの p95=p50 は per-call/multi-seed 側で代替（注記済み）。

---

## 🆕 P8 — 2026-06-11 本走（`make scenario-all` 再実行）のログ↔metrics 突き合わせ監査で判明した残ギャップ

> 検証手法: live 実走ログの「Gemini embedding call」行数（実呼び出し）と metrics の計上値を突き合わせ。**実165 vs 計上162** の差分3件を特定・原因究明した。

### ✅ P8 実施状況（2026-06-11 完了）

**M12–M14 を実装・live でゼロ差分検証済み。** mock スイート **228 passed**（両シェル）、ruff clean、pyright 0 errors。live `make scenario-all` 7/7 → `mws eval reconcile` で **actual==counted（embedding 163/163・実LLM 17/17・delta 0）**。

| ID | 実装の要点 / 受け入れ確認 |
|----|---------------------------|
| M12 | FederatedStore に cost/latency/bandwidth トラッカー＋content-hash キャッシュを注入可能化（S3 が注入）。ユニーククエリ毎に1計上・同一クエリはキャッシュヒット（テスト付き）。**live 突き合わせ監査ゼロ差分で受け入れ確認** |
| 監査常設化 | `mws/eval/reconcile.py`＋`mws eval reconcile` CLI（delta≠0 で非0終了）。live.py に「ADK call」マーカー1行/実呼を追加。合成ログ＋tmp runs のユニットテスト（未計上検出の反証テスト含む） |
| M13 | ADK Event の `usage_metadata` から実トークン抽出（`last_usage`）。S1/S5/S7 が実測トークンで `record_llm_call(measured=True)`。**live で 17/17 呼が実測** → **chars/4 推定は入力トークンを約2.6倍過大評価**と判明、コストを $0.0063→$0.00446 に正直に訂正（REPORT 記載）。`llm_calls_tokens_measured` で機械可読 |
| M14 | BandwidthMeter に real フラグ＋`total_real_bytes`/`total_virtual_bytes`（合計キー互換）。engine は teacher 時のみ real、LLM 帯域は real=is_live。mock で real=0（テスト付き） |

**残（既知・仕様）**: S7 の infer は1呼/ランで p95=p50。A2A 未実装・EmbeddingGemma gated（従来どおり注記が正）。

---

## 🆕 P10 — 2026-06-11 検証本走（P9 完了後の `make scenario-all`）で判明したログ/データの残ギャップ

> live 実走（run IDs は REPORT.md 冒頭）は 7/7 PASS・$0.00456・**突き合わせ監査ゼロ差分**（embedding 123/123・実LLM 18/18・全件実測トークン）。検証の過程で、**まだ取れていない/再現できないデータ**が4点判明した。

### M15 — manifest が再現に不十分（**Medium・CLAUDE §9 不適合**）

- **現状**: `runs/<RUN_ID>/manifest.json` は run_id/scenario/timestamp/git_sha/seed/cloud_mode/embedding_space/dims/python_version のみ。**(a) モデル ID**（gemini-embedding-2 / gemini-3.5-flash / student モデル名）、**(b) `MWS_EMBEDDING_BATCH_SIZE`**（リクエスト数を左右する設定）、**(c) git の dirty/untracked 状態**が無い。特に (c) は深刻で、**MWS ツリー全体が親リポジトリで未追跡（`?? ./`）のため、記録された `git_sha=48d2026c72e3` では実行時のコード状態を一切再現できない**。CLAUDE §9「使用設定・git SHA・依存バージョン・モデル ID・埋め込み空間・seed・クラウドモードを記録」に不適合。
- **修正案**: `create_manifest` に `model_ids`（embedding/llm/student）・`embedding_batch_size`・`git_dirty: bool`・`git_untracked_tree: bool`（`git status --porcelain` ベース）・主要依存バージョン（mujoco/lancedb/duckdb/google-genai）を追加。根本対処として **MWS をコミット**（または専用リポジトリ化）し SHA を意味あるものにする。
- **受け入れ基準**: manifest だけから「どのコード・どのモデル・どの設定で走ったか」が一意に特定できる。dirty 時はその旨が機械可読。

### M16 — 実 ADK 応答が未記録で live の計画変動を事後検証できない（**Medium・CLAUDE §5.1 不適合**）

- **現状**: 本走で S1 の実 LLM 呼が 8→**9** に変動（LLM が 8 ステップ計画を生成。前走は 7 ステップ）。ステップ完了はエビデンスゲートで正しく 8/8 となり計測は自己整合だが、**応答本文を保存していないため**計画長変動の原因確認・走間比較・応答の固定再生ができない。CLAUDE §5.1「再現性が必要な比較では応答をキャッシュ／記録し、seed と合わせて固定する」に不適合。
- **修正案**: ADKOpsAgent / scenario_agent の実呼び出しごとに `runs/<RUN_ID>/llm_calls.jsonl`（prompt 要約・応答本文・実測トークン・レイテンシ・agent名）を追記。任意で `MWS_LLM_REPLAY=<path>` による記録応答の決定的リプレイ（A/B 比較の固定用）。
- **受け入れ基準**: live 実走後に全実呼び出しの応答が runs/ 配下で読める。計画長の変動が応答から説明できる。

### M17 — per-atom 取込ループが E2 バッチ経路を迂回（**Low-Medium・レイテンシ**）

- **現状**: E2 の `engine.ingest_batch` を使うのは `BaseScenario._ingest_atoms` 経由のみ。S3 の inject/perceive、S5/S7 の perceive 等は **`_ingest_atom` を1件ずつループ**しており、live の perceive が S3 5.6s / S5 3.7s / S7 4.8s に滞留（バッチ化済みの S1 は 0.83s）。
- **修正案**: ループで蓄積→`_ingest_atoms(atoms)` 一括投入へ書き換え（federated への個別 ingest はそのまま。standing query 発火順序は ingest_batch が挿入順を保持するため不変）。
- **受け入れ基準**: live の S3/S5/S7 perceive がバッチ化分短縮し、embedding リクエスト数がさらに減る。mock スイート・ゴールデン不変。

### M18 — live multi-seed CI が v1 空間時代の値のまま（**Low・統計規律**）

- **現状**: REPORT が参照しうる live multi-seed CI（seeds 0–2: 知覚税 0.100±0 等）は P7 時点（接頭辞なし・v1 空間）の計測。P9 で埋め込みスキームが v2 に変わったため、**現行コードの live CI は未計測**。
- **修正案**: `MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live` を v2 で再実行（見積もり ~$0.005×3シード ≈ $0.014）し REPORT を更新。
- **受け入れ基準**: REPORT の live CI が v2 スキームの実測値になる。

---

## 🆕 P9 — gemini-embedding-2 を公式ドキュメント準拠で正しく使う（2026-06-11 計画→完了）

> **背景**: Embedding は `gemini-embedding-2` を使う（ユーザー指示・[公式ドキュメント](https://ai.google.dev/gemini-api/docs/embeddings?hl=ja)）。
> モデル ID 自体は現行実装（`mws/embedding/teacher.py:18`）で既に `gemini-embedding-2` を使用しており live で動作確認済み。しかし**公式ドキュメントを精読した結果、モデルの能力を引き出す3つの使い方が未実装**であることが判明した（CLAUDE.md §5 冒頭の「実装時に現行の公式ドキュメントを確認」原則に基づく差分監査。2026-06-11 にドキュメントを取得・確認済み）。

### ✅ P9 実施状況（2026-06-11 完了）

**E1–E4 を実装・live で全受け入れ確認済み。** mock スイート **255 passed**（両シェル）、ruff clean、pyright 0 errors、mock scenario-all 7/7。live `make scenario-all` 7/7（$0.00434）→ `mws eval reconcile` **ゼロ差分（embedding_requests 123/123・実LLM 17/17）**。live 受け入れテスト4件（`tests/embedding/test_teacher_live.py`）全緑。詳細は REPORT.md。

| ID | 実装の要点 / 受け入れ確認 |
|----|---------------------------|
| E1 | Embedder プロトコルに `format_document/format_query`＋`embed_document/embed_query`（既定=恒等委譲 → mock 完全不変）。teacher のみ公式接頭辞（文書 `title: none \| text:` / クエリ `task: search result \| query:`、実装時にドキュメント再取得で確定）。空間 **v2 バンプ**（`gemini2-768-v2`）・キャッシュは接頭辞込み最終文字列でキー。呼び分け: engine.ingest→document / engine.search・FederatedStore・H7→query。**事前登録 A/B → 支持: Recall@5 0.944→1.000（+0.056）**（run `e1_ab-0-dd5df34a`、`eval e1-ab` CLI 常設）。live でシナリオ検索品質・知覚税は不変 |
| E2 | `embed_batch`=複数 Content **1リクエスト**（マーカー1行/リクエスト・`n_texts` 付き）。`engine.ingest_batch`（重複排除・キャッシュ連携・挿入順/standing query 維持、ゴールデン順位一致テスト付き）を `_ingest_atoms` に配線。チャンクは `MWS_EMBEDDING_BATCH_SIZE`（既定64）。cost を `embedding_requests`/`embedding_texts` に分離（`embedding_calls` は互換 alias）、reconcile.py 追随（旧キー読込テスト付き）。**live 前後比較: リクエスト 163→123 (-25%)、S1 27→8、seed_memory -68%・perceive -87%**。バッチ⇔単発の live 等価テスト（cos>0.999）緑 |
| E3 | `mws index build --batch-api`（`client.batches.create_embeddings`・inlined requests）。投入→ポーリング→取込を**再開可能**（`--resume-job`・タイムアウトは非エラー）。**実ジョブ完走**（run `index_build_batch-0-d9c6e88a`: SUCCEEDED・LanceDB/DuckDB 実索引・取込時同期呼び出し0・manifest にジョブ名/規模/コスト記録）。Batch⇔同期のベクトル一致を live テストで確認。per-item token_count は live で None → 推定にフォールバックし `tokens_source` で明示（実測と偽らない） |
| E4 | `types.EmbedContentConfig(output_dimensionality=…)` へ移行（fake client で型検証）。live テストで truncated 768 の **L2 ノルム≈1.0**（公式の自動正規化）を確認 |
| 監査の戦果 | P9 初回 live 実走で**監査が実リーク9件を検出**（actual 132 vs counted 123）: ストア空間タグを settings から取っていたため v2 バンプ＋残留環境変数で**空間ドリフト**が発生し、S3 InstanceStore が未計上の再埋め込みを実行。修正: ストア空間/次元の単一情報源を **embedder に統一**（BaseScenario/S3/index_builder）＋ InstanceStore のフォールバック埋め込みを**計上付き経路**に配線（空間ドリフトでも計上される反証テスト追加）。再実走でゼロ差分回復 |

**残（既知）**: Batch API の per-item token_count が None（推定で代替・`tokens_source` ラベル明示）。`batches.create_embeddings` は SDK 上 experimental 注記。A2A・EmbeddingGemma は従来どおり。

### 公式ドキュメントとの差分サマリ

| 観点 | 公式ドキュメント（gemini-embedding-2） | 現行実装 | 判定 |
|------|----------------------------------------|----------|------|
| モデル ID | `gemini-embedding-2`（マルチモーダル・新規プロジェクト推奨） | 同一 | ✓ |
| タスク指示 | **`task_type` パラメータは使わない**。プロンプト内タスク指示（例: `task: search result \| query: {content}`）で文書/クエリを**非対称に**埋め込む | **未使用** — 文書もクエリも同一形式で埋め込み | ✗ **E1** |
| output_dimensionality | 128–3072、推奨 768/1536/3072。**truncated 次元は自動正規化** | 768 指定 ✓。正規化はストア層で実施（自動正規化と二重だが無害） | ✓（テストで明示化 E4） |
| バッチ埋め込み | 複数 `Content` を1呼で個別埋め込み可。大規模は **Batch API（50%割引）** | `embed_batch` は **per-text ループ**（1テキスト=1呼）。Batch API 未使用 — **CLAUDE.md §5.3「索引化は Batch API で非同期・低コストに」に自ら違反** | ✗ **E2/E3** |
| SDK 呼び出し型 | `types.EmbedContentConfig(...)` | dict リテラル（動作はする） | △ **E4** |

### E1 — 文書/クエリの非対称タスク指示埋め込み（**High・検索品質**）

- **現状**: `engine.ingest`（文書側）と `engine.search`／`FederatedStore._embed_query`（クエリ側）が**同一の `embed_text`** を呼び、タスク指示なしで埋め込んでいる。gemini-embedding-2 は task_type の代わりに**プロンプト接頭辞**で検索非対称性（文書: `task: search result | query: {content}` 系）を表現する設計であり、これを使わないと検索向け埋め込みの品質を取りこぼす。
- **修正案**:
  1. Embedder プロトコル（`mws/embedding/base.py`）に `embed_document(text)` / `embed_query(text)` を追加。**既定実装は `embed_text` へ委譲**（Mock/Student は完全に現行挙動のまま → mock スイート・ゴールデンは不変）。
  2. `GeminiTeacherEmbedder` のみオーバーライドし、公式の接頭辞形式（実装時にドキュメントの正確な文字列を再確認）で文書/クエリを区別して埋め込む。
  3. 呼び分け: `engine.ingest` → `embed_document`、`engine.search`・`FederatedStore._embed_query`・H7 ハーネス → `embed_query`。
  4. **キャッシュ整合**: content-hash は**接頭辞込みの最終文字列**で計算（同一テキストでも文書/クエリでエントリが分かれる — 正しい挙動）。
  5. **embedding_space 規律（CLAUDE.md §6）**: 接頭辞スキーム導入は同一モデルでも座標の意味が変わるため、空間タグを `gemini2-768-v1` → **`gemini2-768-v2`** にバンプし、旧空間ベクトルとの混在・比較を型レベルで防ぐ（「モデル更新＝再索引」の適用）。
- **受け入れ基準**: (a) mock スイートは無変更で全緑（既定委譲のため）。(b) live で S1（または golden 相当コーパス）の **タスク指示あり/なし A/B** を実施し、Recall@5/@10 を事前登録の形で比較・REPORT に記録（改善でも非改善でも正直に報告 — 反証可能）。(c) 突き合わせ監査はゼロ差分のまま。

### E2 — 複数 Content の単発バッチ埋め込み（**High・コスト/レイテンシ**）

- **現状**: `GeminiTeacherEmbedder.embed_batch` が per-text ループ（`teacher.py:93-98`）。live の `scenario-all` は **163 呼/走**（p50 ~370ms × 直列）— 取込フェーズの大半がこの直列往復。
- **修正案**: `embed_batch` を公式の複数 `Content` 形式（1リクエストで個別埋め込みの配列を返す）で実装し、`engine` の取込経路に**バッチ取込 API**（例: `engine.ingest_batch(atoms)` がテキストをまとめて embed_batch → 各アトムへ分配）を追加。シナリオの `_ingest_atoms` から利用。チャンクサイズは設定（`configs/`）へ。
- **計測整合（必須）**: コスト/レイテンシ/帯域の計上と**突き合わせ監査の定義を更新** — 「1 API リクエスト=1 実呼び出し」なので、`embedding_calls` は **リクエスト数**と**埋め込まれたテキスト数**を分離（`embedding_requests` / `embedding_texts`）。teacher のログマーカーもリクエスト単位で1行とし、`reconcile.py` を新キーに追随させる（ゼロ差分維持）。
- **受け入れ基準**: live `scenario-all` の embedding **リクエスト数が大幅減**（例: S1 27→数回）し、取込レイテンシ（perceive/seed_memory フェーズ）が短縮されることを REPORT で前後比較。reconcile ゼロ差分。mock 挙動・検索結果は不変（ゴールデン回帰なし）。

### E3 — Batch API による非同期オフライン索引化（**Medium・CLAUDE §5.3 準拠**）

- **現状**: CLAUDE.md §5.3 は「索引化は Batch API で非同期・低コスト（50%割引）」と定めるが、`index_builder` 含め全経路が同期 `embed_content`。
- **修正案**: `index_builder`（オフライン教師索引の正規経路）に **genai の Batch API**（ジョブ投入→ポーリング→結果取り込み。実装時に公式ドキュメントで現行のジョブ API 形を確認）を実装。中断・再開可能に（CLAUDE §3）。ジョブ ID・規模・推定コストを manifest に記録（§5.5）。対話パス（シナリオ実行中の取込）は E2 の同期バッチのままで良い（Batch API は非同期前提のため）。
- **受け入れ基準**: `mws index build --batch-api` で実ジョブが完走し、同一コーパスの同期経路と**ベクトルが一致**（または数値誤差内）すること、コストが manifest に記録されること。live マーカーテスト1件。

### E4 — SDK 型付き config・正規化の明示化（**Low・整備**）

- **現状**: config が dict リテラル。truncated 次元の正規化は「gemini-embedding-2 は自動」（公式）＋ストア層の再正規化で実質問題ないが、仕様としてテスト化されていない。
- **修正案**: `types.EmbedContentConfig(output_dimensionality=...)` へ移行。live マーカーテストで「返却ベクトルの L2 ノルム ≈ 1.0（自動正規化）」を1件検証し、ドキュメント仕様への依存を明示。
- **受け入れ基準**: pyright 0 errors 維持・live テストでノルム検証。

### 実行順・ガードレール

1. **E1**（品質の根幹・A/B はコスト ~$0.005 以下）→ 2. **E2**（コスト/レイテンシ・reconcile 更新とセット）→ 3. **E4**（小粒）→ 4. **E3**（非同期ジョブ・最後）。
- 既存資産を壊さない: mock 既定・決定性・ゴールデン Recall・反証テスト群・**突き合わせ監査ゼロ差分**（E2 でキー定義を更新しつつ維持）・embedding_space 分離（E1 で v2 バンプ）。
- 外部 API の正確なパラメータ名/接頭辞文字列/Batch ジョブ形式は**実装時に公式ドキュメントを再確認**（CLAUDE.md §5 冒頭原則）。live 検証は従来どおり ~$0.05 以内・`MWS_CONFIRM_LIVE_SPEND` 系の規律に従う。
- REPORT.md には E1 A/B と E2 前後比較を**事前登録の形**で記録し、blog の該当数値（埋め込み呼数・レイテンシ）も追随させる。

---

### M12 — FederatedStore.search のクエリ埋め込みがコスト計上を迂回（**Medium・計測リーク**）

- **現状**: `FederatedStore.search` はクエリ埋め込みを `self._embedder.embed_text()` 直呼びで行い、**CloudCostTracker / レイテンシバケットを通らない**（live では実 Gemini 呼が未計上になる）。さらに従来は**インスタンスごとに同一クエリを再埋め込み**しており（3インスタンス=3呼/検索）、後者の冗長は**本日修正済み**（埋め込みをループ外へ巻き上げ。1検索=1呼に削減、結果順は不変・全テスト緑）。
- **残修正案**: FederatedStore（および QoRRouter 経由呼び出し）にコスト/レイテンシトラッカーを注入可能にし、残る 1 呼/検索を `gemini_embed`/`local_embed` バケットと embedding_calls に計上する。content-hash キャッシュの共有も検討（engine キャッシュと別系統のため、同一クエリでも再埋め込みされる）。
- **受け入れ基準**: live 実走でログの実呼び出し数と metrics 計上値が一致する（突き合わせ監査がゼロ差分）。

### M13 — 実 ADK 呼び出しの実トークン数を未取得（**Medium・コスト精度**）

- **現状**: `estimated_cost_usd` は実 ADK 呼び出し（S1/S5/S7 の `real=True`）にも `len(str)/4` のトークン推定を使う。Gemini API レスポンスの実 usage（`usage_metadata.prompt_token_count` 等）は取得しておらず、推定コストと実コストの乖離が未検証。
- **修正案**: `ADKOpsAgent._run_agent` で ADK イベント（または genai レスポンス）から usage_metadata を抽出して返し、呼び出し側が実トークンで `record_llm_call(real=True)` する。取得不能時は推定にフォールバックし `tokens_estimated: true` をマーク。
- **受け入れ基準**: live S1 の metrics に実トークン由来の `llm_input_tokens` が入り、推定/実測の別が機械可読。

### M14 — 帯域計測が実/仮想を区別しない（**Low・M8 と同種**）

- **現状**: `total_bandwidth_bytes` は「仮想エッジ↔クラウド帯域」の定義だが、live の実呼び出し分も同じ推定式で混在計上される。M8 の LLM 分離と同様の曖昧さ（影響は表示のみ・小）。
- **修正案**: `BandwidthMeter` に real/virtual の分離キーを追加（M13 とまとめて着手推奨）。

### （注記・仕様）
- S7 の `gemini_infer` は 1 呼/ランのため p95=p50（単一サンプル）。multi-seed 集計で分布化される。

### M8 — `llm_calls` が実呼び出しとコストモデル計上を区別しない（**High・計測の正確性**）

- **現状**: `metrics.json` の `system.llm_calls=25` のうち、**実 ADK（gemini-3.5-flash）呼び出しは S1 の 8 件のみ**。S2–S7 の 17 件はモックエージェントのステップに対する `cost.record_llm_call()`（仮想コスト見積もり）であり、実クラウド呼び出しではない。実/仮想は現状 `gemini_infer_count` レイテンシバケットの有無から**手動で**しか判別できない（REPORT 2026-06-11 はこの手動分離で報告）。従来レポートの「LLM 25/26 calls (ADK)」は過大表示だった。
- **修正案**: `CloudCostTracker.record_llm_call(..., real: bool=False)` を追加し、`llm_calls_real` / `llm_calls_modeled` を summary に分離出力。S1 の live 経路のみ `real=True`。REPORT 生成側も分離値を参照。
- **受け入れ基準**: `metrics.json` に `llm_calls_real` と `llm_calls_modeled` が別キーで出る。live S1 で real=8、mock 全シナリオで real=0。

### M9 — live エージェント経路が S1 のみ（**Medium・検証範囲の明確化**）

- **現状**: `mws/agents/live.py`（ADKOpsAgent）を使うのは S1 だけ。S5 の IncidentAgent、S7 の OrderAgent 等は live モードでも `_mock_*_step` 辞書で実行される。つまり**「LLM 計画の live 検証」は S1 に限定**され、他シナリオの「LLM」はコストモデル上の存在。
- **修正案（いずれか）**: (a) S5/S7 にも ADK live 経路を実装（ops_agent の factory パターンを流用）。 (b) 実装を増やさないなら、REPORT・blog に「live LLM 計画は S1 のみ」を恒常的に明記（2026-06-11 REPORT は明記済み）し、metrics に `agent_mode: live|mock` を記録。
- **受け入れ基準**: (a) なら S5/S7 の live 実行で `gemini_infer` バケットが出現。(b) なら metrics から各シナリオのエージェント実態が機械可読。

### M10 — フェーズ単位レイテンシは依然1サンプル（**Low・既知の残余**）

- **現状**: per-call バケット（local_ann/local_embed/gemini_embed/gemini_infer）は実分布の p50/p95 を持つが、フェーズ単位（`setup/inject/perceive/act/index`）は1ラン1サンプルのため p95=p50 のまま。
- **修正案**: multi-seed 集計（M3 実装済み）にフェーズレイテンシを含めて分布化、または REPORT でフェーズ値は p50 のみ引用（現行 REPORT は per-call 側のみ引用しており実害なし）。

### M11 — live のマルチシード未実施（**Low・統計規律**）

- **現状**: 95%CI は mock のみ（`make scenario-multi-seed-all`）。live は seed 0 の単発で、live 数値（Recall 等）には CI が無い。
- **修正案**: live multi-seed の事前コスト見積もり（おおよそ $0.006×シード数/全シナリオ）を添えて任意実行。manifest にシード列を記録。

### （再掲・既知）
- EmbeddingGemma は HF gated — H7 named-model 再実行には HF トークン（`MWS_STUDENT_MODEL` で差し替え可能、現検証は MiniLM-384 代替で実施・開示済み）。
- prefetch / A2A 未実装（PROJECT.md 注記済み）。

---

## 1. 指摘一覧（優先度順）

| ID | 指摘 | 分類 | 深刻度 |
|----|------|------|--------|
| R1 | RRF 融合：重み辞書に無いインデックスが**デフォルト重み 1.0** で支配する（relational が辞書に欠落） | バグ | **Critical** |
| R2 | relational（第6）インデックスが**どのシナリオ/テストのクエリからも発火しない**（実効5種なのに「6種融合」を主張） | 機能死蔵・doc乖離 | **High** |
| R3 | S2：多観測者融合の入力が `self._observation_atoms`（ローカル）直読みで、直前の検索結果 `recon_results` を消費しない | 目的との不整合 | **High** |
| R4 | S3：ConsolidationEngine の入力が `self._atoms`（ローカル）直読みで、`defect_results` を消費しない | 目的との不整合 | **High** |
| R5 | direnv（`.env`→live）環境で `uv run pytest` が**1件失敗**：テストが環境変数に汚染される | テスト分離 | Medium |
| R6 | VLA モジュール一式（`policy.py`/`mock.py`/`skill_atoms.py`）が**全シナリオから未使用**（レガシー削除で孤児化） | 主要コンポーネント未配線 | Medium |
| R7 | consolidation の `ttl.py`/`dedup.py` 未使用 → H5 の「圧縮率↔recall 保持」（PROJECT §10.1）が未計測 | 主要コンポーネント未配線 | Medium |
| R8 | `federation/router.py`（QoRRouter）未使用・テスト0件。予測プリフェッチは実装自体なし | 主要コンポーネント未配線 | Medium |
| R9 | テスト数の誤記：README「120 tests」→ 実測 **187**（blog の「195」は本レビューで 187 に修正済み） | doc 正確性 | Low |
| R10 | IMPLEMENTATION_PLAN.md のフェーズ進捗が実装と乖離。SCENARIOS.md §2.2「業務データは `mws/business/` スキーマ経由」も実態（各 data.py 直書き）と乖離 | doc 正確性 | Low |
| R11 | `mws/business/`（generator/schemas/stubs）がレガシー削除で孤児化。`index_builder.py` は CLI から届くが実処理なしのスタブ | 死蔵コード | Low |
| R12 | `mws eval multi-seed` がログと JSON を同一 stdout に混在出力し、機械可読でない | UX/計測 | Low |

**継続オープン（前版から引き継ぎ）**: M4 レイテンシ分解（local-ANN/embed/infer の p50/p95、CLAUDE.md §10 不適合）/ M5 p95==p50（フェーズ毎1サンプル）/ M6 最終ログ `Audit entries: 0`（flush 後読みの cosmetic バグ）/ M7 H3 知覚税オラクル未実装 / H7 生徒スタブ / （任意）symbolic・structured の転置インデックス化。

---

## 2. 指摘の詳細

### R1 — RRF：未登録インデックスのデフォルト重み 1.0（Critical・バグ）

- **現状**: `mws/retrieval/fusion.py:33` が `w = weights.get(index_name, 1.0)`。一方 `mws/retrieval/engine.py` の既定 `fusion_weights` は `semantic 0.4 / spatial 0.2 / temporal 0.2 / symbolic 0.1 / structured 0.1` のみで **`relational` キーが無い**。
- **実証**（再現済み）: relational がヒットを返すと、semantic の最良ヒット（重み0.4）より relational ヒット（暗黙1.0 = 2.5倍）が上位に来る。
- **波及**: `search_with_indices()` は `original_weights` のコピーを zero 化する実装のため、辞書に無い relational は**アブレーションでも無効化できない**（「semantic_only」設定でも relational が発火すれば混入する）。現状は R2 により relational が発火しないため実害が顕在化していないが、R2 を直した瞬間に爆発する地雷。
- **修正案**: (1) engine 既定 `fusion_weights` に `"relational"` を明示追加（例 0.1）。(2) `fusion.py` の明示重み使用時のフォールバックを `weights.get(index_name, 0.0)` に変更（`weights=None` の等重み挙動は現行維持）。(3) `search_with_indices` が6種すべてを制御できることをテストで保証。
- **受け入れ基準**: 「fusion_weights のキー集合 == 発火しうるインデックス集合」を検証するテスト追加。relational 有効/無効でアブレーション結果が変化する。

### R2 — relational インデックスがどこからも発火しない（High）

- **現状**: relational 検索の発火条件は `query.structured_filters["entity_id"]` があること（`engine.py:124`）だが、**`mws/` にも `tests/` にも `structured_filters` に `entity_id` を渡すクエリが1つも存在しない**（grep 全件確認）。scene graph は全シナリオで構築されているのに、検索経路としては死蔵。
- **問題**: REPORT.md・blog.ja.md は「6種インデックス融合」を掲げるが、実効は5種。S1 アブレーションの「all 6 indices」行も relational の寄与はゼロ。
- **修正案（いずれか）**: (a) S1 に entity_id クエリを追加（pump_07 の scene-graph 近接から valve_03 関連アトムを引く——シナリオの文脈に自然に合う）し、golden test にも relational ケースを追加。R1 修正とセットで実施。 (b) 実装を増やさないなら、REPORT/blog の表現を「5種＋relational（実験未使用）」へ訂正。
- **受け入れ基準**: (a) なら relational が実クエリで発火し、アブレーションで寄与が計測される。(b) なら docs から「6種が実証された」と読める記述が消える。

### R3 — S2：融合入力が検索を迂回（High・「共通プロトコル」主張との不整合）

- **現状**: `s2_physical_record_reconciliation/scenario.py:436` 付近で、融合の counts/variances を `self._observation_atoms`（シナリオのローカルリスト）から読む。直前に発行している `recon_query` → `recon_results` は監査ログとコスト計上にしか使われない。
- **問題**: 本プロジェクトの中心主張は「消費者は検索レイヤー経由でデータを取得する」。判定ロジック自体は実データ駆動（前版 C1 で修正済み）だが、**データの取得経路がプロトコルを通っていない**。S4/S6/S7 は `engine.get_atom(r.atom_id)` で検索結果から読む形に統一済みなので、S2 だけ非対称。
- **修正案**: `recon_results` から `engine.get_atom` で `observed_count`/`noise_variance` を抽出して融合（S7 の reconcile と同型）。コード内コメント「(pipeline output)」も実態に合わせる。
- **受け入れ基準**: 反証テスト「観測アトムが検索で引けない場合（例: top_k を絞る/タグ不一致）、融合が成立せず fusion_beats_single が評価不能 or False になる」。

### R4 — S3：consolidation 入力が検索を迂回（High・同上）

- **現状**: `s3_collective_weak_signal/scenario.py:350` で `defect_atoms = [a for a in self._atoms if ...]` とローカル全アトムをフィルタしてクラスタリング。`defect_query` → `defect_results`（20件取得）はメトリクス計算にしか使われない。federated 検索結果も `federation_found_all` フラグ止まりで、発見ロジックには入らない。
- **修正案**: クラスタリング入力を `defect_results`（＋federated 検索結果）由来のアトムに変更。
- **受け入れ基準**: 反証テスト「検索で defect アトムが surface しない場合、lot_L 発見が失敗する」。

### R5 — テストの環境変数汚染（Medium）

- **現状**: 開発環境では `.envrc`（direnv `dotenv`）が `.env` の `MWS_CLOUD_MODE=live`・`MWS_EMBEDDING_DIMS=768` 等をシェルへエクスポートするため、**素の `uv run pytest` が 1 failed / 186 passed になる**（`tests/core/test_config.py::test_default_settings`）。pydantic-settings が環境変数を直接読むため。
- **問題**: CLAUDE.md §8「既定はクラウド非接触・決定的」が、この開発機の既定シェルで成立していない。CI では通るが、開発者体験と規約の乖離。
- **修正案**: `tests/conftest.py` に autouse fixture を追加し、`MWS_*` と `GOOGLE_API_KEY` を monkeypatch で削除（`@pytest.mark.live` のテストのみ除外）。
- **受け入れ基準**: direnv の live 環境のまま `uv run pytest` が 187 passed になる。

### R6 — VLA モジュールが全シナリオから未使用（Medium）

- **現状**: `mws/vla/policy.py`（RetrievalAugmentedPolicy。trajectory 抽出・類似度計算を実装済み）、`mock.py`、`skill_atoms.py` の利用箇所は**自モジュールのテストのみ**。旧 `scenarios/maintenance_handoff.py`（前版 D2 で削除）が唯一の消費者だったため孤児化した。
- **問題**: PROJECT.md §7 は「検索拡張VLA」を柱に掲げ、blog も S4 を「VLA がスキルを再生」と説明するが、S4/S1 の実行ゲートはシナリオ内に独自実装されており vla/ を通らない（`_replay_similarity` は policy.py のロジックの重複実装）。
- **修正案（いずれか）**: (a) S4（と S1 のマニピュレータ実行）を `RetrievalAugmentedPolicy` 経由にリファクタし、重複実装を解消。 (b) vla/ を「ライブラリ（シナリオ未使用）」と README/PROJECT に明示。
- **受け入れ基準**: (a) なら S4 の成功判定が policy.act() の出力（trajectory_similarity / used_retrieval）を消費。(b) なら docs に未配線であることが明記される。

### R7 — H5 の「圧縮率↔recall 保持」が未計測（Medium）

- **現状**: `consolidation/ttl.py`（TTL剪定）と `dedup.py`（content-hash 重複排除）は実装・単体テスト済みだが、**どのシナリオからも呼ばれない**。S3 が使うのは `cluster_by_tags` のみ。PROJECT §10.1 の consolidation 指標（圧縮率・recall 保持・ストアサイズ対レイテンシ）はどれも計測されていない。
- **修正案**: S3 に consolidate（要約アトム生成）→ dedup → TTL 剪定を組み込み、剪定前後のストアサイズと Recall@k を比較する圧縮↔保持カーブを1点でも計測する。重い場合は、REPORT の H5 行を「パターン発見のみ検証（圧縮・剪定は未計測）」へ限定する。
- **受け入れ基準**: REPORT の H5 の主張範囲とコードの計測範囲が一致する。

### R8 — QoR ルーティング未配線・予測プリフェッチ不在（Medium）

- **現状**: `federation/router.py`（QoRRouter）は実装があるが**利用箇所ゼロ・テストもゼロ**。`federation/prefetch.py` は存在しない。PROJECT §3 は「QoR でルーティング」「タスク計画に基づく予測プリフェッチ」を機構として掲げる。
- **修正案**: S3 の federated 検索を QoRRouter 経由（例: `freshness=strict` → ローカル優先）にし router のテストを追加。プリフェッチは未実装である旨を PROJECT/README に明記（実装するなら別途）。
- **受け入れ基準**: router がシナリオ経路で実行されテストされる。または docs から「実装済み」と誤読される記述が消える。

### R9 — テスト数の誤記（Low・doc）

- **現状**: 実測は **187 tests**（`pytest --collect-only` = 187、全パス）。README.md:194 は「120 tests」。blog.ja.md の「195」(2箇所) は本レビューで 187 に修正済み（前版作業時の集計ミス。`addopts` の `-q` と手動 `-q` の二重指定でサマリ行が抑制され、ドット数を誤読したのが原因）。
- **修正案**: README.md:194 を「187 tests」に更新。今後はサマリ行（`N passed`）で確認する。

### R10 — 計画系ドキュメントの乖離（Low・doc）

- **現状**: IMPLEMENTATION_PLAN.md は consolidation/federation/reactive を未了フェーズとして記載するが、実装済み・S3/S5 で使用中。SCENARIOS.md §2.2 は合成業務データを `mws/business/` スキーマ経由とするが、実シナリオは各 `s*/data.py` に直書き。
- **修正案**: IMPLEMENTATION_PLAN.md に完了マーク（または「歴史的文書」と注記）。SCENARIOS.md §2.2 を実態（per-scenario data.py）に合わせるか、R11 の business 再配線とセットで解消。

### R11 — business/ の孤児化と index_builder スタブ（Low）

- **現状**: `mws/business/generator.py`・`schemas.py`・`stubs.py` の消費者はテストのみ（旧レガシー S1 が唯一の利用者だった）。`mws/scenarios/index_builder.py` は CLI `mws index build` から届くがログを出すだけの実処理なしスタブ。
- **修正案（いずれか）**: (a) SCENARIOS.md §2.2 の設計どおり、各シナリオ data.py を business スキーマ準拠に寄せて再配線。 (b) 当面使わないなら docs に「未配線」と明示（削除は将来の業務データ拡張に備え保留可）。index_builder は実装（オフライン教師索引のバッチ再構築）するか、`NotImplemented` を明示してヘルプに注記。

### R12 — multi-seed CLI の出力が機械可読でない（Low）

- **現状**: `mws eval multi-seed` は structlog のログ（stdout）と JSON（stdout）が混在し、`json.load` 不可。前回の REPORT 作成時もテキストから JSON 部分を切り出すワークアラウンドが必要だった。
- **修正案**: ログを stderr へ（`setup_logging` の出力先指定）、または `--output <path>` で JSON をファイルに書く。
- **受け入れ基準**: `mws eval multi-seed ... | python -c "import json,sys; json.load(sys.stdin)"` が通る。

---

## 3. 継続オープン項目（前版 P5 から引き継ぎ・依然未対応）

| ID | 課題 | 補足 |
|----|------|------|
| M4 | レイテンシ分解が CLAUDE.md §10 不適合（local-ANN / Gemini-embed / Gemini-infer の p50/p95 が無い。`act` 一括で S1=34.7s が不透明） | PROJECT §10.3 Phase 0/1 ゲートの前提。R12 と同時に着手推奨 |
| M5 | p95==p50（フェーズ毎1サンプル）でパーセンタイル無意味 | ホットパス複数回計測 or multi-seed 集計 |
| M6 | 最終ログが常に `Audit entries: 0`（`flush()` 後に `entry_count` を読む）。metrics.json は正しい | 各 evaluate() で flush 前に件数を退避（1行修正×7箇所） |
| M7 | H3 知覚税が未計測（オラクル検索器なし） | S1 で上限検索器 vs 実パイプラインの Recall 差分を1点計測 |
| H7 | 生徒（EmbeddingGemma）がスタブ → 目的4が空白 | sentence-transformers 等で実装し latency/recall A/B（依存追加は extra に隔離） |

---

## 4. 推奨着手順

1. **R1 + R2(a)**（融合重み修正と relational の実利用はセット。R1 単独でも先行可） — 検索の正しさの根幹
2. **R3 → R4**（S2/S3 のプロトコル迂回解消） — ブログ/REPORT の主張とコードの一致
3. **R5**（conftest 環境 scrub） — 開発体験と規約の一致、以後の全作業の足場
4. **M6 / R9 / R12**（小粒の正確性・UX修正をまとめて）
5. **R6 / R7 / R8**（未配線コンポーネントの統合 or 明示） — 目的1・2の完全化
6. **M4 / M5 / M7 / H7**（計測の深化と目的4） — 研究としての次フェーズ

各項目の完了条件は前版と同じ規律に従う：判定はパイプライン出力でゲートし反証可能テストを付ける／seed 管理と mock 既定を維持／`ruff`・pyright・`pytest`（mock・決定的）緑／検索変更はゴールデンテストで Recall 回帰なし／REPORT・blog の数値と主張をコードの実態に一致させる。

---

## 付録：レビューで「健全」と再確認された資産（壊さないこと）

- 7シナリオの反証可能ゲート（C1–C6 の成果）：S2 統計的融合（等重み対照つき）/ S6 ルールエンジンリスク / S7 鮮度ゲート / S4 実行ゲート / S1・S5 エビデンスゲート / S3 厳密優越判定 — いずれも「壊し方」テスト付きで動作
- MuJoCo 物理（`mj_step`・seed 摂動オプション）、standing query の ingest 内 pub/sub 自動発火、CuriosityEngine のギャップ検知
- RRF 融合の数式・クエリプランニング・5消費者射影・content-hash キャッシュ・embedding_space 分離
- 実 LanceDB / DuckDB バックエンド（registry 切替・E2E テスト付き）と Graph/Spatial/Blob ストア
- クラウド境界（genai/adk の import は embedding/ と agents/ のみ — grep で再確認済み）
- `runs/` 追記のみ・manifest 記録（live 実走の成果物で確認済み）
