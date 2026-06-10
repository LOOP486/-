"""上下文管理：工具结果截断与历史压缩。"""

from __future__ import annotations

from typing import Any


def truncate(text: str, max_chars: int) -> str:
    """超长工具结果保头掐中留尾——错误信息常在开头，汇总常在结尾。"""
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    omitted = len(text) - max_chars
    return f"{text[:head]}\n...[已截断 {omitted} 字符]...\n{text[-tail:]}"


def compact_history(messages: list[dict[str, Any]], budget_chars: int) -> list[dict[str, Any]]:
    """历史压缩：超出预算时用模型摘要早期轮次。

    TODO(roadmap Phase 0)：当前为直通占位，长会话会撑爆上下文。
    """
    return messages
