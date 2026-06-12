# data/

実行時成果物の置き場。**`runs/` と `cache/` は gitignore 対象**（PROJECT.md §11）。

- `runs/<run_id>/` — 記録されたエピソード（観測ストリーム、知覚イベント、LLM呼び出し、構造化JSONLログ、マニフェスト）。**手動で削除しないこと**（CLAUDE.md §8）。
- `cache/llm/` — LLM応答のディスクキャッシュ（キー: モデル×プロンプトハッシュ）。テストは必ずこのキャッシュ経由で動く。
- `cache/embeddings/` — テキスト/視覚埋め込みのキャッシュ。
