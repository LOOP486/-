"""K4：TaskRunner 状态机各转移路径（FakeModel 按调用顺序脚本化）。"""

import asyncio
import json

from tests.test_loop import FakeModel, make_registry, tool_turn
from ue5agent.agent.events import RunWriter, read_events
from ue5agent.agent.planner import make_plan
from ue5agent.agent.report import build_report
from ue5agent.agent.runner import TaskRunner
from ue5agent.agent.state import PlanStep, TaskSession
from ue5agent.agent.verifier import deterministic_verdict, evaluate_success_checks
from ue5agent.agent.vision_review import parse_review
from ue5agent.core.errors import ErrorCategory, mark_env_unready, mark_error
from ue5agent.core.permissions import PermissionGate, PermissionLevel
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
                        "rollback_policy": "wb_clear",
                        "step_budget": {"max_turns": 5},
                    }
                ],
            )
        ]
    )
    _, steps = await make_plan(model, "目标")
    step = steps[0]
    assert step.allowed_tools == ["wb_build", "wb_validate"]
    assert step.permission_ceiling == "write_safe"
    assert step.preconditions == ["editor_online"]
    assert step.success_checks == [{"kind": "wb_validate"}]
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
    events = read_events(writer.trace_path)
    vis = [e for e in events if e["event"] == "vision_review"]
    assert [e["passed"] for e in vis] == [False, True]
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
