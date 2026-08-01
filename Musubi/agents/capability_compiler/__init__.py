"""Capability→tool compiler (FR-CAP, E4).

Turns an actor's advertised Capabilities into the concrete Skills it can execute, by matching
advertised action types against the registered skill types. A new robot is added by advertising a
Capability — no agent code changes (E4 invariant: ``git diff`` must not touch agent logic).
"""

from __future__ import annotations

from agents.capability_compiler.compiler import CompiledTool, compile_capabilities

__all__ = ["CompiledTool", "compile_capabilities"]
