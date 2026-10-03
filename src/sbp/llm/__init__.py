"""LLM 層。Provider 抽象（anthropic / openai / gemini）、構造化出力、プロンプト版管理、replay。

責務: LLM 呼び出しの唯一の入口。レート制限・リトライ・タイムアウト・replay の一元管理。
非責務: 信念更新・照合・事象昇格のホットパスに入ること（不変条件 11）。
"""

from __future__ import annotations
