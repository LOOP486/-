"""LLM 抽象层的数据结构与协议。

loop 只依赖这里的 ChatModel 协议，不直接依赖 litellm——测试用替身实现同一协议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str
    """模型产出的原始 JSON 字符串，由 registry 负责解析与容错。"""


@dataclass
class AssistantTurn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


class ChatModel(Protocol):
    async def acomplete(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantTurn: ...
