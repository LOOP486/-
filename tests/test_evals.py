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
        assert not failed.passed
        assert "不会出现的文本" in failed.failures[0]

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
