---
paths:
  - "ontology/**"
  - "src/gtwm/kg/**"
  - "tests/**/test_kg*"
---

# オントロジーと KG（C6, C8, C10）

## 名前空間と語彙
- PoC 拡張は `gt: <https://example.org/gtwm/gt#>`（`ontology/gt-core.ttl`）。名前空間の変更は ADR 必須。
- 標準語彙は `ontology/vendor/` にコピーし、`LICENSES.md` に出典と条件を書く：EPCIS 2.0 オントロジー、CBV、SOSA/SSN、PROV-O、BFO（IOF Core は参照のみ）。
- クラスは PascalCase（`gt:Pallet`）、プロパティは camelCase（`gt:currentZone`）、個体は `gt:Pallet_0042` 形式。全てのクラス・プロパティに `rdfs:label`（ja と en）と `rdfs:comment` を付ける。
- 継続体（`gt:Pallet` `gt:Case` `gt:Vehicle` `gt:Worker`（匿名）`gt:Zone` `gt:Slot` `gt:Equipment`）はオントロジーが定義する。生起体（移動、荷役、検品、待機）は WM が学習し、記号化は EPCIS イベントとしてのみ行う。
- 計画書 5.3 の PoC 拡張表（`gt:Belief` `gt:Anchor` `gt:Discrepancy` `gt:LatentRef` `gt:Intervention` `gt:ConceptCandidate` と各プロパティ）を過不足なく実装する。

## 信念と時間
- 信念は RDF-star：`<< :Pallet_0042 gt:currentZone :Zone_B >> gt:confidence 0.93 ; gt:source gt:WM ; gt:validFrom ... ; gt:transactionTime ...`。
- バイテンポラル：`gt:validFrom/validTo`（有効時間）と `gt:transactionTime`（処理時間）を必ず両方持つ。遅延登録の乖離はこの差で定義する。
- 確信度 0.7 未満の信念は SHACL 検証の対象外。違反の確信度は関与する信念の確信度の積。

## ストア
- `kg/store.py` の `KGStore` は `add_beliefs()` `query(sparql)` `validate(shapes)` `snapshot(t)` を持つ。実装は rdflib（既定、インプロセス）と Oxigraph（`http://localhost:7878`、Docker）の2つで、テストは両方を通す（Oxigraph は `integration` マーカー）。
- SPARQL はファイル化して `src/gtwm/kg/queries/*.rq` に置き、文字列をコードに埋め込まない。
- スナップショット `snapshot(t)` は有効時間 t で成り立つ信念だけを返す。ε の計算はこの関数を使う。

## SHACL
- 形状は `ontology/shapes/*.ttl`。最初に実装する3本：単一ゾーン（`gt:PalletSingleLocationShape`）、危険物隣接禁止、ゾーン容量（計画書 5.3 の例）。
- 新しい形状は `sh:severity sh:Warning` で入れ、誤報率を見て `sh:Violation` に昇格する。昇格は ADR に一行で記録する。
- SPARQL 制約は pyshacl の `advanced=True` で実行する。`tests/unit/test_kg_shapes.py` に「違反するグラフ」と「適合するグラフ」の両方の例を置く。

## EPCIS と WHAT-IF
- `kg/epcis.py`：WMS モックの JSON-LD を取り込み、`record` 出所の信念に変換する。EPCIS の `eventTime` を有効時間、受信時刻を処理時間とする。
- `kg/whatif/grammar.lark` は付録 C の BNF と一致させる。文法変更は付録 C を先に更新する。コンパイラは個体→潜在スロット／行動変数の解決に失敗したら例外を投げ、シミュレーション代替を提案するメッセージを返す。

## LLM の扱い
- ラベル・コメントの下書き、SPARQL の候補生成には `gtwm.llm` を使ってよい。生成物をレビューなしで ttl にコミットしない。
- オントロジーの自動編集（クラス追加）はしない。概念候補は `gt:ConceptCandidate` として保留し、人が承認したものだけ `gt-core.ttl` に手で追加する。
