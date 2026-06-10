"""Agent 主循环：模型与工具往复调用，直到产出最终答复或耗尽迭代预算。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ue5agent.core.context import compact_history, truncate
from ue5agent.llm.types import AssistantTurn, ChatModel
from ue5agent.session_log import SessionLog
from ue5agent.tools.registry import ToolRegistry

SYSTEM_PROMPT = """\
你是面向 Unreal Engine 5 工程的开发 agent。

工作原则：
1. 改动前先用工具读懂相关代码与资产，不凭记忆臆断引擎 API。
2. 新逻辑用 C++ 实现；蓝图只做只读分析。
3. 任何修改类任务，结束前必须出示验证证据（编译输出、测试结果或截图），\
没有证据不得声称完成。
4. 工具调用失败时阅读错误信息并调整方案，不要原样重试。
"""


@dataclass
class LoopResult:
    final_text: str
    turns: int
    tool_call_count: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


class BudgetExhausted(Exception):
    """迭代预算耗尽仍未产出最终答复。"""


class AgentLoop:
    def __init__(
        self,
        llm: ChatModel,
        registry: ToolRegistry,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int = 40,
        max_tool_result_chars: int = 30_000,
        compact_budget_chars: int = 200_000,
        session_log: SessionLog | None = None,
    ):
        self._llm = llm
        self._registry = registry
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations
        self._max_tool_result_chars = max_tool_result_chars
        self._compact_budget_chars = compact_budget_chars
        self._log = session_log

    async def run(
        self,
        user_input: str,
        *,
        role: str = "planner",
        history: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        """任务入口。传入同一个 history 列表即可跨输入延续会话（loop 原地追加）。"""
        messages = history if history is not None else []
        if not messages:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": user_input})
        if self._log:
            self._log.write("run_start", role=role, user_input=user_input)
        tool_call_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        for turn in range(1, self._max_iterations + 1):
            view = compact_history(messages, self._compact_budget_chars)
            started = time.monotonic()
            assistant = await self._llm.acomplete(role, view, tools=self._registry.specs())
            llm_duration_ms = round((time.monotonic() - started) * 1000)
            if assistant.usage:
                prompt_tokens += assistant.usage.prompt_tokens
                completion_tokens += assistant.usage.completion_tokens
            if self._log:
                self._log.write(
                    "llm_turn",
                    turn=turn,
                    duration_ms=llm_duration_ms,
                    prompt_tokens=assistant.usage.prompt_tokens if assistant.usage else 0,
                    completion_tokens=assistant.usage.completion_tokens if assistant.usage else 0,
                    tool_names=[call.name for call in assistant.tool_calls],
                    content_preview=(assistant.content or "")[:200],
                )
            messages.append(_assistant_message(assistant))
            if not assistant.tool_calls:
                if self._log:
                    self._log.write(
                        "run_end",
                        turns=turn,
                        tool_calls=tool_call_count,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                return LoopResult(
                    assistant.content or "",
                    turn,
                    tool_call_count,
                    prompt_tokens,
                    completion_tokens,
                )
            for call in assistant.tool_calls:
                started = time.monotonic()
                tool_result = await self._registry.dispatch(call.name, call.arguments)
                tool_duration_ms = round((time.monotonic() - started) * 1000)
                tool_call_count += 1
                if self._log:
                    self._log.write(
                        "tool_call",
                        tool=call.name,
                        arguments=call.arguments[:500],
                        duration_ms=tool_duration_ms,
                        result_chars=len(tool_result),
                        result_preview=tool_result[:200],
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": truncate(tool_result, self._max_tool_result_chars),
                    }
                )
        raise BudgetExhausted(f"迭代 {self._max_iterations} 轮仍未产出最终答复")


def _assistant_message(turn: AssistantTurn) -> dict[str, Any]:
    """把 AssistantTurn 还原为 OpenAI 格式的历史消息。"""
    message: dict[str, Any] = {"role": "assistant", "content": turn.content}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in turn.tool_calls
        ]
    return message
