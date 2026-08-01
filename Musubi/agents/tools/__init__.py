"""Musubi FunctionTools — the agent's typed interface to the semantic core.

entity_resolve / claim_query / norm_check / plan_validate wrap core services. The ScriptedPlanner
calls them directly; the GeminiPlanner exposes them to ADK as custom FunctionTools (no built-in
tools mixed in, CLAUDE.md §8). I/O shapes come from the ontology, never hand-written.
"""

from __future__ import annotations

from agents.tools.tools import MusubiTools

__all__ = ["MusubiTools"]
