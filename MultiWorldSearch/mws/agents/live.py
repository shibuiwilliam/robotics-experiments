"""Live Gemini agent — uses Gemini ADK with MWS search as a registered tool.

Only instantiated when MWS_CLOUD_MODE=live. Uses google.adk.Agent with
gemini-3.5-flash as the default model. The MWS search function is registered
as a tool so the LLM can call it autonomously during reasoning.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mws.core.logging import get_logger

logger = get_logger(__name__)

GEMINI_LLM_MODEL = "gemini-3.5-flash"


def _extract_usage(events: list[Any]) -> dict[str, int] | None:
    """Sum real token usage from ADK events' usage_metadata (M13).

    Field names verified against the installed google-adk Event model
    (`usage_metadata` with genai's prompt_token_count / candidates_token_count).
    Returns None when no event carries usage — callers then fall back to the
    chars/4 estimate and mark the tokens as estimated.
    """
    input_tokens = 0
    output_tokens = 0
    found = False
    for event in events:
        usage = getattr(event, "usage_metadata", None)
        if usage is None:
            continue
        prompt = getattr(usage, "prompt_token_count", None)
        candidates = getattr(usage, "candidates_token_count", None)
        if prompt is None and candidates is None:
            continue
        found = True
        input_tokens += int(prompt or 0)
        output_tokens += int(candidates or 0)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens} if found else None


def _extract_text(events: list[Any]) -> str:
    """Last text part from ADK events (the agent's final answer), or ''."""
    for event in reversed(events):
        if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    return part.text.strip()
    return ""


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously, creating event loop if needed."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Nested async — use nest_asyncio or create new loop in thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


class ADKOpsAgent:
    """Live operations agent backed by Gemini ADK.

    Uses google.adk.Agent with MWS search registered as a function tool.
    The LLM can autonomously decide when to call the search tool during
    its reasoning loop, rather than receiving pre-fetched context.
    """

    DEFAULT_INSTRUCTION = (
        "You are an operations agent for equipment maintenance at a manufacturing facility. "
        "You have access to the MWS (Multi-World Search) memory system. "
        "When given a maintenance task, you should:\n"
        "1. Search for relevant SOPs, maintenance history, and parts inventory\n"
        "2. Assess the situation based on retrieved context\n"
        "3. Generate a step-by-step maintenance plan\n"
        "4. Dispatch the appropriate robot for execution\n"
        "Always cite the sources of your decisions."
    )

    def __init__(
        self,
        api_key: str,
        seed: int = 0,
        model: str = GEMINI_LLM_MODEL,
        search_fn: Any = None,
        name: str = "ops_agent",
        instruction: str | None = None,
        call_recorder: Any = None,
        replay_source: Any = None,
    ) -> None:
        import os

        from google.adk import Agent

        os.environ.setdefault("GOOGLE_API_KEY", api_key)

        self._seed = seed
        self._model = model
        self._name = name
        #: Token usage of the most recent real call (IMPROVEMENT M13).
        #: {"input_tokens": int, "output_tokens": int} or None if the API
        #: response carried no usage_metadata.
        self.last_usage: dict[str, int] | None = None
        #: Optional LLMCallRecorder (IMPROVEMENT M16) — every real call's
        #: prompt/response/tokens/latency is appended to the run's
        #: llm_calls.jsonl so live variance is explainable after the fact.
        self._call_recorder = call_recorder
        #: Optional LLMReplaySource (REPLAY) — when set, calls return the
        #: recorded responses deterministically: NO cloud round-trip, NO
        #: "ADK call" marker, NO re-recording (a replayed run must never look
        #: like a measured run to the reconciliation audit).
        self._replay = replay_source
        self.is_replay = replay_source is not None

        tools: list[Any] = []
        if search_fn is not None:
            tools.append(search_fn)

        self._agent = Agent(
            name=name,
            model=model,
            instruction=instruction or self.DEFAULT_INSTRUCTION,
            tools=tools,
        )

        logger.info("ADK agent initialized", name=name, model=model, n_tools=len(tools))

    def _run_agent(self, message: str, purpose: str = "call") -> tuple[list[Any], dict | None]:
        """Run the ADK agent; returns (events, usage).

        Usage is RETURNED (not only stored on ``last_usage``) so concurrent
        step execution attributes tokens to the right call — the shared
        attribute is kept for backward compatibility but is racy under
        parallelism and must not be read by parallel callers.
        """
        import time

        if self._replay is not None:
            # Deterministic replay: fabricate one minimal event carrying the
            # recorded response text so ALL downstream parsing (plan JSON
            # scan, _extract_text) behaves exactly as in the original run.
            from types import SimpleNamespace

            record = self._replay.next(agent=self._name, purpose=purpose)
            usage = (
                {
                    "input_tokens": int(record.get("input_tokens", 0)),
                    "output_tokens": int(record.get("output_tokens", 0)),
                }
                if record.get("tokens_measured")
                else None
            )
            self.last_usage = usage
            logger.debug("LLM call replayed", agent=self._name, purpose=purpose)
            part = SimpleNamespace(text=record.get("response", ""))
            events = [SimpleNamespace(content=SimpleNamespace(parts=[part]), usage_metadata=None)]
            return events, usage

        from google.adk.runners import InMemoryRunner

        runner = InMemoryRunner(agent=self._agent, app_name=f"mws_{self._name}")

        # Explicit reconciliation marker: exactly one line per REAL ADK call,
        # so the log<->metrics audit (mws/eval/reconcile.py) can count actual
        # cloud LLM calls independently of the cost tracker.
        logger.debug("ADK call", agent=self._name, model=self._model)

        # run_debug is an async method that returns list[Event]
        start = time.perf_counter()
        coro = runner.run_debug(message, quiet=True)
        events = _run_async(coro)
        latency_ms = (time.perf_counter() - start) * 1000
        usage = _extract_usage(events)
        self.last_usage = usage

        if self._call_recorder is not None:
            self._call_recorder.record(
                agent=self._name,
                model=self._model,
                purpose=purpose,
                prompt=message,
                response=_extract_text(events),
                input_tokens=int((usage or {}).get("input_tokens", 0)),
                output_tokens=int((usage or {}).get("output_tokens", 0)),
                tokens_measured=usage is not None,
                latency_ms=latency_ms,
            )
        return events, usage

    def plan(self, context: dict[str, Any]) -> list[str]:
        """Generate a maintenance plan using ADK agent with tool-use loop."""
        retrieval_results = context.get("retrieval_results", [])
        context_text = "\n".join(
            f"- [{r.get('modality', '?')}] {r.get('text', r.get('text_summary', ''))}"
            for r in retrieval_results[:10]
        )

        events, _usage = self._run_agent(
            f"Based on this context, generate a maintenance plan. "
            f"Return ONLY a JSON list of step name strings.\n\nContext:\n{context_text}",
            purpose="plan",
        )

        import json

        for event in reversed(events):
            if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        text = part.text.strip()
                        if text.startswith("```"):
                            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                        try:
                            steps = json.loads(text)
                            if isinstance(steps, list) and all(isinstance(s, str) for s in steps):
                                logger.info("ADK agent plan parsed", n_steps=len(steps))
                                return steps
                        except (json.JSONDecodeError, IndexError):
                            pass

        logger.warning("Failed to parse ADK agent plan, using default steps")
        return [
            "assess_situation",
            "retrieve_sop",
            "check_inventory",
            "dispatch_robot",
            "execute_repair",
            "verify_fix",
            "close_work_order",
        ]

    def execute_step(self, step: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute a plan step using ADK agent."""
        retrieval_results = context.get("retrieval_results", [])
        context_text = "\n".join(
            f"- {r.get('text', r.get('text_summary', ''))}" for r in retrieval_results[:5]
        )

        events, usage = self._run_agent(
            f"Execute step: '{step}'.\nContext:\n{context_text}\n"
            f"Describe the action and result in 1-2 sentences.",
            purpose=f"step:{step}",
        )

        result_text = _extract_text(events) or f"Step {step} executed."

        logger.info("ADK agent step executed", step=step)
        # ``usage`` rides along in the result so CONCURRENT step execution
        # (Backlog B) attributes measured tokens to the right call — callers
        # must read result["usage"], never the racy shared last_usage.
        return {
            "action": step,
            "result": result_text,
            "status": "complete",
            "model": self._model,
            "usage": usage,
        }
