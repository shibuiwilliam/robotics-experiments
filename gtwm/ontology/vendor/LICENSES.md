# vendor/ 出典とライセンス

| ファイル | 出典 URL | 種別 | ライセンス | 状態 |
|---|---|---|---|---|
| `prov-o.ttl` | https://www.w3.org/ns/prov.ttl | Turtle（公式） | [W3C Document License](https://www.w3.org/Consortium/Legal/2015/doc-license) | 実物 |
| `sosa.ttl` | https://www.w3.org/ns/sosa/ （`Accept: text/turtle`） | Turtle（公式） | W3C Document License | 実物 |
| `ssn.ttl` | https://www.w3.org/ns/ssn/ （`Accept: text/turtle`） | Turtle（公式） | W3C Document License | 実物 |
| `bfo.owl` | http://purl.obolibrary.org/obo/bfo.owl | RDF/XML（OBO PURL 経由の公式配布） | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)（BFO 2020, BFO-ontology/BFO-2020） | 実物（RDF/XML のまま。Turtle への変換はしていない） |
| `epcis-2.0-context.jsonld` | https://ref.gs1.org/standards/epcis/epcis-context.jsonld | JSON-LD `@context`（公式） | [GS1 IP Policy](https://www.gs1.org/policies/ip)（royalty-free） | 実物。ただし OWL の公理（クラス階層・制約）を含む完全なオントロジーではなく、EPCIS 2.0 の語彙・プレフィックス定義（`@context`）のみ |
| `cbv-stub.ttl` | なし（**スタブ**） | 自作 Turtle | — | **スタブ**。理由は下記 |

## CBV がスタブである理由

GS1 Core Business Vocabulary (CBV) 2.0 の機械可読な RDF/Turtle/JSON-LD 配布物を、この環境から到達可能な URL では取得できなかった：

- `https://ref.gs1.org/cbv/CBV-JSONLDContext` → 404
- `https://ref.gs1.org/cbv/2.0.0/CBV-JSONLDContext` → 404
- `https://ref.gs1.org/cbv/CBV-JSONLDContext.jsonld` → 404
- `https://gs1.org/voc/` / `https://www.gs1.org/voc/` → 403（bot ブロックと思われる）

公式 CBV は https://ref.gs1.org/cbv/ に HTML/PDF 仕様書として公開されているのみで、EPCIS 2.0 の `@context`（`epcis-2.0-context.jsonld`）が `cbv:` プレフィックスを `https://ref.gs1.org/cbv/` に割り当てているのは確認できたが、そのURL自体はRDF文書ではない。

そのため `cbv-stub.ttl` は、`src/gtwm/sim/wms_mock.py`（`CBV_BIZSTEP`, `CBV_DISPOSITION_ACTIVE`）と `ontology/gt-core.ttl` が実際に参照する CBV の biz-step / disposition URI（`urn:epcglobal:cbv:bizstep:{receiving,inspecting,storing,picking,shipping}`、`urn:epcglobal:cbv:disp:active`）だけを `skos:Concept` として最小定義したものであり、CBV 全体の正式なインポートではない。将来 CBV の公式 RDF 配布が見つかった場合は、このスタブを置き換えること。

## 備考

- いずれのファイルも `ontology/gt-core.ttl` からは `owl:imports` していない（PoC では `gt-core.ttl` は独立して読み込め、vendor は将来の統合・相互参照用に配置するに留める）。
- IOF Core は poc_plan.md の方針どおり参照のみとし、ファイルは配置しない。
