---
paths:
  - "src/sbp/knowledge/**"
  - "src/sbp/eventlog/**"
  - "infra/sql/**"
---
# 信念・知識層 / イベントログのルール

## イベントログ（Redpanda）
- トピック名は `<site>.<layer>.<class>`（例: `siteA.bridge.physical`, `siteA.integration.business`, `central.reasoning.discrepancy`）。キーは subject の IRI。トピック追加は `scripts/topics.py` に書き、`make seed` で作成する（手作業禁止）。
- Producer は `EventEnvelope`（pydantic）以外を送れない。送信前に `schemas/` の JSON Schema で検証する。
- Consumer は at-least-once。副作用は `event_id` で冪等化する（投影テーブル `processed_events(event_id)` を先に確認）。
- 順序は実体単位。グローバルな時刻順を前提にした処理を書かない。`causation_id` / `correlation_id` を必ず引き継ぐ。

## 二時制アサーション API（`sbp.knowledge.assertions`）
- 公開関数は `assert_(...)`, `retract(assertion_id, at)`, `query(as_of=None, valid_at=None, modality=None, min_confidence=None, ...)` のみ。SPARQL の文字列はこのモジュール内に閉じる。
- 訂正は `tx_to` を閉じて新文を追加する。物理削除の API を作らない（法令上の消去は別途 ADR）。
- 名前付きグラフ: `urn:sbp:g:<modality>:<site>:<ontology_version>`。`hypothetical` は `urn:sbp:scenario:<scenario_id>` に隔離する。
- 文メタデータは RDF 1.2 の triple term + `rdf:reifies`（`<assertion/{id}> rdf:reifies <<( s p o )>> ; sbp:txFrom ...`）。
  旧 RDF-star の「引用三つ組を主語に置く」記法は pyoxigraph 0.5 では使えない（ADR-0005）。
  開いている区間は `validTo` / `txTo` のトリプルを**置かない**ことで表す（null リテラルを使わない）。
  pyoxigraph は実行時のグラフ操作に、rdflib は TTL の読み書きにだけ使う。
- 埋め込み pyoxigraph と Compose の oxigraph サーバは同じ版（0.5.11）に固定する。記法のサポートが版で変わるため。
- 予測は `horizon_origin` 違いで共存させる。既定クエリは最新起点、精度追跡は全起点を返す。
- `query` は必ず `ontology_version` を返す。呼び出し側が版を無視して比較しないようにする。

## 潜在索引（pgvector）
- テーブル `latent_index_d<次元>(entity, ts, level, wm_version, vec vector(D))`、HNSW（cosine）。`level` と `wm_version` で絞ってから近傍検索する。
- 次元 D は `wm_version` ごとに固定。表は次元ごとに `latent_index_d<次元>` とし、版 → 表の対応は登録表 `latent_index_registry(wm_version, table_name, dim)` が持つ（アプリは登録表を引いてから書く。`docs/PROGRESS.md` 所見 19）。次元が変わる WM 更新は新しい次元の表をマイグレーションで作り、移行は再接地ジョブで行う。アプリは実行時に表を作らない（所見 53）。
- ハイブリッドクエリ（グラフ述語 + 近傍）は `sbp.knowledge.hybrid.plan()` を通す。選択率で押し下げ順を決める。

## PROV
- 活動種別は `vocab.prov_activities` の列挙のみ（probe-run, rule-run, belief-update, reconciliation, llm-extraction, human-review, regrounding）。
- 来歴はバンドル単位で保存し、証拠は参照で持つ。展開は説明サービスの責務。

## 再接地（`sbp.knowledge.regrounding`）
- `plan(scope)` → コスト見積り → `run(plan)` の順。旧アサーションは `tx_to` を閉じるだけで消さない。
- 差分レポート（確信度変化 ≥ 閾値、新規 / 消滅した不一致）を `docs/runbooks/regrounding.md` の形式で出力する。

## SQL
- マイグレーションは alembic（`infra/sql/versions/`）。hypertable 化（`create_hypertable`）もマイグレーションに含める。DDL を手で流さない。
- 時刻列は `timestamptz`。区間は `tstzrange`。
