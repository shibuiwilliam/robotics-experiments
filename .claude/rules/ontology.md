---
paths:
  - "ontology/**"
  - "schemas/**"
  - "src/sbp/core/vocab.py"
  - "scripts/gen_*.py"
---
# オントロジー作業ルール

- 名前空間: `sbp-core:` = `https://sbp.example/core#`、`ent:` = `https://sbp.example/ent#`、`site-<id>:` = `https://sbp.example/site/<id>/`。新しい名前空間の追加は ADR。
- 上位オントロジー（BFO 2020 / IAO / SOSA-SSN / PROV-O / OWL-Time / GeoSPARQL / QUDT）は `ontology/imports/` に固定版で置く方針で、取得は `scripts/fetch_imports.sh`、改変禁止。ネットワーク経由の `owl:imports` 解決に頼らない。**2026-10-25 時点では未取得**（README だけ。OWL RL 推論を使う処理がまだ無い）。
- クラス・プロパティの必須注釈: `rdfs:label`（ja / en）、`skos:definition`、`sbp-core:layer`（L1..L4）、`sbp-core:owner`。欠けていると `make ontology-check` が落ちる。
- ICE（情報内容実体）の下位クラスは `iao:is_about` の定義域制約を持つ。`PhysicalEntity` の下位は `hasLocation` を SHACL で必須にする。
- `FluentType` / `EventType` は `ontology/process/*.lp`（clingo）の `initiates` / `terminates` と対応が取れていること。`make ontology-check` が未参照・未定義の型を検出する。
- 排他フルーエントは `sbp-core:mutuallyExclusiveWith` で宣言し、対応する `sh:Warning` 形状を必ず追加する。
- SHACL 形状の使い分け: `sh:Violation` = 構造制約（登録拒否 → 隔離キュー）、`sh:Warning` = 整合制約（登録して不一致候補）。検証は拒否ではなく証拠生成。
- 版管理: `ontology/VERSION` と `CHANGELOG.md`。MINOR までは既存プローブ・クエリ・`schemas/` の後方互換を `tests/contract/test_ontology_compat.py` で証明する。非推奨は `owl:deprecated true` と `dcterms:isReplacedBy` を付け、2 MINOR 版残す。
- `schemas/*.json` と `src/sbp/core/vocab.py` は生成物。手編集しても `make gen` で消える。生成差分は生成専用コミットに分ける。
- 標準にある概念を再定義しない。迷ったら「物理と業務を結ぶためだけに必要な概念か」で判断し、該当するもの（Situation, Discrepancy, Expectation, IdentityClaim, Track, Anchor, Probe, ModelVersion, DataSource）だけ独自定義する。
- Turtle の構文は編集時のフック（`.claude/hooks/post-edit.sh`、rdflib で解析）と `make ontology-check` で検査する。**`make lint` は Turtle を正規化しない**（ruff だけ。2026-10-25 に確認）。プレフィクス宣言はファイル冒頭に集約する。
