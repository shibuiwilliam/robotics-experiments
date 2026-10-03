# 上位オントロジー（取得物・改変禁止）

このディレクトリには標準オントロジーを固定版で置く。取得は `scripts/fetch_imports.sh`。

- 改変禁止（`.claude/settings.json` が編集を拒否する）。
- ネットワーク経由の `owl:imports` 解決に頼らない（CI・オフライン環境で再現しないため）。
- 版を上げるときは `scripts/fetch_imports.sh` の MANIFEST（URL と sha256）を更新し、
  `ontology/CHANGELOG.md` に記録する。上位層の版変更は再接地を伴いうるため MAJOR 相当として扱う。

採用する標準（設計書 §3.11）:

| 目的 | 標準 |
|---|---|
| 上位区分 | BFO 2020 |
| 主張と対象 | IAO（ICE と `is_about`） |
| 観測・仮想センサ | SOSA / SSN |
| 来歴 | PROV-O |
| 時間 | OWL-Time |
| 空間 | GeoSPARQL |
| 単位付き量 | QUDT |

## 現状（2026-10-03）

まだ取得していない。M0〜M2 の `make ontology-check` は自己検証のみで、これらを必要としない。
OWL RL 推論（M3 の派生述語・型推論）に着手する時点で `scripts/fetch_imports.sh` を実行する。
