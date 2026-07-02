"""C7 — エージェント実行系（ツール使用ループ）。

エージェントが世界状態を知る唯一の窓はツール経由（PROJECT.md §5.2-5）。
シム真値への直接アクセスはこのモジュールには存在しない。LLMはプロバイダIF
（openai/cache/stub）経由でのみ呼ぶ。トークン・ツール呼出数を常時記録（H7）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import Field

from orx.common.providers import LLMClient, LLMRequest, LLMResponse
from orx.common.schemas import StrictModel

ToolFn = Callable[[dict[str, Any]], str]

_TOOL_RESULT_LIMIT = 4000  # ツール結果の最大文字数（超過は切り詰めて明示）


class ToolSpec(StrictModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class AgentRunResult(StrictModel):
    answer: str
    turns: int
    tool_call_count: int
    prompt_tokens: int
    completion_tokens: int
    transcript: list[dict[str, Any]] = Field(default_factory=list)


def parse_answer(content: str | None) -> str:
    """最終行の `ANSWER: ...` を抽出する（無ければ全文を返す）。"""
    if not content:
        return ""
    for line in reversed(content.strip().splitlines()):
        stripped = line.strip()
        if stripped.upper().startswith("ANSWER:"):
            return stripped[len("ANSWER:") :].strip()
    return content.strip()


class ToolAgent:
    """ツール使用ループ。LLMの応答が最終回答になるまでツールを実行する。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: list[tuple[ToolSpec, ToolFn]],
        system_prompt: str,
        max_turns: int = 6,
    ) -> None:
        self.llm = llm
        self.specs = [spec.as_openai() for spec, _ in tools]
        self.handlers: dict[str, ToolFn] = {spec.name: fn for spec, fn in tools}
        self.system_prompt = system_prompt
        self.max_turns = max_turns

    def _execute(self, name: str, arguments: str) -> str:
        handler = self.handlers.get(name)
        if handler is None:
            return f"error: unknown tool {name!r}"
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return f"error: invalid JSON arguments: {exc}"
        try:
            result = handler(args)
        except Exception as exc:  # ツール例外は文字列でエージェントに返す
            return f"error: {type(exc).__name__}: {exc}"
        if len(result) > _TOOL_RESULT_LIMIT:
            return result[:_TOOL_RESULT_LIMIT] + "\n...[truncated]"
        return result

    def run(self, question: str) -> AgentRunResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": question},
        ]
        prompt_tokens = completion_tokens = tool_calls_total = 0
        response: LLMResponse | None = None
        for turn in range(1, self.max_turns + 1):
            request = LLMRequest(messages=messages, tools=self.specs if self.specs else None)
            response = self.llm.complete(request)
            prompt_tokens += response.prompt_tokens
            completion_tokens += response.completion_tokens
            if not response.tool_calls:
                return AgentRunResult(
                    answer=parse_answer(response.content),
                    turns=turn,
                    tool_call_count=tool_calls_total,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    transcript=messages[1:],
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                }
            )
            for call in response.tool_calls:
                function = call.get("function", {})
                result = self._execute(function.get("name", ""), function.get("arguments", ""))
                tool_calls_total += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", f"call-{tool_calls_total}"),
                        "content": result,
                    }
                )
        return AgentRunResult(
            answer=parse_answer(response.content if response else None),
            turns=self.max_turns,
            tool_call_count=tool_calls_total,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            transcript=messages[1:],
        )
