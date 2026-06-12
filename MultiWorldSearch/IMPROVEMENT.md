# IMPROVEMENT.md — 未完課題（2026-06-12 整理）

> **本書の位置づけ**
> 未完の修正・改善課題**のみ**を保持する。完了済み課題（P1–P10 の R1–R12 / C1–C6 / M1–M18 / E1–E4 / H7 ほか）の詳細・受け入れ確認は **git 履歴の本ファイル**（コミット `9e895f0` 以前）と **REPORT.md** を参照。
> 現状: live `make scenario-all` 7/7 PASS・突き合わせ監査ゼロ差分（embedding 64/64・実LLM 17/17）・mock **263 tests** 両シェル緑・manifest 完全再現可能（`git_dirty=false`）。

---

## 1. 未完課題

### M19 — 応答記録そのものが監査対象外（**Low-Medium・監査の完全性**）

- **現状**: `mws eval reconcile` は「ログのマーカー行数 ↔ metrics 計上値」の2点照合。M16 で導入した `runs/<RUN_ID>/llm_calls.jsonl` は照合対象外のため、**レコーダの故障（将来の配線漏れ・例外握りつぶし）が起きてもゼロ差分のまま検出されない**。記録は「あるはず」で信頼されている。
- **修正案**: `reconcile()` に第3の照合脚を追加 — 各 run の `llm_calls.jsonl` 行数を合算し `llm_calls_real` と diff（ファイル不在は 0 行として扱い、mock 実行と整合）。CLI 出力の actual/counted に `llm_calls_recorded` を追加し、差分≠0 で非0終了。
- **受け入れ基準**: live 実走で 3 点（ログ・metrics・記録ファイル）が全て一致。記録を 1 件欠落させた合成 run で監査が失敗する反証テスト。

### REPLAY — 記録応答の決定的リプレイ（**任意・再現実験用**）

- **現状**: 実 LLM 応答は記録される（M16）が、再生はできない。応答を固定した A/B 比較（CLAUDE.md §5.1 の「応答をキャッシュ／記録し固定」の後半）は未実装。
- **修正案**: `MWS_LLM_REPLAY=<path to llm_calls.jsonl>` で live エージェントが記録応答を順に決定的に返すモード。purpose 一致で対応付け、枯渇時は明示エラー。
- **着手条件**: 応答固定の A/B 比較が実際に必要になった時点で（現時点の検証要件は記録のみで充足）。

---

## 2. ブロック中（外部要因）

| 課題 | ブロッカー | 解除時のアクション |
|------|-----------|-------------------|
| H7 を本来の生徒 **EmbeddingGemma** で再実行 | Hugging Face の Gemma ライセンス未承諾（トークン有でも 401 gated） | ライセンス承諾 → `MWS_STUDENT_MODEL=google/embeddinggemma-300m` で `eval h7` 再実行（現結果は MiniLM-384 代替・開示済み） |

---

## 3. 任意バックログ（品質改善・優先度低）

| 課題 | 内容 |
|------|------|
| Recall@5 改善 | R@10 は 0.9–1.0 だが R@5 は 0.39–0.57。RRF 重みのチューニング（学習リランカ含む）が候補。変更時はゴールデンテスト＋アブレーションで回帰確認 |
| LLM 呼び出しの並列化 | S1/S5 の act は逐次 8 呼で ~35s。独立ステップの並行実行で短縮余地（エビデンスゲートの順序性に注意） |

---

## 4. 既知の制約（仕様として注記済み・対応不要と判断）

- **A2A 直接通信は未実装** — 協調は共有ストア経由のスティグマジーで実証（PROJECT.md §5.1 注記が正）。
- **Batch API の per-item token_count が None** — コストは chars/4 推定にフォールバックし `tokens_source: estimated` で機械可読に明示。SDK 側の `batches.create_embeddings` は experimental 注記あり。
- **S7 の gemini_infer は 1 呼/ラン** — 単一サンプルのため p95=p50（multi-seed 集計で分布化）。
- **フェーズ単位レイテンシは 1 ラン 1 サンプル** — per-call バケットと multi-seed 集計が実分布を提供。
- **S2 の perceive はロボット観測 3 件が単発埋め込み** — 文脈上バッチ化対象外（by-design）。

---

## 完了済みの記録（参照用サマリ）

| 波 | 内容 | 検証 |
|----|------|------|
| P1–P4 (2026-06-10) | シナリオの反証可能化（C1–C6/H1–H2/M1–M3/D1–D2）— 手書き結論を全廃しパイプライン出力でゲート | 187 tests |
| レビュー修正 (06-11) | R1–R12・M4–M7・H7 — 融合重みバグ/relational 発火/S2・S3 プロトコル迂回解消/VLA・QoR・consolidation 配線/知覚税/実 student A/B | 209 tests |
| P7 (06-11) | M8–M11＋prefetch＋転置インデックス — 実/モデル LLM 分離・S5/S7 live 化・live multi-seed | 220 tests |
| P8 (06-11) | M12–M14＋監査常設化 — ゼロ差分達成・実測トークン（chars/4 の 2.6 倍過大を訂正）・帯域実/仮想分離 | 228 tests |
| P9 (06-11) | E1–E4 — gemini-embedding-2 公式準拠（非対称接頭辞 A/B 支持 +0.056・単発バッチ -25%・Batch API・空間 v2）＋監査が実リーク9件を検出→修正 | 255 tests |
| P10 (06-12) | M15–M18 — manifest 完全化＋ツリーコミット・llm_calls.jsonl 記録・取込全面バッチ化（リクエスト -48%・perceive 最大 -92%）・v2 live CI | 263 tests |

詳細は git: `git log --follow IMPROVEMENT.md` / 各走の数値は REPORT.md。
