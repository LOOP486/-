"""上下文管理：截断、历史压缩与配对边界。"""

from tests.test_loop import FakeModel, make_registry
from ue5agent.core.context import compact_history, truncate
from ue5agent.core.loop import AgentLoop
from ue5agent.llm.types import AssistantTurn


class TestTruncate:
    def test_short_text_untouched(self):
        assert truncate("abc", 10) == "abc"

    def test_long_text_keeps_head_and_tail(self):
        text = "A" * 100 + "B" * 100
        result = truncate(text, 60)
        assert result.startswith("A")
        assert result.endswith("B")
        assert "已截断" in result


def assistant_with_tool(name: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": name, "arguments": "{}"}}
        ],
    }


def long_history(rounds: int) -> list[dict]:
    messages = [{"role": "system", "content": "sys"}]
    for index in range(rounds):
        messages.append({"role": "user", "content": f"请求{index} " + "x" * 200})
        messages.append(assistant_with_tool(f"tool_{index}"))
        messages.append({"role": "tool", "tool_call_id": "c1", "content": "result " + "y" * 200})
        messages.append({"role": "assistant", "content": "答复 " + "z" * 200})
    return messages


class TestCompactHistory:
    def test_under_budget_unchanged(self):
        messages = long_history(2)
        assert compact_history(messages, budget_chars=10_000) is messages

    def test_over_budget_compacts_old_keeps_recent(self):
        # keep_recent=5 的窗口起点是普通 assistant 消息，不触发边界后移
        messages = long_history(10)
        compacted = compact_history(messages, budget_chars=2_000, keep_recent=5)
        assert compacted[0]["role"] == "system"
        assert "[历史压缩]" in compacted[1]["content"]
        assert compacted[2:] == messages[-5:]
        assert len(compacted) == 7

    def test_summary_mentions_requests_and_tools(self):
        compacted = compact_history(long_history(10), budget_chars=2_000, keep_recent=4)
        summary = compacted[1]["content"]
        assert "请求0" in summary
        assert "tool_0" in summary

    def test_window_never_starts_with_tool_message(self):
        messages = long_history(10)
        # keep_recent=6 的窗口起点落在 tool 消息上，应自动后移一位（保留 5 条）
        compacted = compact_history(messages, budget_chars=2_000, keep_recent=6)
        assert compacted[2]["role"] != "tool"
        assert compacted[2:] == messages[-5:]
        for index, message in enumerate(compacted):
            if message.get("role") == "tool":
                previous = compacted[index - 1]
                assert previous.get("tool_calls") or previous.get("role") == "tool"


class TestMultiTurnHistory:
    async def test_history_carries_across_runs(self):
        model = FakeModel([AssistantTurn(content="第一答"), AssistantTurn(content="第二答")])
        loop = AgentLoop(model, make_registry())
        history: list[dict] = []
        await loop.run("第一问", history=history)
        await loop.run("第二问", history=history)
        # 第二次调用时模型应能看到第一轮的完整对话
        second_view = model.seen_messages[1]
        contents = [str(m.get("content")) for m in second_view]
        assert any("第一问" in c for c in contents)
        assert any("第一答" in c for c in contents)
        # system 提示只注入一次
        assert sum(1 for m in history if m["role"] == "system") == 1
