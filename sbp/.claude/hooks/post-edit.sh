#!/usr/bin/env bash
# PostToolUse hook: Python は ruff で整形、Turtle は構文検査。失敗はブロックせず警告のみ。
set -u
input="$(cat)"
path="$(printf '%s' "$input" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)"
[ -z "$path" ] && exit 0
case "$path" in
  *.py)
    uv run ruff format --quiet "$path" 2>/dev/null || true
    uv run ruff check --fix --quiet "$path" 2>/dev/null || true
    ;;
  *.ttl)
    uv run python -c "import rdflib,sys; rdflib.Graph().parse(sys.argv[1], format='turtle')" "$path" \
      || echo "WARNING: Turtle parse failed: $path" >&2
    ;;
esac
exit 0
