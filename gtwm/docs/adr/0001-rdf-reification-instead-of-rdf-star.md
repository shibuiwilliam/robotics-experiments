# ADR-0001: 信念（Belief）の格納に RDF-star ではなく標準 RDF 具象化（reification）を使う

- 日付: 2026-09-12
- 状態: 採用

## 状況

CLAUDE.md の技術選定と `.claude/rules/ontology.md` は、信念（confidence・source・validFrom・transactionTime を伴う事実）を RDF-star の埋め込み三つ組で格納する前提で書かれている：

```
<< :Pallet_0042 gt:currentZone :Zone_B >> gt:confidence 0.93 ; gt:source gt:WM ; gt:validFrom ... ; gt:transactionTime ... .
```

セッション03（`kg`）の実装中に、固定されている `rdflib==7.6.0`（pyproject.toml）には Turtle-star / SPARQL-star のパーサが存在しないことを確認した：
- `rdflib.plugin.plugins(kind=Parser)` に star 構文対応のプラグインが無い。
- `<< s p o >>` 構文を含む Turtle を読ませると N3 パーサが `BadSyntax` で拒否する。

Oxigraph（統合バックエンド）は RDF-star をネイティブサポートするが、`KGStore` は rdflib（既定）と Oxigraph の2実装で同じ信念表現を扱う契約になっている（`ontology.md`「ストア」節）。バックエンド間で表現が割れると `snapshot(t)` やテストの二重実装が必要になる。

## 検討した選択肢

1. **Oxigraph をインプロセス既定にする**：RDF-star がネイティブに使えるが、CLAUDE.md は「rdflib 7（RDF-star）を既定、Oxigraph は統合用（Docker）」と明記しており、既定バックエンドが Docker 依存になるのは技術選定からの逸脱が大きい。オフライン・CI（`make test`、Docker 不要）を壊す。
2. **rdflib のバージョンを star 対応版に上げる／star 対応の別パーサライブラリを追加する**：調査時点で rdflib の安定版に Turtle-star パーサは存在しない（N3/Turtle パーサへの star 対応は継続議論中の未マージ機能）。依存関係を増やし `uv.lock` を不安定にするリスクがある。
3. **標準 RDF 具象化（reification）を共通表現として採用**（採用案）：`_:b a rdf:Statement, gt:Belief ; rdf:subject s ; rdf:predicate p ; rdf:object o ; gt:confidence c ; gt:source ... ; gt:validFrom ... ; gt:transactionTime ... .` は RDF 1.1 の標準機能で、rdflib・Oxigraph の両方で無条件にサポートされる。SPARQL でのクエリは埋め込み三つ組より冗長になるが、`kg/queries/*.rq` にファイル化してあるので影響は限定的。

## 決定

選択肢3を採用する。信念は標準 RDF 具象化で表現し、`src/gtwm/kg/schema.py` の `Belief.to_triples()` がこの表現を生成する。rdflib・Oxigraph の両バックエンドで同一の表現・同一のテストを使う。

## 結果と影響

- `ontology.md` の「信念と時間」節（RDF-star の記法例）は本 ADR により実装上は具象化に読み替える。ontology.md 自体の文言修正は本コミットでは行わない（将来 rdflib が star をサポートした場合に選択肢2へ切り替える余地を残すため）。
- SPARQL クエリ（`kg/queries/*.rq`）は具象化前提のパターン（`?b rdf:subject ?s ; rdf:predicate ?p ; rdf:object ?o`）で書く。
- 受入基準・`experiments/criteria.yaml` への影響はない（信念の格納表現はスコアリングロジックの対象外）。
- 将来 rdflib が Turtle-star に対応した場合、または Oxigraph 専用運用に切り替える場合は、本 ADR を "superseded" にして新 ADR を書く。

関連コミット: `f2c9724`（KGStore・schema.py 実装）。
