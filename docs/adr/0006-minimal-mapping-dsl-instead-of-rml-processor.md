# ADR-0006: 写像は既存 RML プロセッサではなく最小の写像 DSL で実装する

- 日付: 2026-10-03
- 状態: 採択
- 触る層: integration（写像）、tests（契約テスト）

## 文脈

`.claude/rules/integration-llm.md` は写像を「RML / R2RML（`*.rml.ttl`）」で書くと定め、設計書 §7.2 は写像を関手 `F: S→O` として扱い、`Σ_F` / `Δ_F` と「写像ごとにキーの保存・必須制約の保存・多重度の保存を CI で検査する」ことを要求している。

M1-03 の着手時に Python で利用可能な [R2]RML プロセッサを調べた結果（2026-10-03 時点）:

| 候補 | 状態 | 判定 |
|---|---|---|
| `morph-kgc` 2.10.0（2026-01-20 更新） | 依存に **`pyoxigraph<0.4.0,>=0.3.0`**。さらに `duckdb`, `pandas`, `geopandas`, `neo4j`, `kafka-python`, `databricks-sqlalchemy`, `kuzu`, `falcon` などを要求 | **採用不可** |
| `pyrml` 0.5.1 | 最終アップロード **2016-07-31**。依存宣言なし、10 年間更新なし | 採用不可 |
| `rdfizer` 4.7.5 | SDM-RDFizer。活動はあるが設定ファイル形式が独自で、入力は主にファイル・RDB 前提 | 検討対象 |

`morph-kgc` の `pyoxigraph<0.4.0` 制約は**決定的な障害**である。ADR-0005 により、文単位メタデータを RDF 1.2 の triple term + `rdf:reifies` で表すには pyoxigraph 0.5 系が必要で、0.3 系では引用三つ組を主語に置く旧記法しか使えない。二時制アサーション API（M0-11、実装済み・テスト済み）が動かなくなるため、依存を下げる選択はできない。

また依存の重さも制約に反する。`duckdb` + `pandas` + `geopandas` + `neo4j` + `databricks` は、CLAUDE.md の「MacBook で学習・推論できる規模」「既存 4 サービスで足りることを示す」という方針と整合しない。写像 1 本のために RDB・グラフ DB・分析エンジンのクライアントを持ち込むことになる。

## 選択肢

1. **最小の写像 DSL を自前で実装する。** 宣言は YAML、実行は `sbp.integration.mappings` の小さな解釈器。
   - 利点: 依存ゼロ（`pyyaml` のみ、既に依存にある）。pyoxigraph 0.5 と衝突しない。SBP が実際に必要な写像（表の 1 行 → `Record` + `BusinessEvent`）に絞れる。設計書 §7.2 が要求する 3 つの保存性（キー・必須制約・多重度）を**写像仕様の一部として宣言でき**、契約テストで機械検査しやすい。
   - 欠点: RML の標準表現から外れる。外部の RML 資産を持ち込めない。写像の表現力は自前で足す必要がある。
2. `rdfizer` を使う。
   - 利点: RML 標準に近い。
   - 欠点: 入力が RDB/CSV 前提で、`RawRecord`（取り込み済みの dict）を直接渡す経路がない。設定形式が独自で、結局 SBP 側にアダプタを書くことになる。保存性の検査は別途自前になる。依存の重さは中程度。
3. pyoxigraph を 0.3 系に下げて `morph-kgc` を使う。
   - 欠点: **ADR-0005 が成立しなくなる。** 二時制アサーション API を書き直し、文メタデータの表現を旧 RDF-star に戻すことになる。標準化の方向に逆行し、M0 の実装とテストを捨てる。棄却。
4. 写像をコードで直接書く（宣言なし）。
   - 欠点: 業務スキーマ変更時に差分が追えない。「写像の版を上げる」（§7.2）という運用ができない。設計書が写像を一級の成果物として扱っている意図に反する。棄却。

## 決定

**選択肢 1 を採る。** 写像は YAML で宣言し、`sbp.integration.mappings` が解釈する。

```yaml
# mappings/work_order.yaml
mapping: work_order
version: 1.0.0
source: ent:CmmsSource
table: work_order
record_type: ent:WorkOrder

# キーの保存: 業務側の主キーが基盤側の external_id に 1 対 1 で写ること
key:
  columns: [wo_number]
  external_id: "{wo_number}"

# is_about: 対象実体の解決に使う参照（解決は resolver の責務）
subject_ref:
  column: asset_tag
  kind: asset

# 必須制約の保存: 業務側で必須の列が欠けたら写像を失敗させる
required: [wo_number, asset_tag, status, recorded_at]

# 多重度の保存: 1 行から生成する Record は 1 件（1:1）
cardinality: one_to_one

time:
  recorded_at: recorded_at
  occurred_at: completed_at

fields:
  wo_type: wo_type
  status: status
```

拡張子は `.yaml` とし、ルールが指定する `*.rml.ttl` は使わない（RML ではないものに RML の名を付けない）。`.claude/rules/integration-llm.md` の該当行を本 ADR への参照付きで更新する。

写像の 3 つの保存性は宣言から検査する（設計書 §7.2 の CI 要件）:

| 保存性 | 宣言 | 検査内容 |
|---|---|---|
| キーの保存 | `key.columns`, `key.external_id` | 業務側の主キー値が異なる 2 行が、同じ `external_id` にならない |
| 必須制約の保存 | `required` | 必須列が欠けた行は写像が失敗し、黙って不完全な `Record` を作らない |
| 多重度の保存 | `cardinality` | `one_to_one` 宣言の写像が 1 行から 2 件以上の `Record` を作らない |

これらを `tests/contract/test_mapping_<name>.py` で検査する（integration ルールの要求どおり）。

将来 RML 標準への移行が必要になった場合（外部 RML 資産の取り込み、他システムとの写像共有）、写像宣言は YAML 1 ファイルに閉じているため、`morph-kgc` 等が pyoxigraph 0.5 に対応した時点で変換できる。移行コストは写像の本数に比例し、現状は 4 本である。

## 影響

- 不変条件への影響: なし。写像の出力は `Record`（pydantic）であり、そこから先の経路（BusinessEvent 化、信念登録）は変わらない。
- オントロジー版: 影響なし。
- 依存: 追加なし（`pyyaml` は既に依存）。
- ルールへの反映: `.claude/rules/integration-llm.md` の「RML / R2RML（`*.rml.ttl`）」を「写像 DSL（`mappings/*.yaml`、ADR-0006）」に更新する。
- テストへの影響: `tests/contract/test_mapping_<name>.py` が保存性 3 種を検査する（ファイル名の規約はルールのまま）。
- 設計書 §7.2 との関係: 関手的データ移行という**考え方**（キー・必須制約・多重度の保存を写像の不変条件として検査する）は維持し、実装手段だけを変える。§7.2 に本 ADR への参照を入れる。

## 未決

`Δ_F`（オントロジー側の問いを業務側へ引き戻す向き）の実装。現状の写像 DSL は `Σ_F`（業務 → オントロジー）だけを扱う。引き戻しが必要になるのは書き戻し（M1-08）で業務側のキーを逆算するときであり、そこでは `key.external_id` のテンプレートを逆向きに使えば足りる見込み。M1-08 で確認する。
