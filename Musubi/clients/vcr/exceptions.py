"""VCR exceptions."""

from __future__ import annotations


class MissingCassette(RuntimeError):
    """Raised in replay mode when a request has no recorded cassette (surfaces the accident)."""


class BudgetExceeded(RuntimeError):
    """Raised when a live call would exceed the daily budget (config/registry.yaml)."""
