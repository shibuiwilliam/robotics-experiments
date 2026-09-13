"""プロンプトファイル（`src/gtwm/llm/prompts/*.md`）のロード（llm.md「プロンプトは
ファイル化し、コードに文字列で書かない」）。"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str, **kwargs: object) -> str:
    """`prompts/<name>.md` を読み、`{key}` プレースホルダーを kwargs で埋める。"""
    path = _PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    for key, value in kwargs.items():
        text = text.replace(f"{{{key}}}", str(value))
    return text
