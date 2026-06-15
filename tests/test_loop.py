"""agent 主循环：用假模型替身验证工具调度、错误回传与迭代预算。"""

from typing import Any

import pytest

from ue5agent.agent.events import RunWriter, read_events
from ue5agent.agent.state import TaskSession
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


async def test_dispatch_exception_still_answers_tool_call():
    """历史卫生回归：调度层异常不得让 tool_call 缺回包（否则 history 永久污染，
    后续每次请求被 API 以 insufficient tool messages 拒绝——e2e 实测教训）。"""

    class ExplodingRegistry(ToolRegistry):
        async def run(self, name: str, arguments_json: str):
            raise RuntimeError("调度层炸了")

    model = FakeModel([tool_turn("echo", '{"text": "x"}'), AssistantTurn(content="收到")])
    history: list[dict[str, Any]] = []
    loop = AgentLoop(model, ExplodingRegistry(PermissionGate()))
    result = await loop.run("测试", history=history)
    assert result.final_text == "收到"
    # 异常转为错误文本回包，assistant 的 tool_call 有对应 tool 消息
    tool_msgs = [m for m in history if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "[error] 工具调度异常" in tool_msgs[0]["content"]
    assert tool_msgs[0]["tool_call_id"] == "call_1"


async def test_whitebox_layout_json_saved_as_artifact(tmp_path):
    """白盒 DSL 不能只留在截断 trace 里；每次 wb_build 调用都要落完整文件。"""
    registry = ToolRegistry(PermissionGate())

    async def wb_build(layout_json: str, prefix: str = "WB") -> str:
        return f"built {prefix}"

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_build",
            description="搭建白盒",
            parameters={
                "type": "object",
                "properties": {
                    "layout_json": {"type": "string"},
                    "prefix": {"type": "string"},
                },
                "required": ["layout_json"],
            },
            level=PermissionLevel.READ,
            handler=wb_build,
        )
    )
    layout = {
        "name": "dsl_archive",
        "origin": [20000, -10000, 0],
        "rooms": [{"name": "Hall", "rect": [0, 0, 6, 6]}],
    }
    arguments = (
        '{"layout_json": "{\\"name\\": \\"dsl_archive\\", '
        '\\"origin\\": [20000, -10000, 0], '
        '\\"rooms\\": [{\\"name\\": \\"Hall\\", \\"rect\\": [0, 0, 6, 6]}]}", '
        '"prefix": "SPC1"}'
    )
    model = FakeModel(
        [tool_turn("ue_whitebox__wb_build", arguments), AssistantTurn(content="完成")]
    )
    writer = RunWriter(tmp_path, TaskSession.new("dsl"))

    await AgentLoop(model, registry, session_log=writer).run("测试")

    artifacts = list((writer.artifacts_dir / "layouts").glob("*.json"))
    assert len(artifacts) == 1
    assert artifacts[0].read_text(encoding="utf-8").startswith('{\n  "name": "dsl_archive"')
    assert writer.session.artifacts[0].kind == "whitebox_layout"
    assert writer.session.artifacts[0].meta["prefix"] == "SPC1"
    assert layout["rooms"][0]["name"] in artifacts[0].read_text(encoding="utf-8")
    tool_events = [e for e in read_events(writer.trace_path) if e["event"] == "tool_call"]
    assert tool_events[0]["layout_artifact"] == writer.session.artifacts[0].path


async def test_wall_clock_budget_stops_loop():
    """墙钟预算为 0：第一轮前即判定耗尽，防止单个挂死工具拖垮整局。"""
    model = FakeModel([AssistantTurn(content="不该到这")])
    loop = AgentLoop(model, make_registry(), max_wall_seconds=0)
    with pytest.raises(BudgetExhausted, match="墙钟预算"):
        await loop.run("测试")
