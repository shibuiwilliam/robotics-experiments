"""C7 ツール使用ループのテスト（スタブ/フェイクLLMで完全オフライン）。"""

import json

from orx.agent.toolloop import ToolAgent, ToolSpec, parse_answer
from orx.common.providers import LLMRequest, LLMResponse, StubLLMClient


class FakeToolCallingLLM:
    """1回ツールを呼び、その結果を踏まえて回答するフェイク。"""

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"key": "b1"}'},
                    }
                ],
                prompt_tokens=100,
                completion_tokens=10,
            )
        tool_msg = next(m for m in request.messages if m.get("role") == "tool")
        try:
            zone = json.loads(tool_msg["content"])["zone"]
        except (json.JSONDecodeError, KeyError):
            zone = "tool-error"
        return LLMResponse(
            content=f"確認しました。\nANSWER: {zone}",
            prompt_tokens=150,
            completion_tokens=20,
        )


def lookup_tool() -> tuple[ToolSpec, object]:
    spec = ToolSpec(
        name="lookup",
        description="箱のゾーンを引く",
        parameters={"type": "object", "properties": {"key": {"type": "string"}}},
    )

    def run(args: dict) -> str:
        return json.dumps({"zone": "shelf_a" if args["key"] == "b1" else "unknown"})

    return spec, run


def test_tool_loop_executes_and_parses() -> None:
    llm = FakeToolCallingLLM()
    agent = ToolAgent(llm, [lookup_tool()], "system")
    result = agent.run("b1はどこ?")
    assert result.answer == "shelf_a"
    assert result.tool_call_count == 1
    assert result.turns == 2
    assert result.prompt_tokens == 250  # H7: トークンが集計される
    # ツール結果がLLMに渡っている
    assert any(m.get("role") == "tool" for m in llm.calls[1].messages)


def test_stub_llm_terminates_in_one_turn() -> None:
    agent = ToolAgent(StubLLMClient("m"), [lookup_tool()], "system")
    result = agent.run("質問")
    assert result.turns == 1
    assert result.answer.startswith("stub-response:")


def test_unknown_tool_returns_error_string() -> None:
    llm = FakeToolCallingLLM()
    agent = ToolAgent(llm, [], "system")  # ツール未登録
    result = agent.run("q")
    assert result.tool_call_count == 1  # 呼出は記録され、エラー文字列が返る


def test_parse_answer() -> None:
    assert parse_answer("foo\nANSWER: shelf_a") == "shelf_a"
    assert parse_answer("answer: X\nANSWER: y,z") == "y,z"
    assert parse_answer("自由回答テキスト") == "自由回答テキスト"
    assert parse_answer(None) == ""
