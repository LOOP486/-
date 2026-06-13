"""E2 子代理：上下文隔离、工具面受限、角色路由、只回摘要。

替身模型按调用顺序脚本化（主循环与子循环共用一个模型，按 acomplete 调用次序消费），
真实 ToolRegistry + RunWriter（tmp_path），不碰真编辑器/真 LLM。
"""

from typing import Any

from tests.test_loop import tool_turn
from ue5agent.agent.events import RunWriter, read_events
from ue5agent.agent.state import TaskSession
from ue5agent.agent.subagent import (
    SUBAGENT_TOOL_NAME,
    _readonly_face,
    register_spawn_subagent,
)
from ue5agent.core.loop import AgentLoop
from ue5agent.core.permissions import PermissionGate, PermissionLevel
from ue5agent.llm.types import AssistantTurn
from ue5agent.tools.registry import ToolRegistry, ToolSpec


class RecordingModel:
    """记录每次 acomplete 的 (role, messages 快照)，并按脚本依次吐回合。"""

    def __init__(self, script: list[AssistantTurn]):
        self._script = list(script)
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        self.calls.append((role, [dict(m) for m in messages]))
        return self._script.pop(0)


def _read_tool(name: str, result: str = "只读结果") -> ToolSpec:
    async def handler(**kwargs: Any) -> str:
        return result

    return ToolSpec(
        name=name,
        description="只读探查",
        parameters={"type": "object", "properties": {"blueprint_path": {"type": "string"}}},
        level=PermissionLevel.READ,
        handler=handler,
    )


def _write_tool(name: str) -> ToolSpec:
    async def handler(**kwargs: Any) -> str:
        return "写入完成"

    return ToolSpec(
        name=name,
        description="写操作",
        parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
        level=PermissionLevel.WRITE_PROJECT,
        handler=handler,
    )


def _writer(tmp_path) -> RunWriter:
    return RunWriter(tmp_path, TaskSession.new("主任务"))


# ---------- 工具面构造（_readonly_face）----------


def test_readonly_face_excludes_write_and_self(tmp_path):
    """子代理工具面：只放只读工具，剔除写工具与 spawn_subagent 自身（防递归）。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read"))
    registry.register(_write_tool("ue_whitebox__wb_build"))
    register_spawn_subagent(registry, llm=RecordingModel([]), writer=_writer(tmp_path))

    face = _readonly_face(registry, None)
    assert face is not None
    names = face.names()
    assert "ue_editor__bp_read" in names
    assert "ue_whitebox__wb_build" not in names
    assert SUBAGENT_TOOL_NAME not in names


def test_readonly_face_respects_allowlist_bare_names(tmp_path):
    """调用方白名单进一步收窄，支持裸名匹配（planner 写 bp_read，注册名带前缀）。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read"))
    registry.register(_read_tool("ue_editor__editor_actors"))

    face = _readonly_face(registry, ["bp_read"])
    assert face is not None
    assert face.names() == ["ue_editor__bp_read"]


def test_readonly_face_none_when_no_readonly_tools(tmp_path):
    """没有任何只读工具时返回 None（调用方据此给可读错误，而非放出全拒空面）。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_write_tool("ue_whitebox__wb_build"))
    assert _readonly_face(registry, None) is None


# ---------- spawn_subagent 行为 ----------


async def test_subagent_returns_summary_routes_role_and_isolates(tmp_path):
    """子代理用独立 system + 角色路由跑完，只把摘要带回，全文落 artifact。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read", result="BP_A: 角色蓝图"))
    writer = _writer(tmp_path)
    model = RecordingModel(
        [
            tool_turn("ue_editor__bp_read", '{"blueprint_path": "/Game/BP_A"}'),
            AssistantTurn(content="摘要：BP_A 是第三人称角色蓝图"),
        ]
    )
    register_spawn_subagent(registry, llm=model, writer=writer)

    outcome = await registry.run(
        SUBAGENT_TOOL_NAME, '{"task": "读 /Game/BP_A 并总结", "role": "explorer"}'
    )
    assert outcome.ok
    assert outcome.text.startswith("[subagent:explorer]")
    assert "BP_A 是第三人称角色蓝图" in outcome.text

    # 角色路由：子代理两次 acomplete 都走 explorer
    assert [role for role, _ in model.calls] == ["explorer", "explorer"]
    # 上下文隔离：子代理首条是自己的只读 system，且看不到主任务措辞
    sub_system = model.calls[0][1][0]
    assert sub_system["role"] == "system"
    assert "子代理" in sub_system["content"]
    assert "主任务" not in sub_system["content"]

    # 摘要全文落 artifact；trace 有起止事件
    arts = [a for a in writer.session.artifacts if a.kind == "subagent_summary"]
    assert len(arts) == 1
    events = {e["event"] for e in read_events(writer.trace_path)}
    assert {"subagent_start", "subagent_end"} <= events


async def test_subagent_no_readonly_tools_returns_error(tmp_path):
    """无可用只读工具：返回错误文本（pipeline 标 ok=False），不静默放行。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_write_tool("ue_whitebox__wb_build"))
    register_spawn_subagent(registry, llm=RecordingModel([]), writer=_writer(tmp_path))

    outcome = await registry.run(SUBAGENT_TOOL_NAME, '{"task": "做点啥"}')
    assert not outcome.ok
    assert "无可用只读工具" in outcome.text


async def test_subagent_exception_returns_error_text(tmp_path):
    """子代理执行异常（如 LLM 故障）转错误文本回主循环，不炸主任务。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read"))

    class BoomModel:
        async def acomplete(self, role, messages, tools=None):
            raise RuntimeError("LLM 503")

    register_spawn_subagent(registry, llm=BoomModel(), writer=_writer(tmp_path))
    outcome = await registry.run(SUBAGENT_TOOL_NAME, '{"task": "x"}')
    assert not outcome.ok
    assert "子代理" in outcome.text and "LLM 503" in outcome.text


async def test_subagent_budget_exhausted_returns_error(tmp_path):
    """子代理预算耗尽未产摘要：错误文本提示拆小，不上抛。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read"))
    model = RecordingModel(
        [tool_turn("ue_editor__bp_read", "{}"), tool_turn("ue_editor__bp_read", "{}")]
    )
    register_spawn_subagent(registry, llm=model, writer=_writer(tmp_path), max_iterations=2)

    outcome = await registry.run(SUBAGENT_TOOL_NAME, '{"task": "无尽探查"}')
    assert not outcome.ok
    assert "预算耗尽" in outcome.text


async def test_subagent_empty_summary_returns_error(tmp_path):
    """子代理产出空摘要：判错误（摘要是唯一产物，空摘要等于没干活）。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read"))
    register_spawn_subagent(
        registry, llm=RecordingModel([AssistantTurn(content="   ")]), writer=_writer(tmp_path)
    )
    outcome = await registry.run(SUBAGENT_TOOL_NAME, '{"task": "x"}')
    assert not outcome.ok
    assert "未产出摘要" in outcome.text


async def test_subagent_empty_task_returns_error(tmp_path):
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read"))
    register_spawn_subagent(registry, llm=RecordingModel([]), writer=_writer(tmp_path))
    outcome = await registry.run(SUBAGENT_TOOL_NAME, '{"task": "   "}')
    assert not outcome.ok
    assert "为空" in outcome.text


# ---------- 与主循环集成 ----------


async def test_main_loop_delegates_and_only_sees_summary(tmp_path):
    """主循环 spawn 子代理读蓝图后只拿到摘要——子代理的工具原始输出不进主上下文。"""
    registry = ToolRegistry(PermissionGate())
    registry.register(_read_tool("ue_editor__bp_read", result="原始大段蓝图JSON"))
    writer = _writer(tmp_path)
    model = RecordingModel(
        [
            # 主循环：委派子代理
            tool_turn(SUBAGENT_TOOL_NAME, '{"task": "读 BP_A 和 BP_B 总结", "role": "explorer"}'),
            # 子代理：读工具 → 产摘要
            tool_turn("ue_editor__bp_read", '{"blueprint_path": "/Game/BP_A"}'),
            AssistantTurn(content="摘要：两个都是角色蓝图"),
            # 主循环：汇总收尾
            AssistantTurn(content="已汇总：两个角色蓝图"),
        ]
    )
    register_spawn_subagent(registry, llm=model, writer=writer)

    loop = AgentLoop(model, registry, session_log=writer)
    result = await loop.run("调研两个蓝图", role="planner")

    assert "已汇总" in result.final_text
    # 角色顺序印证隔离：主 planner → 子 explorer×2 → 主 planner
    assert [role for role, _ in model.calls] == ["planner", "explorer", "explorer", "planner"]
    # 主循环收尾轮看到的是摘要而非子代理工具的原始输出
    final_view = model.calls[-1][1]
    tool_msgs = [m for m in final_view if m.get("role") == "tool"]
    assert any("摘要：两个都是角色蓝图" in str(m["content"]) for m in tool_msgs)
    assert all("原始大段蓝图JSON" not in str(m["content"]) for m in tool_msgs)
