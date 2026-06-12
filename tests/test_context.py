"""上下文管理：截断、历史压缩与配对边界。"""

import json

from tests.test_loop import FakeModel, make_registry
from ue5agent.core.context import (
    build_project_brief,
    compact_history,
    summarize_tool_result,
    truncate,
)
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


class TestSummarizeToolResult:
    def test_short_result_untouched(self):
        assert summarize_tool_result("ok", 100) == "ok"

    def test_actor_list_folded_to_count_and_names(self):
        actors = {"actors": [{"name": f"WB_{i}", "class": "SM"} for i in range(50)]}
        text = json.dumps(actors)
        out = summarize_tool_result(text, 200, tool_name="ue_editor__editor_actors")
        assert "共 50 个 actor" in out
        assert "WB_0" in out
        assert len(out) <= 220  # 摘要 + 兜底截断

    def test_toplevel_list_of_actors_folded(self):
        text = json.dumps([{"name": f"A{i}"} for i in range(40)])
        out = summarize_tool_result(text, 150)
        assert "共 40 个 actor" in out

    def test_compile_log_keeps_error_lines(self):
        lines = ["Building...", *[f"normal output {i}" for i in range(200)]]
        lines.append("MyFile.cpp(12): error: undeclared identifier 'Foo'")
        lines.append("Result: Failed")
        text = "\n".join(lines)
        out = summarize_tool_result(text, 600, tool_name="ue_build__ubt_compile")
        assert "编译日志摘要" in out
        assert "error: undeclared identifier" in out
        assert "Result: Failed" in out
        assert len(out) <= 650

    def test_non_special_falls_back_to_truncate(self):
        text = "X" * 500
        out = summarize_tool_result(text, 100)
        assert "已截断" in out


class TestProjectBrief:
    def test_empty_when_no_probes(self):
        assert build_project_brief() == ""

    def test_assembles_and_clips(self):
        brief = build_project_brief(
            editor="online：编辑器桥可达",
            repo="分支 master（干净）",
            engine="UE 5.7",
        )
        assert "工程状态" in brief
        assert "UE 5.7" in brief
        assert "online" in brief
        assert "master" in brief

    def test_respects_max_chars(self):
        brief = build_project_brief(repo="x" * 1000, max_chars=120)
        assert len(brief) <= 120


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
