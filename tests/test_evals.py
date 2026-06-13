"""评测 harness：任务加载、检查器、沙盒工具、runner 全链路（mock 模型）。"""

from pathlib import Path

from tests.test_loop import FakeModel
from ue5agent.core.loop import LoopResult
from ue5agent.evals.checks import TaskOutcome, evaluate_check
from ue5agent.evals.runner import EvalTask, load_tasks, run_eval
from ue5agent.evals.sandbox import build_sandbox_registry
from ue5agent.llm.types import AssistantTurn, ToolCall

BASIC_TASKS = Path(__file__).parent.parent / "evals" / "tasks" / "basic.yaml"


def outcome(
    final: str = "",
    turns: int = 1,
    calls: list | None = None,
    tool_results: list | None = None,
) -> TaskOutcome:
    return TaskOutcome(
        result=LoopResult(final, turns, len(calls or [])),
        calls=calls or [],
        tool_results=tool_results or [],
    )


class TestChecks:
    def test_tool_called_pass_and_fail(self):
        ok = outcome(calls=[("echo", '{"text": "hi"}')])
        assert evaluate_check({"type": "tool_called", "tool": "echo"}, ok) is None
        assert evaluate_check({"type": "tool_called", "tool": "add"}, ok) is not None

    def test_tool_called_args_contain(self):
        ok = outcome(calls=[("echo", '{"text": "白盒"}')])
        check = {"type": "tool_called", "tool": "echo", "args_contain": "白盒"}
        assert evaluate_check(check, ok) is None
        check_miss = {"type": "tool_called", "tool": "echo", "args_contain": "蓝图"}
        assert evaluate_check(check_miss, ok) is not None

    def test_tool_not_called(self):
        bad = outcome(calls=[("add", "{}")])
        assert evaluate_check({"type": "tool_not_called", "tool": "add"}, bad) is not None

    def test_tool_called_times(self):
        twice = outcome(calls=[("add", "{}"), ("add", "{}")])
        check = {"type": "tool_called_times", "tool": "add", "at_least": 2}
        assert evaluate_check(check, twice) is None
        assert evaluate_check({**check, "at_least": 3}, twice) is not None

    def test_final_contains_and_max_turns(self):
        out = outcome(final="结果是 42", turns=3)
        assert evaluate_check({"type": "final_contains", "text": "42"}, out) is None
        assert evaluate_check({"type": "max_turns", "value": 2}, out) is not None

    def test_no_tool_errors(self):
        bad = outcome(tool_results=["ok", "[error] 未知工具：x"])
        assert evaluate_check({"type": "no_tool_errors"}, bad) is not None

    def test_unknown_check_type_fails_loudly(self):
        assert evaluate_check({"type": "typo_check"}, outcome()) is not None


class TestSandbox:
    async def test_tools_behave(self):
        registry = build_sandbox_registry()
        assert await registry.dispatch("add", '{"a": 17, "b": 25}') == "42"
        assert await registry.dispatch("convert", '{"value_cm": 250, "to": "m"}') == "2.5"
        await registry.dispatch("write_note", '{"key": "k", "content": "v"}')
        assert await registry.dispatch("read_note", '{"key": "k"}') == "v"
        assert "没有名为" in await registry.dispatch("read_note", '{"key": "ghost"}')
        assert registry.calls[0] == ("add", '{"a": 17, "b": 25}')


class TestRunner:
    def test_basic_yaml_loads(self):
        tasks = load_tasks(BASIC_TASKS)
        assert len(tasks) == 10
        assert all(task.checks for task in tasks)

    async def test_report_aggregates_pass_and_fail(self):
        tasks = [
            EvalTask(
                name="will_pass",
                prompt="echo hi",
                checks=[{"type": "tool_called", "tool": "echo"}],
            ),
            EvalTask(
                name="will_fail",
                prompt="echo hi",
                checks=[{"type": "final_contains", "text": "不会出现的文本"}],
            ),
        ]
        scripts = iter(
            [
                [
                    AssistantTurn(None, [ToolCall("c1", "echo", '{"text": "hi"}')]),
                    AssistantTurn("done"),
                ],
                [AssistantTurn("done")],
            ]
        )
        report = await run_eval(tasks, lambda: FakeModel(next(scripts)))
        assert report.pass_rate == 0.5
        passed, failed = report.results
        assert passed.passed and passed.tool_calls == 1
        assert passed.tool_errors == 0
        assert report.tool_error_rate == 0.0
        assert not failed.passed
        assert "不会出现的文本" in failed.failures[0]

    async def test_tool_errors_counted(self):
        task = EvalTask(
            name="calls_unknown_tool",
            prompt="x",
            checks=[{"type": "no_tool_errors"}],
        )
        script = [
            AssistantTurn(None, [ToolCall("c1", "ghost_tool", "{}")]),
            AssistantTurn("done"),
        ]
        report = await run_eval([task], lambda: FakeModel(script))
        assert report.results[0].tool_errors == 1
        assert report.tool_error_rate == 1.0
        assert not report.results[0].passed

    async def test_budget_exhausted_counts_as_failure(self):
        task = EvalTask(
            name="loops_forever",
            prompt="x",
            max_turns=2,
            checks=[{"type": "final_contains", "text": "ok"}],
        )
        endless = [AssistantTurn(None, [ToolCall("c1", "echo", '{"text": "x"}')])] * 2
        report = await run_eval([task], lambda: FakeModel(list(endless)))
        assert not report.results[0].passed
        assert report.pass_rate == 0.0


UE_TASKS = Path(__file__).parent.parent / "evals" / "tasks" / "ue.yaml"


class TestUeSuite:
    """E3/C3 UE 在线档：编排/指标/检查器离线单测（用替身 run_one，不碰真编辑器）。"""

    def test_ue_yaml_loads(self):
        from ue5agent.evals.ue_suite import load_ue_tasks

        tasks = load_ue_tasks(UE_TASKS)
        assert tasks and all(task.checks for task in tasks)
        assert any(t.name == "read_blueprint_and_explain" for t in tasks)

    def test_checks_cover_success_and_text(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        ok = UeRunRecord(success=True, final_answer="它继承自 Character，有 Camera 组件")
        assert evaluate_ue_check({"type": "run_succeeded"}, ok) is None
        assert evaluate_ue_check({"type": "final_contains", "text": "Camera"}, ok) is None
        miss = evaluate_ue_check({"type": "final_contains", "text": "蓝图缺失"}, ok)
        assert miss is not None
        any_ok = evaluate_ue_check({"type": "final_contains_any", "texts": ["相机", "Camera"]}, ok)
        assert any_ok is None

    def test_run_failed_check_for_fault_injection(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        failed = UeRunRecord(success=False, error="env_unready")
        assert evaluate_ue_check({"type": "run_failed"}, failed) is None
        succeeded = UeRunRecord(success=True)
        assert evaluate_ue_check({"type": "run_failed"}, succeeded) is not None

    def test_max_iterations_and_unknown_check(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        rec = UeRunRecord(success=True, iteration_count=9)
        assert evaluate_ue_check({"type": "max_iterations", "value": 12}, rec) is None
        assert evaluate_ue_check({"type": "max_iterations", "value": 5}, rec) is not None
        assert evaluate_ue_check({"type": "typo"}, rec) is not None

    async def test_run_ue_suite_aggregates_metrics(self):
        from ue5agent.evals.ue_suite import UeEvalTask, UeRunRecord, run_ue_suite

        tasks = [
            UeEvalTask(name="pass_one_try", prompt="a", checks=[{"type": "run_succeeded"}]),
            UeEvalTask(
                name="fail_text",
                prompt="b",
                checks=[{"type": "final_contains", "text": "缺"}],
            ),
        ]
        records = {
            "pass_one_try": UeRunRecord(
                success=True, final_answer="done", iteration_count=2, max_step_attempts=1
            ),
            "fail_text": UeRunRecord(
                success=True, final_answer="other", iteration_count=5, max_step_attempts=3
            ),
        }

        async def run_one(task):
            return records[task.name]

        report = await run_ue_suite(tasks, run_one)
        assert report.pass_rate == 0.5
        assert report.first_try_pass_rate == 0.5  # 仅 pass_one_try：通过+无重试+零干预
        assert report.avg_iterations == 3.5
        assert report.total_human_intervention == 0

    async def test_run_ue_suite_records_run_error(self):
        from ue5agent.evals.ue_suite import UeEvalTask, UeRunRecord, run_ue_suite

        task = UeEvalTask(name="boom", prompt="x", checks=[{"type": "run_succeeded"}])

        async def run_one(_task):
            return UeRunRecord(success=False, error="RuntimeError: boom")

        report = await run_ue_suite([task], run_one)
        assert not report.results[0].passed
        assert any("boom" in f for f in report.results[0].failures)
