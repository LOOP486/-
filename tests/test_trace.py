"""trace：loop 事件埋点、token 汇总、读取与定位。"""

from tests.test_loop import FakeModel, make_registry
from ue5agent.core.loop import AgentLoop
from ue5agent.llm.types import AssistantTurn, ToolCall, Usage
from ue5agent.session_log import SessionLog, latest_session, read_events


def scripted_model() -> FakeModel:
    return FakeModel(
        [
            AssistantTurn(
                content=None,
                tool_calls=[ToolCall("call_1", "echo", '{"text": "hi"}')],
                usage=Usage(prompt_tokens=100, completion_tokens=20),
            ),
            AssistantTurn(content="完成", usage=Usage(prompt_tokens=150, completion_tokens=10)),
        ]
    )


async def test_loop_emits_full_event_sequence(tmp_path):
    log = SessionLog(tmp_path)
    loop = AgentLoop(scripted_model(), make_registry(), session_log=log)
    result = await loop.run("测试")

    events = read_events(log.path)
    assert [e["event"] for e in events] == [
        "run_start",
        "llm_turn",
        "tool_call",
        "llm_turn",
        "run_end",
    ]
    tool_event = events[2]
    assert tool_event["tool"] == "echo"
    assert tool_event["result_preview"] == "echo: hi"
    assert "duration_ms" in tool_event
    end_event = events[-1]
    assert end_event["prompt_tokens"] == 250
    assert end_event["completion_tokens"] == 30
    assert result.prompt_tokens == 250
    assert result.completion_tokens == 30


async def test_usage_optional(tmp_path):
    """模型不回 usage（部分中转会丢）时 token 记 0，不崩。"""
    log = SessionLog(tmp_path)
    loop = AgentLoop(FakeModel([AssistantTurn(content="ok")]), make_registry(), session_log=log)
    result = await loop.run("测试")
    assert result.prompt_tokens == 0


def test_latest_session_picks_newest(tmp_path):
    (tmp_path / "session-20260101-000000.jsonl").write_text("{}", encoding="utf-8")
    newest = tmp_path / "session-20260102-000000.jsonl"
    newest.write_text("{}", encoding="utf-8")
    assert latest_session(tmp_path) == newest


def test_latest_session_missing_dir(tmp_path):
    assert latest_session(tmp_path / "nope") is None


def test_read_events_skips_corrupt_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"event": "a"}\n{broken\n{"event": "b"}\n', encoding="utf-8")
    assert [e["event"] for e in read_events(path)] == ["a", "b"]
