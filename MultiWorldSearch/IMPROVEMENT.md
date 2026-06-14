# IMPROVEMENT.md — 未完課題（2026-06-14 P13 完了後）

> **本書の位置づけ**
> 未完の修正・改善課題**のみ**を保持する。完了済み課題の詳細は **git 履歴の本ファイル**と **REPORT.md** を参照。
> 現状: live 7/7 PASS・**3点照合監査ゼロ差分**・埋め込みは `gemini-embedding-2` 単一（P12）・ベクトルバックエンドは memory/LanceDB/**Elasticsearch** から選択可（P13）・mock **275 tests** 両シェル緑（＋ES統合5件、`make es-up` 時）・manifest 完全再現可能。

---

## 1. 未完課題

### M20 — manifest が「何が dirty か」を記録しない（**Low・再現性の最後の一歩**）

- **現状**: manifest は `git_dirty: bool` を記録するが、dirty な**パス一覧は記録しない**。2026-06-12 の検証本走で `git_dirty: true` が記録された際、原因（REPORT.md が作業ツリーから削除されていた — コードは SHA と一致、計測影響なし）の特定に manifest の外（`git status`）が必要だった。
- **修正案**: `get_git_state()` が `git status --porcelain -- .` の結果から dirty パスの先頭 N 件（例 20）を `git_dirty_paths` として manifest に含める（既にコマンドは実行している — 出力を捨てているだけ）。
- **受け入れ基準**: dirty な状態で生成した manifest 単体から「何が dirty だったか」が読める。clean なら空リスト。テスト付き。

---

## 2. ブロック中（外部要因）

**なし。** 唯一の外部ブロックだった「H7 を EmbeddingGemma で再実行（HF ライセンス未承諾）」は、**P12 で H7 を撤回し埋め込みを `gemini-embedding-2` 単一に統一**したことで消滅した。

---

## 3. 既知の制約（仕様・実験的結論として注記済み・対応不要と判断）

- **R@5 は構造上限の 93%** — 事前登録の重みスイープ（`tune_fusion-0-df849f70`）で全12候補がフラット、上限 5/n_relevant に S1/S3/S7 が張り付きと判明。残余は S5 の1アトムのみで、改善はコーパス/ラベル設計の領域（重みでは動かない — 実験的結論）。
- **A2A 直接通信は未実装** — 協調は共有ストア経由のスティグマジーで実証（PROJECT.md §5.1 注記が正）。
- **Batch API の per-item token_count が None** — `tokens_source: estimated` で機械可読に明示。
- **S7 の gemini_infer は 1 呼/ラン**（p95=p50、multi-seed で分布化）・**フェーズ単位は1サンプル**・**S2 perceive のロボ観測3件は単発埋め込み**（by-design）。
- **リプレイ走は reconcile の入力にしない** — 再現実験であり計測走ではない（`mws/core/llm_log.py` に文書化）。
- **埋め込みは `gemini-embedding-2` 単一**（P12）— 教師/生徒二層・H7・sentence-transformers は撤去。クエリ時のクラウド往復（p50〜400ms）は既知の制約で、キャッシュ/バッチ/Batch API で抑える方針。高頻度制御ループ適合はスコープ外。

---

## 完了済みの記録（参照用サマリ）

| 波 | 内容 | 検証 |
|----|------|------|
| P1–P4 (06-10) | シナリオの反証可能化 — 手書き結論を全廃しパイプライン出力でゲート | 187 tests |
| レビュー修正 (06-11) | R1–R12・M4–M7・H7 — 融合バグ/relational/プロトコル迂回/VLA・QoR・consolidation/知覚税/実 student A/B | 209 tests |
| P7 (06-11) | M8–M11＋prefetch＋転置インデックス | 220 tests |
| P8 (06-11) | M12–M14＋監査常設化（ゼロ差分・実測トークン・帯域分離） | 228 tests |
| P9 (06-11) | E1–E4 gemini-embedding-2 公式準拠（非対称接頭辞 A/B +0.056・バッチ・Batch API・v2）＋監査が実リーク9件検出→修正 | 255 tests |
| P10 (06-12) | M15–M18 — manifest 完全化＋コミット・llm_calls.jsonl 記録・取込全面バッチ（-48%）・v2 live CI | 263 tests |
| P11 (06-12) | M19 **3点照合監査**・**REPLAY**（記録応答の決定的リプレイ、S1 全ゲート再現・LLM呼0）・**重みスイープ**（事前登録→現行確定＋R@5 構造上限の発見）・**並列 act**（S1 -65%/S5 -72%、レース排除・順序決定性維持） | 276 tests |
| P14 (06-14) | **シナリオ実行を ES バックエンド既定化**（B方針）— `MWSSettings.vector_backend` 既定 `elasticsearch`、pytest は conftest で `memory` 強制（モックテスト境界）。ES ストアはインスタンスごと一意インデックス＋`close()/drop()` で後始末（engine/federated/consolidation の混在防止）。`make scenario-all`/`scenario-multi-seed-all` は `setup`(--extra es)＋`es-reset`（クリーン起動＝初期化）を事前依存に。実 ES で `make scenario-all` 完走・インデックス0残（teardown 検証）。既定テストは memory のまま 275 緑 | 275 tests |
| P13 (06-14) | **Elasticsearch ベクトルバックエンドを追加**（`vector_backend="elasticsearch"`、`dense_vector`+kNN）— `docker-compose.yml`（ES8 単一ノード）・`ElasticsearchVectorStore`（VectorStore と同一IF・空間名前空間化・冪等add）・registry/config/index_builder 配線・`es` extra・`elasticsearch` pytest marker（既定除外）・`make es-up/es-down/test-es`・`configs/index/elasticsearch.yaml`。実 ES で統合テスト5件緑＋エンジン end-to-end 動作確認。既定はインメモリ維持で 275 緑不変 | 275+5(es) tests |
| P12 (06-14) | **埋め込みを `gemini-embedding-2` 単一に統一** — 教師/生徒二層・H7・`LocalStudentEmbedder`・`sentence-transformers`（student extra）・`eval h7` CLI・student マーカー・GEMMA/MINILM 空間・`student_model` 設定を撤去。factory は live=gemini / mock=決定的スタンドインに単純化。docs（PROJECT/CLAUDE）整合 | 275 tests |

詳細: `git log --follow IMPROVEMENT.md` / 各走の数値は REPORT.md。
