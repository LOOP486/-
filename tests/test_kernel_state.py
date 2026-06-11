"""K1：TaskSession 持久化、RunWriter 事件与产物、loop 兼容层。"""

import pytest

from tests.test_loop import FakeModel, make_registry
from ue5agent.agent.events import EVENT_TYPES, RunWriter, latest_trace, read_events
from ue5agent.agent.state import Budgets, PlanStep, TaskSession
from ue5agent.core.loop import AgentLoop
from ue5agent.llm.types import AssistantTurn, ToolCall


class TestTaskSession:
    def test_save_load_roundtrip(self, tmp_path):
        session = TaskSession.new("给角色加冲刺技能")
        session.plan = [PlanStep(id="s1", intent="写 C++", acceptance="编译通过")]
        session.budgets = Budgets(max_iterations=10)
        directory = tmp_path / session.id
        directory.mkdir(parents=True)
        session.save(directory)

        loaded = TaskSession.load(directory / "session.json")
        assert loaded.goal == session.goal
        assert loaded.plan[0].acceptance == "编译通过"
        assert loaded.budgets.max_iterations == 10
        assert loaded.status == "running"

    def test_id_is_directory_safe(self):
        session = TaskSession.new("修复 跳跃/二段跳 bug!!")
        assert "/" not in session.id
        assert "!" not in session.id
        assert session.id.split("_", 1)[1].startswith("修复")

    def test_empty_goal_falls_back(self):
        assert TaskSession.new("///").id.endswith("_task")


class TestRunWriter:
    def test_unknown_event_type_rejected(self, tmp_path):
        writer = RunWriter(tmp_path, TaskSession.new("x"))
        with pytest.raises(ValueError, match="trace 事件类型"):
            writer.event("made_up_event")

    def test_events_artifacts_session_report(self, tmp_path):
        session = TaskSession.new("测试")
        writer = RunWriter(tmp_path, session)
        writer.event("run_start", phase="intake", user_input="hi")
        writer.save_artifact("build_log", "build.txt", "ok", target="agent_testEditor")
        writer.save_session()
        writer.write_report("# 报告")

        events = read_events(writer.trace_path)
        assert events[0]["event"] == "run_start"
        assert events[0]["session_id"] == session.id
        assert events[0]["phase"] == "intake"
        assert (writer.artifacts_dir / "build.txt").read_text(encoding="utf-8") == "ok"
        assert session.artifacts[0].path == "artifacts/build.txt"
        assert session.artifacts[0].meta["target"] == "agent_testEditor"
        assert (writer.dir / "session.json").exists()
        assert (writer.dir / "report.md").exists()

    async def test_loop_writes_through_runwriter(self, tmp_path):
        """兼容层验收：现有 loop 不改一行即可写新 trace。"""
        writer = RunWriter(tmp_path, TaskSession.new("loop兼容"))
        model = FakeModel(
            [
                AssistantTurn(None, [ToolCall("c1", "echo", '{"text": "hi"}')]),
                AssistantTurn("done"),
            ]
        )
        loop = AgentLoop(model, make_registry(), session_log=writer)
        await loop.run("测试")
        kinds = [e["event"] for e in read_events(writer.trace_path)]
        assert kinds == ["run_start", "llm_turn", "tool_call", "llm_turn", "run_end"]
        assert all(kind in EVENT_TYPES for kind in kinds)


def test_read_events_skips_corrupt_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"event": "a"}\n{broken\n{"event": "b"}\n', encoding="utf-8")
    assert [e["event"] for e in read_events(path)] == ["a", "b"]


class TestLatestTrace:
    def test_missing_root(self, tmp_path):
        assert latest_trace(tmp_path / "nope") is None

    def test_picks_newest_by_session_id(self, tmp_path):
        for sid in ["20260101-000000_a", "20260102-000000_b"]:
            writer = RunWriter(tmp_path, TaskSession(id=sid, goal="x"))
            writer.event("run_start")
        found = latest_trace(tmp_path)
        assert found is not None
        assert found.parent.name == "20260102-000000_b"
