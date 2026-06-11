"""K4：TaskRunner 状态机各转移路径（FakeModel 按调用顺序脚本化）。"""

import json

from tests.test_loop import FakeModel, make_registry
from ue5agent.agent.events import RunWriter, read_events
from ue5agent.agent.runner import TaskRunner
from ue5agent.agent.state import TaskSession
from ue5agent.llm.types import AssistantTurn


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
