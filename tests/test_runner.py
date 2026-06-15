"""K4：TaskRunner 状态机各转移路径（FakeModel 按调用顺序脚本化）。"""

import asyncio
import json

from tests.test_loop import FakeModel, make_registry, tool_turn
from ue5agent.agent.events import RunWriter, read_events
from ue5agent.agent.planner import make_plan
from ue5agent.agent.report import build_report
from ue5agent.agent.runner import TaskRunner, _whitebox_recovery_hint
from ue5agent.agent.state import PlanStep, TaskSession
from ue5agent.agent.verifier import (
    deterministic_verdict,
    evaluate_required_evidence,
    evaluate_success_checks,
)
from ue5agent.agent.vision_review import parse_review
from ue5agent.core.errors import ErrorCategory, mark_env_unready, mark_error
from ue5agent.core.permissions import PermissionGate, PermissionLevel
from ue5agent.llm.client import LLMUnavailable
from ue5agent.llm.types import AssistantTurn
from ue5agent.tools.registry import ScopedRegistry, ToolRegistry, ToolSpec


def _spec(name: str, level: PermissionLevel, handler) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        level=level,
        handler=handler,
    )


def plan(task_class: str, *intents_acceptance: tuple[str, str]) -> AssistantTurn:
    steps = [{"intent": i, "acceptance": a} for i, a in intents_acceptance]
    return AssistantTurn(content=json.dumps({"task_class": task_class, "steps": steps}))


def judge(verdict: str, reason: str = "r") -> AssistantTurn:
    return AssistantTurn(content=json.dumps({"verdict": verdict, "reason": reason}))


def make_runner(tmp_path, script) -> tuple[TaskRunner, FakeModel, RunWriter]:
    model = FakeModel(script)
    writer = RunWriter(tmp_path, TaskSession.new("测试任务"))
    return TaskRunner(model, make_registry(), writer), model, writer


class RaisingFakeModel(FakeModel):
    """FakeModel 变体：脚本项为异常时直接抛出，用于覆盖 LLM 故障路径。"""

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        self.seen_messages.append([dict(m) for m in messages])
        action = self._script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class CancelledFakeModel(FakeModel):
    """模拟底层 LLM 客户端用 CancelledError 表示请求被取消/超时。"""

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        self.seen_messages.append([dict(m) for m in messages])
        action = self._script.pop(0)
        if action == "cancel":
            raise asyncio.CancelledError()
        return action


class BaseExceptionFakeModel(FakeModel):
    """模拟底层 LLM 客户端抛出 SystemExit 这类 BaseException。"""

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        self.seen_messages.append([dict(m) for m in messages])
        action = self._script.pop(0)
        if action == "system_exit":
            raise SystemExit(1)
        return action


class SlowCoderFakeModel(FakeModel):
    """模拟 coder 请求挂起超过 TaskRunner 步内墙钟预算。"""

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        if role == "coder":
            await asyncio.sleep(0.2)
        return await super().acomplete(role, messages, tools=tools)


class SlowPlannerFakeModel(FakeModel):
    """模拟 planner 请求挂起超过 TaskRunner 步内墙钟预算。"""

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        if role == "planner":
            await asyncio.sleep(0.2)
        return await super().acomplete(role, messages, tools=tools)


class StrictToolHistoryFakeModel(FakeModel):
    """模拟 OpenAI 兼容 API 对 tool_call/tool 消息配对的校验。"""

    async def acomplete(self, role, messages, tools=None) -> AssistantTurn:
        self._assert_tool_history_is_paired(messages)
        return await super().acomplete(role, messages, tools=tools)

    def _assert_tool_history_is_paired(self, messages) -> None:
        pending: list[str] = []
        for message in messages:
            if message.get("role") == "assistant":
                pending.extend(call["id"] for call in message.get("tool_calls") or [])
            elif message.get("role") == "tool":
                tool_call_id = message.get("tool_call_id")
                if tool_call_id in pending:
                    pending.remove(tool_call_id)
        if pending:
            raise AssertionError(f"unanswered tool_calls in history: {pending}")


async def test_trivial_fast_path(tmp_path):
    runner, _, writer = make_runner(
        tmp_path,
        [
            plan("trivial", ("直接回答", "")),  # 无验收标准 → 自动通过，judge 不被调用
            AssistantTurn(content="答案是 42"),
        ],
    )
    outcome = await runner.run("1+41 等于几")
    assert outcome.success
    assert writer.session.task_class == "trivial"
    assert writer.session.plan[0].status == "done"
    assert "42" in outcome.report or "答案" in outcome.report
    events = [e["event"] for e in read_events(writer.trace_path)]
    assert "verify_result" in events
    assert events[-1] == "run_end"


async def test_two_steps_with_judge(tmp_path):
    runner, _, writer = make_runner(
        tmp_path,
        [
            plan("standard", ("做 A", "A 完成"), ("做 B", "B 完成")),
            AssistantTurn(content="A 做完"),
            judge("pass"),
            AssistantTurn(content="B 做完"),
            judge("pass"),
        ],
    )
    outcome = await runner.run("做 A 和 B")
    assert outcome.success
    assert [s.status for s in writer.session.plan] == ["done", "done"]
    assert writer.session.status == "done"


async def test_llm_request_start_event_records_request_shape(tmp_path):
    runner, _, writer = make_runner(
        tmp_path,
        [
            plan("standard", ("回答", "回答完成")),
            AssistantTurn(content="完成"),
            judge("pass"),
        ],
    )

    await runner.run("做点事")

    events = read_events(writer.trace_path)
    start = next(e for e in events if e["event"] == "llm_request_start")
    assert start["role"] == "coder"
    assert start["turn"] == 1
    assert start["message_count"] >= 2
    assert start["estimated_chars"] > 0
    assert start["tool_count"] >= 1
    assert events.index(start) < next(i for i, e in enumerate(events) if e["event"] == "llm_turn")


async def test_plan_cancelled_error_returns_failed_outcome(tmp_path):
    model = CancelledFakeModel(["cancel"])
    writer = RunWriter(tmp_path, TaskSession.new("计划取消"))
    runner = TaskRunner(model, make_registry(), writer)

    outcome = await runner.run("搭建空间")

    assert not outcome.success
    assert writer.session.status == "aborted"
    events = read_events(writer.trace_path)
    errors = [e for e in events if e["event"] == "run_error"]
    assert errors and errors[0]["phase"] == "plan"
    assert errors[0]["failure_type"] == "llm_timeout"


async def test_plan_wall_timeout_returns_failed_outcome(tmp_path):
    model = SlowPlannerFakeModel([plan("standard", ("执行", "完成"))])
    writer = RunWriter(tmp_path, TaskSession.new("计划超时"))
    runner = TaskRunner(model, make_registry(), writer, step_wall_seconds=0.05)

    outcome = await runner.run("搭建空间")

    assert not outcome.success
    assert writer.session.status == "aborted"
    events = read_events(writer.trace_path)
    errors = [e for e in events if e["event"] == "run_error"]
    assert errors and errors[0]["phase"] == "plan"
    assert errors[0]["failure_type"] == "llm_timeout"
    assert "计划阶段墙钟预算" in errors[0]["error"]


async def test_coder_llm_timeout_is_classified_and_retried_once(tmp_path):
    model = RaisingFakeModel(
        [
            plan("standard", ("生成白盒布局", "完成")),
            LLMUnavailable("角色 coder 的全部模型不可用：deepseek/deepseek-v4-pro（TimeoutError）"),
            AssistantTurn(content="重试完成"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("LLM 超时"))
    runner = TaskRunner(model, make_registry(), writer, max_step_attempts=3)

    outcome = await runner.run("搭建空间")

    assert outcome.success
    assert writer.session.plan[0].attempts == 2
    events = read_events(writer.trace_path)
    errors = [e for e in events if e["event"] == "run_error"]
    assert errors and errors[0]["failure_type"] == "llm_timeout"
    retries = [e for e in events if e["event"] == "recover_action"]
    assert retries and retries[0]["action"] == "retry"
    assert "llm_timeout" in retries[0]["reason"]


async def test_coder_cancelled_error_is_classified_and_retried_once(tmp_path):
    model = CancelledFakeModel(
        [
            plan("standard", ("生成白盒布局", "完成")),
            "cancel",
            AssistantTurn(content="重试完成"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("LLM 取消"))
    runner = TaskRunner(model, make_registry(), writer, max_step_attempts=3)

    outcome = await runner.run("搭建空间")

    assert outcome.success
    assert writer.session.plan[0].attempts == 2
    events = read_events(writer.trace_path)
    errors = [e for e in events if e["event"] == "run_error"]
    assert errors and errors[0]["failure_type"] == "llm_timeout"
    retries = [e for e in events if e["event"] == "recover_action"]
    assert retries and retries[0]["action"] == "retry"


async def test_coder_base_exception_is_recorded_without_process_exit(tmp_path):
    model = BaseExceptionFakeModel(
        [
            plan("standard", ("生成白盒布局", "完成")),
            "system_exit",
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("LLM base exception"))
    runner = TaskRunner(model, make_registry(), writer, max_step_attempts=3)

    outcome = await runner.run("搭建空间")

    assert not outcome.success
    assert writer.session.plan[0].attempts == 1
    events = read_events(writer.trace_path)
    errors = [e for e in events if e["event"] == "run_error"]
    assert errors and errors[0]["failure_type"] == "execution_error"
    assert "SystemExit" in errors[0]["error"]


async def test_step_wall_timeout_interrupts_slow_coder_request(tmp_path):
    model = SlowCoderFakeModel(
        [
            plan("standard", ("生成白盒布局", "完成")),
            AssistantTurn(content="慢请求返回"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("步内超时"))
    runner = TaskRunner(
        model,
        make_registry(),
        writer,
        max_step_attempts=1,
        step_wall_seconds=0.05,
    )

    outcome = await runner.run("执行一个不能卡住整套 eval 的任务")

    assert not outcome.success
    events = read_events(writer.trace_path)
    errors = [e for e in events if e["event"] == "run_error"]
    assert errors and errors[0]["failure_type"] == "llm_timeout"
    assert "步内墙钟预算" in errors[0]["error"]


async def test_step_timeout_retry_does_not_reuse_unanswered_tool_call_history(tmp_path):
    registry = ToolRegistry(PermissionGate())

    async def slow_tool() -> str:
        await asyncio.sleep(0.2)
        return "too late"

    registry.register(
        ToolSpec(
            name="slow_tool",
            description="慢工具",
            parameters={"type": "object", "properties": {}},
            level=PermissionLevel.READ,
            handler=slow_tool,
        )
    )
    model = StrictToolHistoryFakeModel(
        [
            plan_raw(
                "standard",
                [{"intent": "调用慢工具", "acceptance": "完成", "allowed_tools": ["slow_tool"]}],
            ),
            tool_turn("slow_tool", "{}"),
            AssistantTurn(content="重试完成"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("工具超时重试"))
    runner = TaskRunner(
        model,
        registry,
        writer,
        max_step_attempts=2,
        step_wall_seconds=0.05,
    )

    outcome = await runner.run("执行一个工具可能超时的任务")

    assert outcome.success
    assert writer.session.plan[0].attempts == 2
    retry_view = model.seen_messages[2]
    assert all(not message.get("tool_calls") for message in retry_view)
    events = read_events(writer.trace_path)
    retries = [e for e in events if e["event"] == "recover_action"]
    assert retries and retries[0]["action"] == "retry"


async def test_report_only_final_step_is_completed_without_extra_llm(tmp_path):
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "验证导航可达",
                        "acceptance": "path_test 返回 reachable true",
                        "allowed_tools": ["ue_editor__path_test"],
                        "success_checks": [
                            {"kind": "path_test", "field": "reachable", "equals": True}
                        ],
                    },
                    {
                        "intent": "简短报告验证结果",
                        "acceptance": "输出报告，确认结构校验通过且导航可达",
                        "allowed_tools": ["ue_editor__output_log_tail"],
                    },
                ],
            ),
            tool_turn("ue_editor__path_test", "{}"),
        ]
    )
    registry = make_registry()

    async def path_test() -> str:
        return '{"reachable": true}\n[facts] {"kind": "path_test", "reachable": true}'

    registry.register(
        ToolSpec(
            name="ue_editor__path_test",
            description="",
            parameters={"type": "object", "properties": {}},
            level=PermissionLevel.READ,
            handler=path_test,
        )
    )
    writer = RunWriter(tmp_path, TaskSession.new("报告跳过"))
    runner = TaskRunner(model, registry, writer)

    outcome = await runner.run("验证后简短报告")

    assert outcome.success
    assert len(model.seen_messages) == 2
    assert writer.session.plan[1].status == "done"
    assert "前序步骤已完成" in outcome.final_answer


async def test_whitebox_build_step_prompt_discourages_long_preamble(tmp_path):
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建白盒结构",
                        "acceptance": "完成落地",
                        "allowed_tools": ["ue_whitebox__wb_build"],
                    }
                ],
            ),
            AssistantTurn(content="完成"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("白盒提示"))
    runner = TaskRunner(model, make_registry(), writer)

    await runner.run("搭建一个默认 slab 白盒空间")

    prompt = model.seen_messages[1][-1]["content"]
    assert "不要在工具调用前展开完整设计说明" in prompt
    assert "优先一次性调用 wb_build" in prompt
    assert "不要重复粘贴完整 JSON" in prompt
    assert "先在脑中按整数格画 room.rect 邻接表" in prompt
    assert "共享墙门洞必须两侧成对且 at/width 对齐" in prompt
    assert "不确定某面墙是否是外墙就不要写 windows" in prompt


async def test_whitebox_validate_step_does_not_get_build_prompt(tmp_path):
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "用 wb_validate 校验整个白盒场景",
                        "acceptance": "校验通过",
                        "allowed_tools": ["ue_whitebox__wb_build", "ue_whitebox__wb_validate"],
                    }
                ],
            ),
            AssistantTurn(content="完成"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("白盒校验提示"))
    runner = TaskRunner(model, make_registry(), writer)

    await runner.run("校验一个默认 slab 白盒空间")

    prompt = model.seen_messages[1][-1]["content"]
    assert "优先一次性调用 wb_build" not in prompt
    assert "不要重复粘贴完整 JSON" not in prompt


async def test_wb_build_dispatch_repairs_internal_window_and_one_sided_door(tmp_path):
    captured_layouts: list[dict] = []
    registry = make_registry()

    async def wb_build(layout_json: str = "", prefix: str = "WB") -> str:
        captured_layouts.append(json.loads(layout_json))
        return 'built\n[facts] {"kind": "wb_build", "ok": true}'

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_build",
            description="",
            parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
            level=PermissionLevel.READ,
            handler=wb_build,
        )
    )
    bad_layout = {
        "name": "guarded",
        "rooms": [
            {
                "name": "a",
                "rect": [0, 0, 4, 4],
                "doors": [{"wall": "east", "at": 1, "width": 2}],
                "windows": [{"wall": "east", "at": 0, "width": 1}],
            },
            {"name": "b", "rect": [4, 0, 4, 4], "doors": []},
        ],
    }
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建白盒结构",
                        "acceptance": "wb_build 返回 ok",
                        "allowed_tools": ["ue_whitebox__wb_build"],
                        "success_checks": [{"kind": "wb_build", "field": "ok", "equals": True}],
                    }
                ],
            ),
            tool_turn(
                "ue_whitebox__wb_build",
                json.dumps({"layout_json": json.dumps(bad_layout)}, ensure_ascii=False),
            ),
            AssistantTurn(content="完成"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("白盒 guardrail"))
    runner = TaskRunner(model, registry, writer)

    outcome = await runner.run("搭建默认 slab 白盒空间")

    assert outcome.success
    assert captured_layouts
    repaired = captured_layouts[0]
    room_a, room_b = repaired["rooms"]
    assert room_a["windows"] == []
    assert {"wall": "west", "at": 1, "width": 2} in room_b["doors"]


async def test_fail_then_retry_then_pass(tmp_path):
    runner, model, writer = make_runner(
        tmp_path,
        [
            plan("standard", ("修 bug", "编译零错误")),
            AssistantTurn(content="改了代码"),
            judge("fail", "没有编译证据"),
            AssistantTurn(content="编译通过了"),
            judge("pass"),
        ],
    )
    outcome = await runner.run("修编译错误")
    assert outcome.success
    step = writer.session.plan[0]
    assert step.status == "done"
    assert step.attempts == 2
    # 重试时 judge 的理由必须进了执行方的上下文
    retry_view = model.seen_messages[3]
    assert any("没有编译证据" in str(m.get("content")) for m in retry_view)
    events = read_events(writer.trace_path)
    retries = [e for e in events if e["event"] == "recover_action"]
    assert retries[0]["action"] == "retry"


async def test_persistent_fail_aborts_and_skips_rest(tmp_path):
    script = [plan("standard", ("做 A", "A 完成"), ("做 B", "B 完成"))]
    for _ in range(3):  # max_step_attempts=3
        script += [AssistantTurn(content="尝试"), judge("fail", "不行")]
    runner, _, writer = make_runner(tmp_path, script)
    outcome = await runner.run("注定失败")
    assert not outcome.success
    assert writer.session.status == "aborted"
    assert writer.session.plan[0].status == "failed"
    assert writer.session.plan[0].attempts == 3
    assert writer.session.plan[1].status == "skipped"
    assert "失败" in outcome.report


async def test_garbage_plan_falls_back_to_single_step(tmp_path):
    runner, _, writer = make_runner(
        tmp_path,
        [
            AssistantTurn(content="我觉得应该先……（不是 JSON）"),
            AssistantTurn(content="做完了，证据齐全"),
            judge("pass"),
        ],
    )
    outcome = await runner.run("某个任务")
    assert outcome.success
    assert len(writer.session.plan) == 1
    assert writer.session.plan[0].intent == "某个任务"


async def test_total_wall_budget_aborts(tmp_path):
    """会话总预算为 0：不执行任何步骤直接放弃——3 小时跑不完类问题的总闸。"""
    model = FakeModel([plan("standard", ("做 A", "A 完成"), ("做 B", "B 完成"))])
    writer = RunWriter(tmp_path, TaskSession.new("预算测试"))
    runner = TaskRunner(model, make_registry(), writer, total_wall_seconds=0)
    outcome = await runner.run("预算耗尽场景")
    assert not outcome.success
    assert writer.session.plan[0].status == "failed"
    assert writer.session.plan[1].status == "skipped"
    events = [e["event"] for e in read_events(writer.trace_path)]
    assert "budget_warning" in events


async def test_step_exception_recorded_not_crash(tmp_path):
    """LLM 层异常计为步骤失败，不炸整个会话（仍产出报告）。"""

    class ExplodingModel:
        def __init__(self):
            self.calls = 0

        async def acomplete(self, role, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return plan("standard", ("做 A", "A 完成"))
            raise RuntimeError("API 永久故障")

    writer = RunWriter(tmp_path, TaskSession.new("异常测试"))
    runner = TaskRunner(ExplodingModel(), make_registry(), writer, max_step_attempts=1)
    outcome = await runner.run("异常场景")
    assert not outcome.success
    assert "失败" in outcome.report


async def test_step_execution_exception_aborts_without_evidence_retry(tmp_path):
    """执行阶段 LLM/底层异常是决定性失败，不应被当作缺证据反复重试。"""

    class ExplodingModel:
        def __init__(self):
            self.calls = 0

        async def acomplete(self, role, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return plan(
                    "standard",
                    ("调用 wb_build", "wb_build 成功"),
                    ("继续下一步", "不应执行"),
                )
            raise TimeoutError("模型请求超时")

    writer = RunWriter(tmp_path, TaskSession.new("异常不重试"))
    runner = TaskRunner(ExplodingModel(), make_registry(), writer, max_step_attempts=3)

    outcome = await runner.run("搭白盒")

    assert not outcome.success
    assert writer.session.plan[0].status == "failed"
    assert writer.session.plan[0].attempts == 1
    assert writer.session.plan[1].status == "skipped"
    events = read_events(writer.trace_path)
    verifies = [e for e in events if e["event"] == "verify_result"]
    assert verifies[-1]["mode"] == "execution"
    assert "TimeoutError" in verifies[-1]["reason"]


async def test_env_unready_aborts_without_retry(tmp_path):
    """环境未就绪（编辑器桥连接被拒）：验收失败后不重试不烧预算，直接终止并给指引。"""
    registry = ToolRegistry(PermissionGate())

    async def wb_build(layout_json: str = "") -> str:
        return mark_env_unready("落地失败：编辑器桥连接被拒")

    registry.register(
        ToolSpec(
            name="wb_build",
            description="白盒搭建",
            parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_build,
        )
    )
    model = FakeModel(
        [
            plan("standard", ("建主厅", "主厅就位"), ("建走廊", "走廊就位")),
            tool_turn("wb_build", '{"layout_json": "{}"}'),
            AssistantTurn(content="编辑器没开，搭不了"),
            judge("fail", "无构建证据"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("环境未就绪"))
    runner = TaskRunner(model, registry, writer)
    outcome = await runner.run("搭白盒")
    assert not outcome.success
    step = writer.session.plan[0]
    assert step.status == "failed"
    assert step.attempts == 1  # 关键：环境性失败不消耗剩余重试
    assert writer.session.plan[1].status == "skipped"
    assert "环境未就绪" in outcome.report
    events = read_events(writer.trace_path)
    aborts = [e for e in events if e["event"] == "recover_action"]
    assert aborts and "env_unready" in aborts[0]["reason"]


def _bridge_tool_registry(tool_text: str, *, editor_online: bool) -> ToolRegistry:
    """注册一个返回 bridge_down 错误的工具 + editor_status 探活工具（B3 恢复测试用）。"""
    registry = ToolRegistry(PermissionGate())

    async def wb_build(layout_json: str = "") -> str:
        return tool_text

    async def editor_status() -> str:
        return "online：桥可达" if editor_online else "offline：桥不可达"

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_build",
            description="",
            parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_build,
        )
    )
    registry.register(
        ToolSpec(
            name="ue_editor__editor_status",
            description="",
            parameters={"type": "object", "properties": {}},
            level=PermissionLevel.READ,
            handler=editor_status,
        )
    )
    return registry


async def test_bridge_down_offline_aborts_fast(tmp_path):
    """桥中途掉线 + 探活仍 offline：当作环境性失败快速终止，不空转耗尽重试（踩坑史第8条）。"""
    bridge_err = mark_error(ErrorCategory.BRIDGE_DOWN, "编辑器桥通信中断：WinError 10054")
    registry = _bridge_tool_registry(bridge_err, editor_online=False)
    model = FakeModel(
        [
            plan("standard", ("搭白盒", "落地成功"), ("再搭", "再成功")),
            tool_turn("ue_whitebox__wb_build", '{"layout_json": "{}"}'),
            AssistantTurn(content="桥断了"),
            judge("fail", "无构建证据"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("桥掉线"))
    runner = TaskRunner(model, registry, writer)
    outcome = await runner.run("搭白盒")
    assert not outcome.success
    step = writer.session.plan[0]
    assert step.status == "failed"
    assert step.attempts == 1  # 探活 offline → 快速终止，不消耗剩余重试
    assert writer.session.plan[1].status == "skipped"
    events = read_events(writer.trace_path)
    actions = [e for e in events if e["event"] == "recover_action"]
    assert any(e["action"] == "probe_bridge" and "offline" in e["reason"] for e in actions)
    assert any(e["action"] == "abort" and "bridge_down" in e["reason"] for e in actions)


async def test_bridge_down_online_retries_normally(tmp_path):
    """桥瞬断但探活恢复 online：走正常重试（不快速终止），第二次成功收口。"""
    bridge_err = mark_error(ErrorCategory.BRIDGE_DOWN, "编辑器桥通信中断：偶发超时")
    registry = _bridge_tool_registry(bridge_err, editor_online=True)
    model = FakeModel(
        [
            plan("standard", ("搭白盒", "落地成功")),
            tool_turn("ue_whitebox__wb_build", '{"layout_json": "{}"}'),
            AssistantTurn(content="第一次桥抖了一下"),
            judge("fail", "无构建证据"),
            AssistantTurn(content="重试这次成功了"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("桥瞬断"))
    runner = TaskRunner(model, registry, writer)
    outcome = await runner.run("搭白盒")
    assert outcome.success
    step = writer.session.plan[0]
    assert step.status == "done"
    assert step.attempts == 2  # 探活 online → 正常重试，第二次过
    events = read_events(writer.trace_path)
    actions = [e for e in events if e["event"] == "recover_action"]
    assert any(e["action"] == "probe_bridge" and "online" in e["reason"] for e in actions)
    assert any(e["action"] == "retry" for e in actions)


def test_report_clips_long_summary_with_marker():
    session = TaskSession.new("报告截断")
    session.goal = "测试"
    session.plan = [PlanStep(id="s1", intent="做事", status="failed", attempts=1)]
    report = build_report(session, {"s1": "x" * 2500})
    assert "已截断" in report
    assert "x" * 2000 in report  # 截断阈值放宽到 2000，不再 300 字符切碎 JSON


async def test_insufficient_evidence_retries(tmp_path):
    runner, _, writer = make_runner(
        tmp_path,
        [
            plan("standard", ("改配置", "配置生效")),
            AssistantTurn(content="改了"),
            judge("insufficient", "缺少验证"),
            AssistantTurn(content="验证过了"),
            judge("pass"),
        ],
    )
    outcome = await runner.run("改配置")
    assert outcome.success
    assert writer.session.plan[0].attempts == 2


# ---------- A3 证据信封：确定性验收规则 ----------


def test_deterministic_verdict_rules():
    assert deterministic_verdict([]) is None
    # 非决定性事实（操作成功 != 做对了）不单独支撑 pass
    assert deterministic_verdict([{"kind": "wb_build", "ok": True}]) is None
    fail = deterministic_verdict([{"kind": "compile", "ok": False, "errors": 3}])
    assert fail is not None and fail.verdict == "fail" and "compile" in fail.reason
    # 同类取最新：修复后的 compile 覆盖先前失败
    latest_wins = deterministic_verdict(
        [
            {"kind": "compile", "ok": False, "errors": 3},
            {"kind": "compile", "ok": True, "exit_code": 0},
        ]
    )
    assert latest_wins is not None and latest_wins.verdict == "pass"
    combo = deterministic_verdict(
        [
            {"kind": "wb_build", "ok": True},
            {"kind": "wb_validate", "ok": True, "violations": 0},
        ]
    )
    assert combo is not None and combo.verdict == "pass"
    # 任一类失败一票否决
    veto = deterministic_verdict(
        [
            {"kind": "wb_validate", "ok": True},
            {"kind": "path_test", "ok": False, "reachable": False},
        ]
    )
    assert veto is not None and veto.verdict == "fail"


def _facts_tool_registry(name: str, result_text: str) -> ToolRegistry:
    registry = ToolRegistry(PermissionGate())

    async def handler(layout_json: str = "") -> str:
        return result_text

    registry.register(
        ToolSpec(
            name=name,
            description="",
            parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
            level=PermissionLevel.READ,
            handler=handler,
        )
    )
    return registry


async def test_deterministic_pass_skips_judge(tmp_path):
    """决定性事实全 ok → 直接 pass。脚本中没有 judge 回合：若 LLM judge 被调用，
    FakeModel 脚本耗尽将导致步骤异常——测试通过即证明规则先行。"""
    registry = _facts_tool_registry(
        "wb_validate",
        '校验PASS：实测 15 个构件\n[facts] {"kind": "wb_validate", "ok": true, "violations": 0}',
    )
    model = FakeModel(
        [
            plan("standard", ("搭建并校验", "校验通过")),
            tool_turn("wb_validate", '{"layout_json": "{}"}'),
            AssistantTurn(content="校验通过"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("确定性验收"))
    runner = TaskRunner(model, registry, writer)
    outcome = await runner.run("搭白盒并校验")
    assert outcome.success
    verifies = [e for e in read_events(writer.trace_path) if e["event"] == "verify_result"]
    assert verifies[0]["mode"] == "deterministic"
    assert verifies[0]["verdict"] == "pass"


async def test_deterministic_fail_without_judge(tmp_path):
    """决定性事实失败 → 直接 fail（不调 judge），按正常重试/放弃路径走。"""
    registry = _facts_tool_registry(
        "path_test",
        '{"reachable": false}\n[facts] {"kind": "path_test", "ok": false, "reachable": false}',
    )
    model = FakeModel(
        [
            plan("standard", ("验证可达", "两房间可达")),
            tool_turn("path_test", '{"layout_json": "{}"}'),
            AssistantTurn(content="测了"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("确定性失败"))
    runner = TaskRunner(model, registry, writer, max_step_attempts=1)
    outcome = await runner.run("验证可达性")
    assert not outcome.success
    verifies = [e for e in read_events(writer.trace_path) if e["event"] == "verify_result"]
    assert verifies[0]["mode"] == "deterministic"
    assert verifies[0]["verdict"] == "fail"
    assert "path_test" in verifies[0]["reason"]


# ---------- B1 PlanStep 契约 ----------


def plan_raw(task_class: str, steps: list[dict]) -> AssistantTurn:
    return AssistantTurn(content=json.dumps({"task_class": task_class, "steps": steps}))


def _tool_allowed(tools: list[str], name: str) -> bool:
    return any(tool == name or tool.endswith(f"__{name}") for tool in tools)


async def test_planner_parses_contract_fields():
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "搭建并校验",
                        "acceptance": "校验通过",
                        "allowed_tools": ["wb_build", "wb_validate"],
                        "permission_ceiling": "write_safe",
                        "preconditions": ["editor_online"],
                        "success_checks": [{"kind": "wb_validate"}],
                        "required_evidence": ["screenshot", "vision_review"],
                        "rollback_policy": "wb_clear",
                        "step_budget": {"max_turns": 5},
                    }
                ],
            )
        ]
    )
    _, steps = await make_plan(model, "目标：截图视觉审查")
    step = steps[0]
    assert step.allowed_tools == ["wb_build", "wb_validate"]
    assert step.permission_ceiling == "write_safe"
    assert step.preconditions == ["editor_online"]
    assert step.success_checks == [{"kind": "wb_validate"}]
    assert step.required_evidence == ["screenshot", "vision_review"]
    assert step.rollback_policy == "wb_clear"
    assert step.step_budget == {"max_turns": 5}


async def test_planner_garbage_contract_falls_back_to_defaults():
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "做事",
                        "allowed_tools": "wb_build",  # 不是列表
                        "permission_ceiling": "sudo",  # 非法值
                        "success_checks": "全都过",  # 不是列表
                        "step_budget": [1, 2],  # 不是 dict
                    }
                ],
            )
        ]
    )
    _, steps = await make_plan(model, "目标")
    step = steps[0]
    assert step.allowed_tools == []
    assert step.permission_ceiling == ""
    assert step.success_checks == []
    assert step.step_budget == {}
    assert step.rollback_policy == "none"


async def test_planner_normalizes_path_test_success_alias():
    """弱模型常把 path_test.reachable 写成 success；规划器应在契约层纠正。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "搭建白盒空间并验证 path_test 可达",
                        "acceptance": "导航可达",
                        "allowed_tools": ["wb_build"],
                        "success_checks": [
                            {"kind": "path_test", "field": "success", "equals": True}
                        ],
                    }
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建白盒空间，然后验证导航可达")

    step = steps[0]
    assert {"kind": "path_test", "field": "reachable", "equals": True} in step.success_checks
    assert "path_test" in step.allowed_tools


def test_plan_step_loads_old_session_without_contract_fields():
    """旧 session.json（无契约字段）必须能加载，全部回默认值。"""
    old = {"id": "s1", "intent": "旧步骤", "acceptance": "", "status": "done", "attempts": 1}
    step = PlanStep(**old)
    assert step.allowed_tools == [] and step.preconditions == []
    assert step.rollback_policy == "none" and step.step_budget == {}


def test_evaluate_success_checks_rules():
    checks = [
        {"kind": "wb_validate"},
        {"kind": "path_test", "field": "reachable", "equals": True},
    ]
    assert evaluate_success_checks([], []) is None
    assert evaluate_success_checks([{"kind": ""}], []) is None
    missing = evaluate_success_checks(checks, [])
    assert missing is not None and missing.verdict == "insufficient"
    partial = evaluate_success_checks(checks, [{"kind": "wb_validate", "ok": True}])
    assert (
        partial is not None and partial.verdict == "insufficient" and "path_test" in partial.reason
    )
    failed = evaluate_success_checks(
        checks,
        [
            {"kind": "wb_validate", "ok": True},
            {"kind": "path_test", "ok": True, "reachable": False},
        ],
    )
    assert failed is not None and failed.verdict == "fail"
    passed = evaluate_success_checks(
        checks,
        [{"kind": "wb_validate", "ok": True}, {"kind": "path_test", "ok": True, "reachable": True}],
    )
    assert passed is not None and passed.verdict == "pass"


def test_evaluate_success_checks_treats_wb_validate_valid_as_ok_alias():
    passed = evaluate_success_checks(
        [{"kind": "wb_validate", "field": "valid", "equals": True}],
        [{"kind": "wb_validate", "ok": True, "violations": 0}],
    )

    assert passed is not None and passed.verdict == "pass"


def test_evaluate_success_checks_supports_numeric_min_max():
    checks = [{"kind": "path_test", "field": "path_length", "min": 1500}]

    passed = evaluate_success_checks(checks, [{"kind": "path_test", "path_length": 2855.1}])
    failed = evaluate_success_checks(checks, [{"kind": "path_test", "path_length": 1200.0}])

    assert passed is not None and passed.verdict == "pass"
    assert failed is not None and failed.verdict == "fail"
    assert "path_length" in failed.reason


def test_evaluate_success_checks_treats_path_test_total_as_latest_fact_count():
    result = evaluate_success_checks(
        [{"kind": "path_test", "field": "total", "equals": 1}],
        [{"kind": "path_test", "ok": True, "reachable": True, "path_length": 2855.1}],
    )

    assert result is not None and result.verdict == "pass"


def test_required_evidence_accepts_path_test_result_alias():
    result = evaluate_required_evidence(
        ["path_test_result"],
        [{"kind": "path_test", "ok": True, "reachable": True}],
    )

    assert result is None


def test_whitebox_recovery_hint_guides_common_layout_errors():
    step = PlanStep(id="s1", intent="使用 wb_build 搭建白盒结构", acceptance="wb_build 成功")
    hint = _whitebox_recovery_hint(
        step,
        "房间 upper_path 的窗只能开在外墙；房间 central 的内部共享墙门洞必须两侧对齐",
    )

    assert "删除所有非必要 windows" in hint
    assert "at/width 完全相同" in hint
    assert "不要猜共享墙" in hint


def test_required_evidence_fails_when_screenshot_frame_fact_is_false():
    result = evaluate_required_evidence(
        ["screenshot", "vision_review"],
        [
            {"kind": "screenshot", "ok": False, "framing_reason": "截图主体不在画面中心"},
            {"kind": "vision_review", "ok": True},
        ],
    )

    assert result is not None
    assert result.verdict == "fail"
    assert "screenshot" in result.reason


async def test_required_evidence_blocks_whitebox_pass_without_screenshot_and_vision(tmp_path):
    """白盒硬门禁：即使 wb_validate PASS，缺截图/视觉证据也不能收口。"""
    registry = _facts_tool_registry(
        "ue_whitebox__wb_validate",
        '校验PASS\n[facts] {"kind": "wb_validate", "ok": true, "violations": 0}',
    )
    script = [
        plan_raw(
            "standard",
            [
                {
                    "intent": "搭建并视觉校验白盒",
                    "acceptance": "校验和视觉都通过",
                    "success_checks": [{"kind": "wb_validate"}],
                    "required_evidence": ["screenshot", "vision_review"],
                }
            ],
        ),
        tool_turn("ue_whitebox__wb_validate", '{"layout_json": "{}"}'),
        AssistantTurn(content="校验通过"),
    ]
    writer = RunWriter(tmp_path, TaskSession.new("白盒硬门禁"))
    runner = TaskRunner(FakeModel(script), registry, writer, max_step_attempts=1)

    outcome = await runner.run("搭一个白盒并截图自查")

    assert not outcome.success
    verifies = [e for e in read_events(writer.trace_path) if e["event"] == "verify_result"]
    assert verifies[0]["mode"] == "required_evidence"
    assert verifies[0]["verdict"] == "insufficient"
    assert "screenshot" in verifies[0]["reason"]


async def test_scoped_registry_allowlist_and_ceiling():
    base = make_registry()  # echo: READ

    async def hammer(text: str = "") -> str:
        return "砸了"

    base.register(_spec("srv__hammer", PermissionLevel.WRITE_SAFE, hammer))
    # 白名单（裸名匹配带前缀注册名）
    scoped = ScopedRegistry(base, allowed_tools=["echo"])
    assert [s["function"]["name"] for s in scoped.specs()] == ["echo"]
    denied = await scoped.run("srv__hammer", "{}")
    assert not denied.ok and "本步骤契约不允许" in denied.text
    assert (await scoped.run("echo", '{"text": "hi"}')).ok
    bare = ScopedRegistry(base, allowed_tools=["hammer"])
    assert "srv__hammer" in bare.names()
    # 权限上限
    readonly = ScopedRegistry(base, permission_ceiling="read")
    assert "srv__hammer" not in readonly.names()
    denied_by_ceiling = await readonly.run("srv__hammer", "{}")
    assert not denied_by_ceiling.ok and "权限上限" in denied_by_ceiling.text
    # 显式点名优先于上限：白名单里的写工具不被 ceiling=read 拦
    # （planner 实测会同时声明两者，按上限拦会让步骤无解）
    explicit = ScopedRegistry(base, allowed_tools=["hammer"], permission_ceiling="read")
    assert "srv__hammer" in explicit.names()
    assert (await explicit.run("srv__hammer", "{}")).ok
    assert "echo" not in explicit.names()  # 未点名的工具仍受白名单约束
    # 非法上限值 → 不限（宽于实际比误杀安全）
    assert "srv__hammer" in ScopedRegistry(base, permission_ceiling="sudo").names()


async def test_contract_success_checks_drive_retry_then_pass(tmp_path):
    """缺契约证据 → insufficient 重试；补调验证工具 → contract pass。全程不调 judge。"""
    registry = _facts_tool_registry(
        "wb_validate", '校验PASS\n[facts] {"kind": "wb_validate", "ok": true, "violations": 0}'
    )
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "搭建并校验",
                        "acceptance": "校验通过",
                        "success_checks": [{"kind": "wb_validate"}],
                    }
                ],
            ),
            AssistantTurn(content="搭好了（但没调验证工具）"),
            tool_turn("wb_validate", '{"layout_json": "{}"}'),
            AssistantTurn(content="验证过了"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("契约驱动"))
    runner = TaskRunner(model, registry, writer)
    outcome = await runner.run("搭白盒")
    assert outcome.success
    verifies = [e for e in read_events(writer.trace_path) if e["event"] == "verify_result"]
    assert verifies[0]["mode"] == "contract" and verifies[0]["verdict"] == "insufficient"
    assert verifies[1]["mode"] == "contract" and verifies[1]["verdict"] == "pass"


async def test_contract_pass_stops_after_tool_without_summary_turn(tmp_path):
    """工具事实已经满足 step 契约时，runner 不再额外请求模型做收口总结。

    真机 UE eval 中 path_test 已经返回 reachable=true 后，后续总结请求如果超时/断线，
    不应抹掉已经拿到的客观验证证据。
    """
    registry = _facts_tool_registry(
        "path_test",
        '{"reachable": true}\n[facts] {"kind": "path_test", "ok": true, "reachable": true}',
    )
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "验证路径",
                        "acceptance": "path_test 返回可达",
                        "success_checks": [
                            {"kind": "path_test", "field": "reachable", "equals": True}
                        ],
                    }
                ],
            ),
            tool_turn("path_test", "{}"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("工具后早停"))
    runner = TaskRunner(model, registry, writer, max_step_attempts=1)

    outcome = await runner.run("验证可达性")

    assert outcome.success
    assert len(model.seen_messages) == 2  # plan + coder 工具调用；没有第三次总结请求
    verifies = [e for e in read_events(writer.trace_path) if e["event"] == "verify_result"]
    assert verifies[0]["mode"] == "contract"
    assert verifies[0]["verdict"] == "pass"


async def test_decisive_failure_overrides_contract_pass(tmp_path):
    """契约检查通过时，也不能忽略同一步里的客观验证失败。

    真机黑盒评测里 build 步 success_checks 只要求 wb_build.ok，但同一步
    wb_validate 已返回 ok=false；runner 不应把这类步骤放行。
    """
    registry = make_registry()

    async def wb_build(layout_json: str = "") -> str:
        return 'built\n[facts] {"kind": "wb_build", "ok": true}'

    async def wb_validate(layout_json: str = "") -> str:
        return '校验FAIL\n[facts] {"kind": "wb_validate", "ok": false, "violations": 1}'

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_build",
            description="",
            parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_build,
        )
    )
    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_validate",
            description="",
            parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
            level=PermissionLevel.READ,
            handler=wb_validate,
        )
    )
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "搭建并顺手校验",
                        "acceptance": "wb_build 成功",
                        "allowed_tools": ["ue_whitebox__wb_build", "ue_whitebox__wb_validate"],
                        "success_checks": [{"kind": "wb_build", "field": "ok", "equals": True}],
                    }
                ],
            ),
            tool_turn("ue_whitebox__wb_build", '{"layout_json": "{}"}'),
            tool_turn("ue_whitebox__wb_validate", '{"layout_json": "{}"}'),
            AssistantTurn(content="搭好了，但校验失败"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("契约不能盖过失败"))
    runner = TaskRunner(model, registry, writer, max_step_attempts=1)

    outcome = await runner.run("搭白盒")

    assert not outcome.success
    verifies = [e for e in read_events(writer.trace_path) if e["event"] == "verify_result"]
    assert verifies[0]["verdict"] == "fail"
    assert "wb_validate" in verifies[0]["reason"]


async def test_contract_allowed_tools_denies_in_step(tmp_path):
    """步内调契约外工具：收到 [denied] 契约文本，换契约内工具后正常收口。"""
    registry = make_registry()

    async def hammer(text: str = "") -> str:
        return "砸了"

    registry.register(_spec("srv__hammer", PermissionLevel.WRITE_SAFE, hammer))
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [{"intent": "只许用 echo", "acceptance": "", "allowed_tools": ["echo"]}],
            ),
            tool_turn("srv__hammer", "{}"),
            AssistantTurn(content="hammer 被契约拒绝，已用 echo 完成"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("契约白名单"))
    runner = TaskRunner(model, registry, writer)
    outcome = await runner.run("受限步骤")
    assert outcome.success
    hammer_calls = [
        e
        for e in read_events(writer.trace_path)
        if e["event"] == "tool_call" and e.get("tool") == "srv__hammer"
    ]
    assert hammer_calls and "本步骤契约不允许" in hammer_calls[0]["result_preview"]


async def test_contract_precondition_hint_injected(tmp_path):
    """前置条件未满足：执行提示注入补救指引，trace 记 precondition_unmet。"""
    registry = make_registry()

    async def editor_status() -> str:
        return "offline：编辑器桥不可达"

    registry.register(
        ToolSpec(
            name="ue_editor__editor_status",
            description="",
            parameters={"type": "object", "properties": {}},
            level=PermissionLevel.READ,
            handler=editor_status,
        )
    )
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [{"intent": "读场景", "acceptance": "", "preconditions": ["editor_online"]}],
            ),
            AssistantTurn(content="环境未就绪，已按提示说明"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("前置条件"))
    runner = TaskRunner(model, registry, writer)
    await runner.run("编辑器任务")
    exec_view = model.seen_messages[1]
    assert any("[前置条件未满足]" in str(m.get("content")) for m in exec_view)
    events = [e["event"] for e in read_events(writer.trace_path)]
    assert "precondition_unmet" in events


async def test_contract_rollback_wb_clear_on_abort(tmp_path):
    """步骤超限失败 → 契约回滚 wb_clear 自动执行并记 trace。"""
    registry = make_registry()
    cleared: list[str] = []

    async def wb_clear(prefix: str = "WB") -> str:
        cleared.append(prefix)
        return f"已删除 3 个 {prefix}_ 构件"

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_clear",
            description="",
            parameters={"type": "object", "properties": {"prefix": {"type": "string"}}},
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_clear,
        )
    )
    script = [
        plan_raw(
            "standard",
            [{"intent": "搭白盒", "acceptance": "校验通过", "rollback_policy": "wb_clear"}],
        )
    ]
    for _ in range(3):
        script += [AssistantTurn(content="没搭成"), judge("fail", "不行")]
    writer = RunWriter(tmp_path, TaskSession.new("契约回滚"))
    runner = TaskRunner(FakeModel(script), registry, writer)
    outcome = await runner.run("白盒任务")
    assert not outcome.success
    assert cleared == ["WB"]  # 无 wb_build 事实时用工具默认前缀
    rollbacks = [e for e in read_events(writer.trace_path) if e["event"] == "rollback_action"]
    assert rollbacks and rollbacks[0]["policy"] == "wb_clear"


async def test_contract_rollback_uses_actual_build_prefix(tmp_path):
    """回滚前缀错位回归：本步用 S1_ 前缀落地，wb_clear 必须按 S1 清而非默认 WB
    （真机 e2e 实测：默认前缀删的是上次任务残留，本步构件全留在场景里）。"""
    registry = make_registry()
    cleared: list[str] = []

    async def wb_build(layout_json: str = "", prefix: str = "WB") -> str:
        return f'搭建完成\n[facts] {{"kind": "wb_build", "ok": true, "prefix": "{prefix}"}}'

    async def wb_clear(prefix: str = "WB") -> str:
        cleared.append(prefix)
        return f"已删除 {prefix}_ 构件"

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_build",
            description="",
            parameters={
                "type": "object",
                "properties": {"layout_json": {"type": "string"}, "prefix": {"type": "string"}},
            },
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_build,
        )
    )
    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_clear",
            description="",
            parameters={"type": "object", "properties": {"prefix": {"type": "string"}}},
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_clear,
        )
    )
    script = [
        plan_raw(
            "standard",
            [
                {
                    "intent": "搭白盒",
                    "acceptance": "校验通过",
                    "rollback_policy": "wb_clear",
                    "success_checks": [{"kind": "wb_validate"}],  # 永远缺证据 → 步骤失败
                }
            ],
        )
    ]
    for _ in range(3):
        script += [
            tool_turn("ue_whitebox__wb_build", '{"layout_json": "{}", "prefix": "S1"}'),
            AssistantTurn(content="搭好了"),
        ]
    writer = RunWriter(tmp_path, TaskSession.new("回滚前缀"))
    runner = TaskRunner(FakeModel(script), registry, writer)
    outcome = await runner.run("白盒任务")
    assert not outcome.success
    assert cleared == ["S1"]


# ---------- A4 视觉审查集成（runner 局部重生成回灌）----------


def _vision_registry() -> ToolRegistry:
    """注册会落 screenshot/wb_validate 事实的工具（供 A4 集成测试用）。"""
    registry = ToolRegistry(PermissionGate())

    async def viewport_screenshot(file_path: str = "") -> str:
        return 'saved\n[facts] {"kind": "screenshot", "ok": true, "path": "/tmp/shot.png"}'

    async def wb_validate(layout_json: str = "") -> str:
        return '校验PASS\n[facts] {"kind": "wb_validate", "ok": true, "violations": 0}'

    registry.register(
        ToolSpec(
            name="ue_editor__viewport_screenshot",
            description="",
            parameters={"type": "object", "properties": {"file_path": {"type": "string"}}},
            level=PermissionLevel.READ,
            handler=viewport_screenshot,
        )
    )
    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_validate",
            description="",
            parameters={"type": "object", "properties": {"layout_json": {"type": "string"}}},
            level=PermissionLevel.READ,
            handler=wb_validate,
        )
    )
    return registry


async def test_viewport_screenshot_auto_focus_uses_latest_wb_build_folder(tmp_path):
    """模型裸调截图时，runner 用最新 wb_build.folder_root 精确聚焦本批白盒。"""
    registry = ToolRegistry(PermissionGate())
    screenshot_args = []

    async def wb_build(prefix: str = "WB") -> str:
        return (
            '搭建完成\n[facts] {"kind": "wb_build", "ok": true, '
            f'"prefix": "{prefix}", "folder_root": "SPC1/abc123"}}'
        )

    async def viewport_screenshot(
        file_path: str = "",
        focus_prefix: str = "",
        clean_view: bool = False,
    ) -> str:
        screenshot_args.append(
            {"file_path": file_path, "focus_prefix": focus_prefix, "clean_view": clean_view}
        )
        return 'saved\n[facts] {"kind": "screenshot", "ok": true, "path": "/tmp/shot.png"}'

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_build",
            description="",
            parameters={"type": "object", "properties": {"prefix": {"type": "string"}}},
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_build,
        )
    )
    registry.register(
        ToolSpec(
            name="ue_editor__viewport_screenshot",
            description="",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "focus_prefix": {"type": "string"},
                    "clean_view": {"type": "boolean"},
                },
            },
            level=PermissionLevel.READ,
            handler=viewport_screenshot,
        )
    )
    model = FakeModel(
        [
            plan("standard", ("搭建并截图", "完成")),
            tool_turn("ue_whitebox__wb_build", '{"prefix": "SPC1"}'),
            tool_turn("ue_editor__viewport_screenshot", "{}"),
            AssistantTurn(content="完成"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("截图聚焦"))
    runner = TaskRunner(model, registry, writer)

    outcome = await runner.run("搭建 SPC1 并截图")

    assert outcome.success
    assert screenshot_args == [{"file_path": "", "focus_prefix": "SPC1/abc123", "clean_view": True}]


async def test_viewport_screenshot_auto_focus_keeps_explicit_focus_prefix(tmp_path):
    """模型显式传 focus_prefix 时，runner 不覆盖。"""
    registry = ToolRegistry(PermissionGate())
    screenshot_args = []

    async def wb_build() -> str:
        return '搭建完成\n[facts] {"kind": "wb_build", "ok": true, "folder_root": "SPC1/abc123"}'

    async def viewport_screenshot(focus_prefix: str = "", clean_view: bool = True) -> str:
        screenshot_args.append({"focus_prefix": focus_prefix, "clean_view": clean_view})
        return 'saved\n[facts] {"kind": "screenshot", "ok": true, "path": "/tmp/shot.png"}'

    registry.register(
        ToolSpec(
            name="ue_whitebox__wb_build",
            description="",
            parameters={"type": "object", "properties": {}},
            level=PermissionLevel.WRITE_SAFE,
            handler=wb_build,
        )
    )
    registry.register(
        ToolSpec(
            name="ue_editor__viewport_screenshot",
            description="",
            parameters={
                "type": "object",
                "properties": {
                    "focus_prefix": {"type": "string"},
                    "clean_view": {"type": "boolean"},
                },
            },
            level=PermissionLevel.READ,
            handler=viewport_screenshot,
        )
    )
    model = FakeModel(
        [
            plan("standard", ("搭建并截图", "完成")),
            tool_turn("ue_whitebox__wb_build", "{}"),
            tool_turn(
                "ue_editor__viewport_screenshot",
                '{"focus_prefix": "manual/batch", "clean_view": false}',
            ),
            AssistantTurn(content="完成"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("截图显式聚焦"))
    runner = TaskRunner(model, registry, writer)

    outcome = await runner.run("搭建 SPC1 并截图")

    assert outcome.success
    assert screenshot_args == [{"focus_prefix": "manual/batch", "clean_view": False}]


class _FakeReviewer:
    """按调用顺序返回预设审查结果，并记录收到的 (截图路径, 需求)。"""

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[list[str], str]] = []

    async def __call__(self, paths, requirement):
        self.calls.append((list(paths), requirement))
        return self._results.pop(0)


async def test_vision_high_issue_fails_then_regenerates_and_passes(tmp_path):
    """A4：截图被视觉审查判 high 问题 → 步骤 fail；问题区域回灌 history → 重做后
    决定性证据（wb_validate）+ 视觉通过 → pass。全程不调 judge。"""
    reviewer = _FakeReviewer(
        [
            parse_review(
                '{"issues": [{"area": "房间A", "issue": "与房间B未连通", "severity": "high"}]}'
            ),
            parse_review('{"issues": []}'),
        ]
    )
    model = FakeModel(
        [
            plan("standard", ("搭建并目视校验", "布局符合需求")),
            # 第 1 次尝试：只截图（视觉审查会判 high → fail）
            tool_turn("ue_editor__viewport_screenshot", "{}"),
            AssistantTurn(content="截了俯视图"),
            # 第 2 次尝试：修正后重新校验+截图（视觉通过 + wb_validate 决定性 ok）
            tool_turn("ue_whitebox__wb_validate", '{"layout_json": "{}"}'),
            tool_turn("ue_editor__viewport_screenshot", "{}"),
            AssistantTurn(content="已修正房间A并重新校验"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("视觉迭代"))
    runner = TaskRunner(model, _vision_registry(), writer, vision_reviewer=reviewer)
    outcome = await runner.run("搭一个两房间连通的关卡")

    assert outcome.success
    assert writer.session.plan[0].attempts == 2
    # 审查被调用两次，且拿到的是截图实际落盘路径
    assert len(reviewer.calls) == 2
    assert reviewer.calls[0][0] == ["/tmp/shot.png"]
    assert reviewer.calls[0][1] == "搭一个两房间连通的关卡"
    # 视觉问题（区域名）回灌进了执行方上下文
    assert any(any("房间A" in str(m.get("content")) for m in view) for view in model.seen_messages)
    retry_views = [
        view for view in model.seen_messages if any("房间A" in str(m.get("content")) for m in view)
    ]
    assert retry_views
    retry_context = "\n".join(str(m.get("content", "")) for m in retry_views[0])
    assert "房间A" in retry_context
    assert "截了俯视图" not in retry_context
    events = read_events(writer.trace_path)
    vis = [e for e in events if e["event"] == "vision_review"]
    assert [e["passed"] for e in vis] == [False, True]
    assert vis[0]["facts"]["kind"] == "vision_review"
    assert vis[0]["facts"]["ok"] is False
    assert vis[0]["facts"]["high_count"] == 1
    assert vis[1]["facts"]["kind"] == "vision_review"
    assert vis[1]["facts"]["ok"] is True
    verifies = [e for e in events if e["event"] == "verify_result"]
    assert verifies[0]["verdict"] == "fail" and verifies[0]["mode"] == "deterministic"
    assert verifies[1]["verdict"] == "pass"


async def test_no_reviewer_keeps_legacy_behavior(tmp_path):
    """未注入 reviewer（未配 vision）：截图照常，但不产生 vision_review 事实，
    验收回落到 judge——行为与 A4 之前完全一致。"""
    model = FakeModel(
        [
            plan("standard", ("截图", "看起来对")),
            tool_turn("ue_editor__viewport_screenshot", "{}"),
            AssistantTurn(content="截好了"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("无视觉"))
    runner = TaskRunner(model, _vision_registry(), writer, vision_reviewer=None)
    outcome = await runner.run("截个图")
    assert outcome.success
    events = [e["event"] for e in read_events(writer.trace_path)]
    assert "vision_review" not in events


async def test_vision_reviewer_failure_does_not_crash_step(tmp_path):
    """视觉审查链路本身故障（vision 模型不可用等）：记 trace、不炸步骤，
    验收照常回落 judge。"""

    async def boom(paths, requirement):
        raise RuntimeError("vision 模型 503")

    model = FakeModel(
        [
            plan("standard", ("截图", "看起来对")),
            tool_turn("ue_editor__viewport_screenshot", "{}"),
            AssistantTurn(content="截好了"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("视觉故障"))
    runner = TaskRunner(model, _vision_registry(), writer, vision_reviewer=boom)
    outcome = await runner.run("截个图")
    assert outcome.success
    events = [e["event"] for e in read_events(writer.trace_path)]
    assert "vision_review_error" in events
    assert "vision_review" not in events


async def test_vision_review_hard_timeout_degrades(tmp_path):
    """视觉审查挂起超过硬超时 → 记 vision_review_error、降级不阻断（litellm 对某些
    多模态端点异步挂起不遵守自身超时的真机教训），步骤照常走 judge。"""

    async def hang(paths, requirement):
        await asyncio.sleep(10)
        raise AssertionError("不应执行到这里")

    model = FakeModel(
        [
            plan("standard", ("截图", "看起来对")),
            tool_turn("ue_editor__viewport_screenshot", "{}"),
            AssistantTurn(content="截好了"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("视觉超时"))
    runner = TaskRunner(
        model, _vision_registry(), writer, vision_reviewer=hang, vision_timeout_seconds=0.05
    )
    outcome = await runner.run("截个图")
    assert outcome.success
    errs = [e for e in read_events(writer.trace_path) if e["event"] == "vision_review_error"]
    assert errs and "超时" in errs[0]["error"]


async def test_vision_skipped_without_screenshots(tmp_path):
    """本步没截图：即便注入了 reviewer 也不触发审查（视觉是增量证据）。"""
    reviewer = _FakeReviewer([parse_review('{"issues": []}')])
    model = FakeModel(
        [
            plan("standard", ("只校验", "校验通过")),
            tool_turn("ue_whitebox__wb_validate", '{"layout_json": "{}"}'),
            AssistantTurn(content="校验过了"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("无截图"))
    runner = TaskRunner(model, _vision_registry(), writer, vision_reviewer=reviewer)
    outcome = await runner.run("只跑校验")
    assert outcome.success
    assert reviewer.calls == []  # 没截图 → 审查未被调用
    events = [e["event"] for e in read_events(writer.trace_path)]
    assert "vision_review" not in events


# ---------- B1 PlanStep 契约（续）----------


async def test_contract_reconciles_check_tools_into_allowlist():
    """契约自洽性：success_checks 要求的验证工具自动并入 allowed_tools
    （否则工具面过滤后模型看不见验证工具，证据永远补不上——真机 e2e 实测教训）。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "搭建",
                        "acceptance": "校验通过",
                        "allowed_tools": ["ue_whitebox__wb_build"],
                        "success_checks": [
                            {"kind": "wb_validate"},
                            {"kind": "path_test", "field": "reachable"},
                        ],
                    }
                ],
            )
        ]
    )
    _, steps = await make_plan(model, "目标")
    assert "wb_validate" in steps[0].allowed_tools
    assert "path_test" in steps[0].allowed_tools
    # 无 allowed_tools 限制时不动（不限制就无需并入）
    model2 = FakeModel(
        [plan_raw("standard", [{"intent": "x", "success_checks": [{"kind": "compile"}]}])]
    )
    _, steps2 = await make_plan(model2, "目标")
    assert steps2[0].allowed_tools == []


async def test_planner_reconciles_whitebox_build_with_visual_gate():
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {"intent": "使用 wb_build 搭建白盒结构", "acceptance": "完成落地"},
                    {"intent": "拍摄俯视截图并做 vision_review", "acceptance": "视觉通过"},
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建白盒并截图视觉审查")

    build = steps[0]
    assert build.required_evidence == ["screenshot", "vision_review"]
    assert "wb_build" in build.allowed_tools
    assert "wb_clear" in build.allowed_tools
    assert "viewport_screenshot" in build.allowed_tools
    assert build.preconditions == ["editor_online"]
    assert build.rollback_policy == "wb_clear"


async def test_planner_strips_unrequested_whitebox_visual_gate():
    """用户没有明确要求截图/视觉时，planner 不应靠幻觉把白盒任务升级成视觉硬门禁。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建白盒结构，然后俯视截图自查并运行 wb_validate",
                        "acceptance": "wb_validate 返回 ok: true",
                        "allowed_tools": [
                            "ue_whitebox__wb_build",
                            "ue_whitebox__wb_clear",
                            "ue_editor__viewport_screenshot",
                            "ue_whitebox__wb_validate",
                        ],
                        "required_evidence": ["screenshot", "vision_review"],
                    }
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建一个默认 slab 白盒空间，并用 wb_validate 校验")

    build = steps[0]
    assert build.required_evidence == []
    assert "ue_editor__viewport_screenshot" not in build.allowed_tools


async def test_planner_strips_unrequested_visual_evidence_without_screenshot_tool():
    """即使 planner 只幻觉硬证据、没开放截图工具，也不能让白盒步骤卡死。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建白盒结构并运行 wb_validate",
                        "acceptance": "wb_validate 返回 ok: true",
                        "allowed_tools": ["ue_whitebox__wb_build", "ue_whitebox__wb_validate"],
                        "required_evidence": ["screenshot", "vision_review"],
                    }
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建一个默认 slab 白盒空间，并用 wb_validate 校验")

    assert steps[0].required_evidence == []


async def test_planner_respects_explicit_no_viewport_screenshot():
    """用户显式禁止 viewport_screenshot 时，不应因为出现 screenshot 字样而加视觉门禁。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建白盒结构",
                        "acceptance": "wb_build 返回 ok",
                        "allowed_tools": [
                            "ue_whitebox__wb_build",
                            "ue_editor__viewport_screenshot",
                        ],
                        "required_evidence": ["screenshot", "vision_review"],
                    }
                ],
            )
        ]
    )

    _, steps = await make_plan(
        model, "搭建默认 slab 白盒空间；不要调用 viewport_screenshot，只用 wb_validate 校验"
    )

    build = steps[0]
    assert build.required_evidence == []
    assert "ue_editor__viewport_screenshot" not in build.allowed_tools


async def test_planner_accepts_split_whitebox_build_with_wb_build_fact():
    """白盒搭建与验证拆成两步时，build 步应靠 wb_build 成功事实收口。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建白盒结构",
                        "acceptance": "空间结构搭建完成",
                        "allowed_tools": ["ue_whitebox__wb_build", "ue_whitebox__wb_clear"],
                    },
                    {
                        "intent": "使用 wb_validate 和 path_test 验证",
                        "acceptance": "验证通过",
                        "allowed_tools": [
                            "ue_whitebox__wb_validate",
                            "ue_editor__path_test",
                        ],
                        "success_checks": [
                            {"kind": "wb_validate"},
                            {"kind": "path_test", "field": "reachable"},
                        ],
                    },
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建白盒后再验证")

    assert steps[0].success_checks == [{"kind": "wb_build", "field": "ok", "equals": True}]
    assert "wb_validate" in steps[0].allowed_tools


async def test_planner_keeps_clear_for_combined_whitebox_build_validate_step():
    """同一步 build+validate 失败时，也要允许 wb_clear 后重搭。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建楼梯白盒空间，并立即 wb_validate 校验",
                        "acceptance": "wb_validate 返回 ok",
                        "allowed_tools": [
                            "ue_whitebox__wb_build",
                            "ue_whitebox__wb_validate",
                        ],
                        "success_checks": [{"kind": "wb_validate", "field": "ok", "equals": True}],
                    }
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建带楼梯的白盒空间并校验")

    assert "wb_clear" in steps[0].allowed_tools


async def test_planner_keeps_whitebox_repair_tools_for_split_nav_validation():
    """白盒 build 与导航验证拆步时，验证步也要允许重建布局。

    真机黑盒评测里 path_test 发现几何断裂后，模型想用 wb_clear/wb_build 重搭，
    但 s2 白名单只剩导航工具，导致 agent 自我修复通道被框架切断。
    """
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建 slab 白盒空间",
                        "acceptance": "空间落地",
                        "allowed_tools": ["ue_whitebox__wb_build", "ue_whitebox__wb_clear"],
                    },
                    {
                        "intent": "重建 NavMesh 并用 path_test 验证入口到尽端房间可达",
                        "acceptance": "path_test 返回 reachable true",
                        "allowed_tools": [
                            "ue_editor__navmesh_rebuild",
                            "ue_editor__path_test",
                        ],
                        "success_checks": [
                            {"kind": "path_test", "field": "reachable", "equals": True}
                        ],
                    },
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建白盒空间，然后验证导航可达")

    validate = steps[1]
    assert _tool_allowed(validate.allowed_tools, "wb_build")
    assert _tool_allowed(validate.allowed_tools, "wb_clear")
    assert _tool_allowed(validate.allowed_tools, "navmesh_rebuild")
    assert _tool_allowed(validate.allowed_tools, "path_test")


async def test_planner_keeps_repair_tools_for_split_wb_validate():
    """wb_validate 单独成步时，也要允许重建布局。"""
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建 slab 白盒空间",
                        "acceptance": "空间落地",
                        "allowed_tools": ["ue_whitebox__wb_build"],
                    },
                    {
                        "intent": "使用 wb_validate 校验空间结构",
                        "acceptance": "wb_validate 返回 ok",
                        "allowed_tools": ["ue_whitebox__wb_validate"],
                        "success_checks": [{"kind": "wb_validate", "field": "ok", "equals": True}],
                    },
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建白盒空间后校验")

    validate = steps[1]
    assert _tool_allowed(validate.allowed_tools, "wb_build")
    assert _tool_allowed(validate.allowed_tools, "wb_clear")
    assert not _tool_allowed(validate.allowed_tools, "navmesh_rebuild")
    assert not _tool_allowed(validate.allowed_tools, "path_test")


async def test_planner_keeps_navmesh_rebuild_for_split_path_validation():
    """path_test 单独成步时，仍要允许重建 NavMesh。

    否则该步即使能 wb_build 重搭布局，也不能对新布局重建导航。
    """
    model = FakeModel(
        [
            plan_raw(
                "standard",
                [
                    {
                        "intent": "使用 wb_build 搭建 slab 白盒空间",
                        "acceptance": "空间落地",
                        "allowed_tools": ["ue_whitebox__wb_build"],
                    },
                    {
                        "intent": "重建导航网格",
                        "acceptance": "navmesh_rebuild 完成",
                        "allowed_tools": ["ue_editor__navmesh_rebuild"],
                    },
                    {
                        "intent": "用 path_test 验证入口到尽端房间可达",
                        "acceptance": "path_test 返回 reachable true",
                        "allowed_tools": ["ue_editor__path_test"],
                        "success_checks": [
                            {"kind": "path_test", "field": "reachable", "equals": True}
                        ],
                    },
                ],
            )
        ]
    )

    _, steps = await make_plan(model, "搭建白盒空间，重建导航后测试路径")

    path_step = steps[2]
    assert "navmesh_rebuild" in path_step.allowed_tools
    assert "wb_build" in path_step.allowed_tools


# ---------- B4 上下文工程 ----------


async def test_project_brief_injected_into_system_context(tmp_path):
    """开场探测 editor_status/engine_info → 工程状态摘要注入 system 消息（首位），
    并发 context_brief 事件。模型首轮即可看到环境，无需自己逐个探测。"""
    registry = make_registry()

    async def editor_status() -> str:
        return "online：编辑器桥可达（127.0.0.1:55557）"

    async def engine_info() -> str:
        return "UE 5.7 @ C:/Program Files/Epic Games/UE_5.7"

    registry.register(
        ToolSpec(
            name="ue_editor__editor_status",
            description="",
            parameters={"type": "object", "properties": {}},
            level=PermissionLevel.READ,
            handler=editor_status,
        )
    )
    registry.register(
        ToolSpec(
            name="ue_build__engine_info",
            description="",
            parameters={"type": "object", "properties": {}},
            level=PermissionLevel.READ,
            handler=engine_info,
        )
    )
    model = FakeModel([plan("trivial", ("回答", "")), AssistantTurn(content="好的")])
    writer = RunWriter(tmp_path, TaskSession.new("工程摘要"))
    runner = TaskRunner(model, registry, writer)
    await runner.run("做点事")
    # 执行步看到的首条消息是 system，且含工程状态摘要
    exec_view = model.seen_messages[1]
    assert exec_view[0]["role"] == "system"
    assert "工程状态" in exec_view[0]["content"]
    assert "UE 5.7" in exec_view[0]["content"]
    assert "online" in exec_view[0]["content"]
    events = [e for e in read_events(writer.trace_path) if e["event"] == "context_brief"]
    assert events and "工程状态" in events[0]["brief"]


async def test_no_brief_when_no_probe_tools(tmp_path):
    """无探测工具时不注入摘要、不发 context_brief（行为与此前一致）。"""
    model = FakeModel([plan("trivial", ("回答", "")), AssistantTurn(content="好的")])
    writer = RunWriter(tmp_path, TaskSession.new("无探测"))
    runner = TaskRunner(model, make_registry(), writer)
    await runner.run("做点事")
    exec_view = model.seen_messages[1]
    assert exec_view[0]["role"] == "system"
    assert "工程状态" not in exec_view[0]["content"]
    assert not [e for e in read_events(writer.trace_path) if e["event"] == "context_brief"]


async def test_progress_file_written_after_steps(tmp_path):
    """每步收口刷新 progress.md，含各步状态。"""
    model = FakeModel(
        [
            plan("standard", ("做 A", "A 完成"), ("做 B", "B 完成")),
            AssistantTurn(content="A 好"),
            judge("pass"),
            AssistantTurn(content="B 好"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("进度文件"))
    runner = TaskRunner(model, make_registry(), writer)
    await runner.run("做 A 和 B")
    progress = (writer.dir / "progress.md").read_text(encoding="utf-8")
    assert "# 进度" in progress
    assert "s1" in progress and "s2" in progress
    assert "[x]" in progress  # 至少一步完成


async def test_progress_line_in_step_prompt(tmp_path):
    """每步提示注入 [进度] 行，列出当前步与待办（compact 后仍随新提示重述）。"""
    model = FakeModel(
        [
            plan("standard", ("做 A", "A 完成"), ("做 B", "B 完成")),
            AssistantTurn(content="A 好"),
            judge("pass"),
            AssistantTurn(content="B 好"),
            judge("pass"),
        ]
    )
    writer = RunWriter(tmp_path, TaskSession.new("进度行"))
    runner = TaskRunner(model, make_registry(), writer)
    await runner.run("做 A 和 B")
    # 第一步执行视图的 user 提示含进度行（当前 s1、待办含 s2）
    exec_view = model.seen_messages[1]
    user_msg = exec_view[-1]["content"]
    assert "[进度]" in user_msg
    assert "当前 s1" in user_msg
    assert "s2" in user_msg
