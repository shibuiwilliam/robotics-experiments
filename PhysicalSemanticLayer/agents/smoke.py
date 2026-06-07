"""Agent smoke test — minimal SDK call to verify connectivity.

Usage: python -m agents.smoke
WARNING: This calls the Anthropic API and costs money.
"""

from __future__ import annotations

import asyncio
import os


async def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set. Skipping agent smoke test.")
        return

    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    print("=== Agent Smoke Test ===")
    print("Sending a single query to Claude...")

    async for message in query(
        prompt="Reply with exactly: AGENT_SMOKE_OK",
        options=ClaudeAgentOptions(
            allowed_tools=[],
            max_turns=1,
            max_budget_usd=0.05,
        ),
    ):
        if isinstance(message, ResultMessage):
            print(f"  Result: {message.result}")
            print(f"  Cost: ${message.total_cost_usd or 0:.4f}")
            if message.result and "AGENT_SMOKE_OK" in message.result:
                print("=== PASS ===")
            else:
                print("=== UNEXPECTED RESPONSE ===")

    print("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
