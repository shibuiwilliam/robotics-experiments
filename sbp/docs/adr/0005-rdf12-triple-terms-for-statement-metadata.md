# ADR-0005: 文単位メタデータを RDF 1.2 の triple term + `rdf:reifies` で表す

- 日付: 2026-10-03
- 状態: 採択
- 触る層: knowledge, ontology（クエリ記法）, docs/design（§6.3・§6.4・§6.6 の記法更新）

## 文脈

設計書 §6.3 は文単位メタデータの実装に **RDF-star** を選び、§6.6 のクエリ例は次の記法を使っている。

```sparql
<< ?pump sbp:hasState ent:Failed >> sbp:modality sbp:Predicted ;
                                     sbp:leadTime ?lead .
```

これは「引用三つ組（quoted triple）を**主語**に置く」旧 RDF-star の記法である。M0-11 の着手前スパイク（`docs/PROMPT.md` の M0-11 プロンプトが指示する手順）で、**pyoxigraph 0.5.11 ではこの記法が使えない**ことが判明した。

```
>>> store.add(ox.Quad(ox.Triple(S, P, O), TXF, ..., G))
TypeError: argument 'subject': failed to extract enum PyNamedOrBlankNode ('NamedNode | BlankNode')
```

理由は Oxigraph が RDF 1.2 の標準化結果に追随したことである。RDF 1.2 では quoted triple が **triple term** に再定義され、**目的語位置にのみ出現できる**。文への注釈は `rdf:reifies` を介した reifier を主語にして行う。

スパイクで確認した事実（pyoxigraph 0.5.11 の埋め込みストアと Compose の `oxigraph/oxigraph:0.5.11` サーバの両方）:

| 試したこと | 結果 |
|---|---|
| `Triple` を Quad の主語に置く | **不可**（上記 TypeError） |
| `Triple` を Quad の目的語に置く | 可 |
| Turtle 1.2 の `<<( s p o )>>` 記法のパース | 可（3 quads として読める） |
| `?a rdf:reifies <<( ?s ?p ?o )>>` + メタデータで絞る SPARQL | **可**（as-of 絞り込みが動作） |
| 同じクエリを HTTP サーバの `/query` に投げる | **可**（HTTP 200、期待どおり 1 行） |
| N-Quads へのダンプ | 可 |

つまり**要件（文単位メタデータ、as-of / valid-at の絞り込み）は満たせる**。満たせないのは設計書が書いた**記法**だけである。

## 選択肢

1. **RDF 1.2 の triple term + `rdf:reifies` を使う。** 設計書の記法を更新する。
   - 利点: W3C の標準化結果に沿う。ストアの機能をそのまま使える。文の識別子（reifier IRI）が明示的に存在するため、`assertion_id`（付録 A.1）をそのまま reifier IRI に対応づけられ、来歴バンドルからの参照も自然になる。
   - 欠点: 設計書 §6.6 のクエリ例を書き換える必要がある。1 文につき `rdf:reifies` の 1 トリプルが増える。
2. 補助テーブル（Postgres）に文メタデータを持ち、グラフには裸の三つ組だけを置く。
   - 利点: 記法問題を回避。時間範囲索引が B-tree で効く。
   - 欠点: **信念の二時制が 2 つのストアに分かれる。** as-of クエリがグラフ横断の結合になり、SPARQL だけで監査できなくなる（非機能要求「任意のアサーションから証拠まで 3 ホップ以内」が崩れる）。設計書 §6.3 が「主ストアは RDF、プロパティグラフは派生ビュー」と決めた理由（標準性・来歴・推論）を捨てることになる。棄却。
3. 旧 RDF-star を使える古い Oxigraph（0.3 系）に固定する。
   - 利点: 設計書の記法をそのまま使える。
   - 欠点: 標準化前の記法に将来を賭ける。0.3 系は RDF 1.2 への移行で保守が止まる方向であり、セキュリティ更新も見込めない。棄却。
4. 各文を名前付きグラフ 1 つに入れ、グラフ IRI にメタデータを付ける（graph-per-statement）。
   - 利点: 標準の RDF 1.1 だけで済む。
   - 欠点: 名前付きグラフを `<modality>/<site>/<ontologyVersion>` で分割する設計（§6.3）と衝突する。グラフ数がアサーション数と同じになり、ストアの索引が肥大する。棄却。

## 決定

**選択肢 1 を採る。** 文メタデータは次の形で表す。

```turtle
# アサーション 1 件 = reifier IRI 1 つ + triple term 1 つ + メタデータのトリプル群
<https://sbp.example/site/siteA/assertion/{assertion_id}>
    rdf:reifies <<( <...asset/P-101> ent:Running true )>> ;
    sbp:validFrom "..."^^xsd:dateTime ;
    sbp:validTo   "..."^^xsd:dateTime ;   # 開いているときは省略
    sbp:txFrom    "..."^^xsd:dateTime ;
    sbp:txTo      "..."^^xsd:dateTime ;   # 開いているときは省略
    sbp:modality  sbp:Inferred ;
    sbp:confidence "0.91"^^xsd:double ;
    sbp:provenance <...prov/bundle/...> ;
    sbp:ontologyVersion "0.1.0" .
```

- reifier IRI は `Assertion.assertion_id`（付録 A.1）から導く。これにより Python 側の `Assertion` と
  グラフ上の 1 文が 1 対 1 に対応し、`retract` が対象を一意に指せる。
- 「開いている区間」は `validTo` / `txTo` の**トリプルを置かない**ことで表す（`null` リテラルを置かない）。
  as-of / valid-at のクエリは `OPTIONAL` + `!BOUND(...)` で扱う（スパイクで動作を確認済み）。
- `sbp:validFrom` などの述語は L1 中核オントロジーに追加する（オントロジー MINOR 更新）。

設計書 §6.3・§6.4・§6.6 の該当箇所に本 ADR への参照と更新後の記法を反映する。

## 影響

- 不変条件への影響:
  - 1（全アサーションが必須フィールドを持つ）: 表現方法が変わるだけで要件は不変。SHACL 形状で
    reifier が必須述語を揃えていることを検証できる（むしろ検証しやすくなる）。
  - 2（追記専用）: 訂正は旧 reifier に `txTo` を**追加**し、新 reifier を作る。トリプルの削除を伴わない。
- オントロジー版: **0.2.0（MINOR）** になる。`sbp:validFrom` / `validTo` / `txFrom` / `txTo` /
  `modality` / `confidence` / `provenance` / `ontologyVersion` / `scenario` / `situation` /
  `horizonOrigin` / `leadTime` / `sourceReliability` を加法的に追加する。既存プローブ・クエリは無いため
  後方互換の破壊はない。再接地は不要（既存データなし）。
- テストへの影響: 二時制の性質テスト（as-of 単調性・訂正の非破壊性・tx 区間の無重複）は記法に
  依存しない形で書ける。SPARQL 文字列は `sbp.knowledge` 内に閉じる（knowledge ルール）ので、
  記法変更の影響範囲もその 1 モジュールに留まる。
- 版の固定: 埋め込み pyoxigraph と Compose の Oxigraph サーバを**同じ 0.5.11 に固定する**ことが
  前提になる（記法のサポート状況が版で変わるため）。`pyproject.toml` で `>=0.5.11,<0.6` に制約した。

## 未決

RDF 1.2 の `<< s p o ~ ?r >>` 短縮記法（reifier を同時に束縛する糖衣）を使うかどうか。
pyoxigraph での対応を確認していない。明示的な `rdf:reifies` で書けば済むため急がない。
