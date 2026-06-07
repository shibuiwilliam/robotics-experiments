"""Execution mode for scenario evaluation — OFFLINE or ONLINE.

OFFLINE (default, free): MuJoCo physics + direct MCP handler calls.
ONLINE (PSL_MODE=online): Full path — MuJoCo + pseudo-cloud HTTP +
  VLA encoder + Claude Agent SDK with real MCP tool calls.

Auto-detection: if PSL_MODE is not set explicitly, checks for
ANTHROPIC_API_KEY + claude_agent_sdk importability. Falls back
gracefully with warnings — never crashes.
"""

from __future__ import annotations

import os
import warnings
from enum import Enum


class ExecutionMode(Enum):
    """Scenario execution mode."""

    OFFLINE = "offline"
    ONLINE = "online"


def detect_mode() -> ExecutionMode:
    """Detect execution mode from environment.

    Priority:
      1. PSL_MODE env var ("online" or "offline") — explicit override
      2. Auto-detect: ONLINE if ANTHROPIC_API_KEY is set AND
         claude_agent_sdk is importable
      3. Default: OFFLINE

    Returns:
        ExecutionMode.ONLINE or ExecutionMode.OFFLINE.
    """
    explicit = os.environ.get("PSL_MODE", "").strip().lower()

    if explicit == "offline":
        return ExecutionMode.OFFLINE

    if explicit == "online":
        if not os.environ.get("ANTHROPIC_API_KEY", ""):
            warnings.warn(
                "PSL_MODE=online but ANTHROPIC_API_KEY not set. Falling back to offline.",
                RuntimeWarning,
                stacklevel=2,
            )
            return ExecutionMode.OFFLINE
        if not _sdk_importable():
            warnings.warn(
                "PSL_MODE=online but claude_agent_sdk not installed. Falling back to offline.",
                RuntimeWarning,
                stacklevel=2,
            )
            return ExecutionMode.OFFLINE
        return ExecutionMode.ONLINE

    # Auto-detect: online only if both key and SDK are available
    if os.environ.get("ANTHROPIC_API_KEY", "") and _sdk_importable():
        return ExecutionMode.ONLINE

    return ExecutionMode.OFFLINE


def _sdk_importable() -> bool:
    """Check if claude_agent_sdk can be imported."""
    try:
        import claude_agent_sdk  # noqa: F401

        return True
    except ImportError:
        return False
