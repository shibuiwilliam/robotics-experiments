# コンピテンシー質問（CQ）

**CQはオントロジーの仕様であり、回帰テストである**（PROJECT.md §6）。
CQに答えられなくなる語彙変更はリグレッションとみなす。

実行: `uv run pytest tests/cq -q` または `uv run orx cq`

## 記述形式（P0で確定）

各CQは本ディレクトリの1 YAMLファイル:

```yaml
id: cq01_barcodes_in_shelf          # ファイル名と一致させる
question: "棚Aに現在ある識別子付きの箱のバーコードは何か"   # 自然言語
sparql: |                            # 期待SPARQL。現在信念は CURRENT_GRAPH
  PREFIX orx-upper: <https://orx.local/onto/upper#>
  PREFIX orx-st: <https://orx.local/onto/st#>
  SELECT ?bc WHERE {
    GRAPH <https://orx.local/id/graph/current> {
      ?e orx-st:inZone <https://orx.local/id/zone/shelf_a> ;
         orx-upper:hasIdentifier ?bc .
    }
  }
truth_fn: barcodes_in_zone           # orx.exp.cq の真値導出レジストリのキー
truth_args: {zone: shelf_a}
compare: set                         # set | count
```

- `sparql` は世界グラフに対して実行される。**現在信念**は物質化グラフ
  `<https://orx.local/id/graph/current>`、来歴メタデータは
  `<https://orx.local/id/graph/meta>`、各主張の生グラフは claim IRI で引ける。
- `truth_fn` は oracle の TruthState から正解を機械導出する関数
  （`orx/exp/cq.py` の `TRUTH_FNS`）。新しい導出規則が必要なら関数を追加する。
- 正準フィクスチャは `configs/world/demo_tiny.yaml`（12秒・seed 7・stub）。

## 追加手順

1. YAMLを置く → 2. 必要なら truth_fn を追加 → 3. `uv run pytest tests/cq -q` で
   合格を確認。**oracle の D2 述語集合に触る変更は `orx/oracle/truth.py` と
   ここの両方を同時に更新すること**（CLAUDE.md §9）。
