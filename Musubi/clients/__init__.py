"""Gemini adapters (ER / ADK / Embedding) — the ONE cloud choke point (CLAUDE.md §0-1, §7).

Every Gemini call in Musubi lives behind the VCR here. Nothing outside this package imports
``google.genai`` / ``google.adk``. Offline paths use ``FakeGeminiClient`` and committed cassettes.
"""

from __future__ import annotations
