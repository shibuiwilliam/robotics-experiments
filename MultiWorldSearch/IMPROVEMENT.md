# IMPROVEMENT.md — 未完課題

> **本書の位置づけ**
> **未完の修正・改善課題のみ**を保持する。完了済み課題・既知の制約・対応不要/不可能と判断した事項は、混乱を避けるため本書には残さない（経緯は `git log --follow IMPROVEMENT.md` と `REPORT.md` を参照）。

---

## 未完課題

> 2026-06-14 `make scenario-all` 検証走（**ライブ**・全7シナリオ・ES）で判明した計測・運用上のギャップ。
> 詳細・数値は `REPORT.md` を参照。いずれも「結論の信頼性」ではなく「計測網羅性・コスト統制」の課題。

### G1 — ライブ走のコスト/トークンが記録されず、spend 確認ゲートも無い（高・コスト統制）
- **現状**: `make scenario-all` は cloud mode を明示せず、`.env`（`MWS_CLOUD_MODE=live`＋`GOOGLE_API_KEY`）を CLI が読み込むため**既定 mock ではなくライブ＝実課金**になる。2026-06-14 走は実 Gemini（埋め込み＋ADK、live LLM 呼: S1=10/S5=8/S7=1）を実行した。にもかかわらず per-run のトークン数・コストが `manifest.json`/`metrics.json` に残らず、事後のコスト監査ができない。`scenario-multi-seed-live` にある `MWS_CONFIRM_LIVE_SPEND=1` 相当のゲートが `scenario-all` には無く、**無確認で課金**しうる。
- **修正案**: (a) ライブ時は `manifest`/`metrics` に `cloud_calls`（embed/LLM 件数）・推定 `cost_usd`・`tokens`（取得可能なら）を焼き込む。(b) `scenario-all` がライブ解決された場合は実行前に推定コストを表示し、`MWS_CONFIRM_LIVE_SPEND=1` を要求する（mock 時は素通し）。
- **受け入れ基準**: ライブ走の各 `manifest.json` に cloud 呼び出し件数とコスト推定が記録される／ライブ `scenario-all` が未確認時に非ゼロ終了でコスト見積りを表示するテスト（mock では従来通り素通し）。

### G2 — シナリオ走の cloud mode が走行前に不可視（中・運用/可観測性）
- **現状**: `scenario-all` の docstring・コンソール出力に解決後の cloud mode 表示が無い。ライブ/モックの別は実行後に `manifest.cloud_mode` を見るまで分からない（本走も後追いで live と判明）。
- **修正案**: シナリオ走の冒頭バナーに `cloud_mode` / embedding model / vector backend を1行表示。Makefile docstring に「`.env` がライブなら実課金」と明記。
- **受け入れ基準**: `scenario run`/`scenario-all` の標準出力先頭に mode 行が出る（テストで検証）。

### G3 — 知覚税（H3）が S1 でしか算出されない（中・計測網羅性）
- **現状**: 特権オラクル差分（`perception_tax`）は `maintenance_handoff` のみ記録。他6シナリオは未算出（本走で確認: tax@10 は S1=0.1、他は None）。PROJECT.md は H3 を「1, 全般」と位置づける。
- **修正案**: オラクル検索が定義可能な検索中心シナリオ（S3/S5/S7 等）にも `perception_tax` を算出。算出不能なシナリオ（タスク成功型: S4/S6）はその旨を metrics に明示（`perception_tax: null` + 理由）。
- **受け入れ基準**: 検索ラベルを持つ全シナリオの `metrics.json` に `perception_tax` が入る／非該当シナリオは理由付きで null。

### G4 — ライブ E2E 走に受け入れ PASS/FAIL アサートが無い（中・検証完全性）
- **現状**: `scenario-all` の `[2/2]`（ES ライブ走）は指標を印字するのみで受け入れ閾値を判定しない。判定は `[1/2]` の mock pytest（in-memory）に限られ、**ライブ＋ES 経路は情報提供に留まる**。
- **修正案**: 各シナリオの受け入れ基準（SCENARIOS.md §3）をライブ走後の `metrics.json` に対しても判定し、未達なら非ゼロ終了。
- **受け入れ基準**: ライブ `scenario-all` が1シナリオでも受け入れ未達なら fail する（緑/赤がコンソールに出る）。

### G5 — `scenario-all` は単一 seed で CI が付かない（低・統計）
- **現状**: seed 0 の点推定のみ。95% CI は別ターゲット `scenario-multi-seed-all` 依存で、`scenario-all` 単独では統計的裏付けが付かない。
- **修正案**: docstring に「点推定・CI は multi-seed 参照」と明記、または `scenario-all` に軽量な数 seed 集計オプションを追加。
- **受け入れ基準**: 利用者が単一 seed であることを実行前に認識できる（docstring/バナー）。

### G6 — ES バックエンド時のベクトル検索レイテンシが `local_ann` バケットで計時される（中・計測の妥当性）
- **現状**: `engine.search` は `stores.vector.search` を一律 `local_ann` バケットで計時する。`vector_backend=elasticsearch` のとき、これは実体として **ES への HTTP 往復**であり「local ANN」ではない。2026-06-14 の `scenario-multi-seed-all`（ES）走では `local_ann_p50 = 4.25ms`（in-memory 走の約 0.04ms の ~100倍）と計測され、バケット名と実体が乖離。バックエンドによりレイテンシ分解（CLAUDE.md §10）の意味が変わり誤読されうる。
- **修正案**: ベクトル検索の計時バケットをバックエンド別に分離する（例: ES 時は `es_search`、in-memory/LanceDB は `local_ann`）。`RetrievalEngine` がストア種別から適切なバケット名を選ぶ、またはストアが `latency_bucket` を公開する（embedder の `latency_bucket` と同じ手法）。
- **受け入れ基準**: ES 走の metrics に ES 検索往復が独立バケットで出る／in-memory・LanceDB は従来どおり `local_ann`。テスト付き。

---

新たな課題が見つかったら、本セクションに次の形式で追記する:

```
### <ID> — <一言要約>（<深刻度>・<分類>）
- **現状**: 何が問題か（再現条件・影響範囲）
- **修正案**: どう直すか
- **受け入れ基準**: 何をもって完了とするか（テスト要件含む）
```
