"""agent 主循环：用假模型替身验证工具调度、错误回传与迭代预算。"""

from typing import Any

import pytest

from ue5agent.core.loop import AgentLoop, BudgetExhausted
from ue5agent.core.permissions import PermissionGate, PermissionLevel
from ue5agent.llm.types import AssistantTurn, ToolCall
from ue5agent.tools.registry import ToolRegistry, ToolSpec


class FakeModel:
    """按脚本依次吐出预设回合，并记录收到的消息历史。"""

    def __init__(self, script: list[AssistantTurn]):
        self._script = list(script)
        self.seen_messages: list[list[dict[str, Any]]] = []

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        self.seen_messages.append([dict(m) for m in messages])
        return self._script.pop(0)


def make_registry() -> ToolRegistry:
    registry = ToolRegistry(PermissionGate())

    async def echo(text: str) -> str:
        return f"echo: {text}"

    registry.register(
        ToolSpec(
            name="echo",
            description="原样返回输入",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            level=PermissionLevel.READ,
            handler=echo,
        )
    )
    return registry


def tool_turn(name: str, arguments: str) -> AssistantTurn:
    return AssistantTurn(content=None, tool_calls=[ToolCall("call_1", name, arguments)])


async def test_tool_call_then_final_answer():
    model = FakeModel([tool_turn("echo", '{"text": "hi"}'), AssistantTurn(content="完成")])
    loop = AgentLoop(model, make_registry())
    result = await loop.run("测试")
    assert result.final_text == "完成"
    assert result.turns == 2
    assert result.tool_call_count == 1
    # 第二轮模型应能看到工具结果
    last_message = model.seen_messages[1][-1]
    assert last_message["role"] == "tool"
    assert last_message["content"] == "echo: hi"


async def test_unknown_tool_reported_back_to_model():
    model = FakeModel([tool_turn("missing", "{}"), AssistantTurn(content="ok")])
    loop = AgentLoop(model, make_registry())
    await loop.run("测试")
    assert "[error]" in model.seen_messages[1][-1]["content"]


async def test_bad_arguments_reported_back_to_model():
    model = FakeModel([tool_turn("echo", "{not json"), AssistantTurn(content="ok")])
    loop = AgentLoop(model, make_registry())
    await loop.run("测试")
    assert "[error]" in model.seen_messages[1][-1]["content"]


async def test_budget_exhausted():
    model = FakeModel([tool_turn("echo", '{"text": "x"}') for _ in range(3)])
    loop = AgentLoop(model, make_registry(), max_iterations=3)
    with pytest.raises(BudgetExhausted):
        await loop.run("测试")
