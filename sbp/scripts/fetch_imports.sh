#!/usr/bin/env bash
# 上位オントロジーを固定版で ontology/imports/ に取得する（ontology ルール）。
#
# ネットワーク経由の owl:imports 解決には頼らない。取得したファイルは改変禁止で、
# .claude/settings.json が ontology/imports/ の編集を拒否する。
#
# これらが必要になるのは OWL RL 推論（M3 以降）と SHACL のクラス制約検証を標準語彙まで
# 広げるときである。M0〜M2 の ontology-check は imports なしでも通る（自己検証のみ）。
set -euo pipefail

cd "$(dirname "$0")/../ontology/imports"

# name|url|sha256（sha256 は初回取得時に scripts/fetch_imports.sh --record で記録する）
MANIFEST=(
  "bfo-2020.owl|http://purl.obolibrary.org/obo/bfo/2020/bfo.owl|"
  "iao.owl|http://purl.obolibrary.org/obo/iao.owl|"
  "sosa.ttl|https://www.w3.org/ns/sosa.ttl|"
  "ssn.ttl|https://www.w3.org/ns/ssn.ttl|"
  "prov-o.ttl|https://www.w3.org/ns/prov-o.ttl|"
  "time.ttl|https://www.w3.org/2006/time.ttl|"
  "geosparql.ttl|http://www.opengis.net/ont/geosparql|"
  "qudt-schema.ttl|http://qudt.org/schema/qudt|"
)

record_mode=false
[[ "${1:-}" == "--record" ]] && record_mode=true

for entry in "${MANIFEST[@]}"; do
  IFS='|' read -r name url expected <<< "$entry"
  if [[ -f "$name" && "$record_mode" == false ]]; then
    echo "skip   $name (already present)"
    continue
  fi
  echo "fetch  $name <- $url"
  curl -fsSL --retry 3 -o "$name.tmp" "$url"
  actual="$(shasum -a 256 "$name.tmp" | cut -d' ' -f1)"
  if [[ -n "$expected" && "$actual" != "$expected" ]]; then
    rm -f "$name.tmp"
    echo "ERROR  $name checksum mismatch: expected $expected, got $actual" >&2
    exit 1
  fi
  mv "$name.tmp" "$name"
  echo "       sha256 $actual"
done

echo
echo "注意: 取得したファイルは改変しない。版を上げるときは MANIFEST の URL と sha256 を"
echo "      更新し、ontology/CHANGELOG.md に記録する（上位層の版変更は MAJOR 相当）。"
