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


def compact_history(
    messages: list[dict[str, Any]],
    budget_chars: int,
    *,
    keep_recent: int = 8,
) -> list[dict[str, Any]]:
    """超出字符预算时，把早期轮次压缩成一条确定性摘要。

    规则：
    - 开头的 system 消息与最近 keep_recent 条原样保留；
    - 窗口边界不得拆开 assistant(tool_calls) 与其 tool 结果：起点落在 tool 消息上时
      向后挪，直到窗口以非 tool 消息开头（OpenAI 格式要求配对完整）；
    - 摘要保留被压缩部分的用户请求与工具调用名。

    TODO(roadmap 横切)：用模型生成摘要替代确定性拼接。
    """
    if _total_chars(messages) <= budget_chars:
        return messages
    head = messages[:1] if messages and messages[0].get("role") == "system" else []
    body = messages[len(head) :]
    if len(body) <= keep_recent:
        return messages
    old = body[:-keep_recent]
    recent = body[-keep_recent:]
    while recent and recent[0].get("role") == "tool":
        old.append(recent.pop(0))
    if not old:
        return messages
    summary = {"role": "user", "content": _summarize(old)}
    return [*head, summary, *recent]


def _total_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += len(str(message.get("content") or ""))
        for call in message.get("tool_calls") or []:
            total += len(str(call.get("function", {}).get("arguments") or ""))
    return total


def _summarize(old: list[dict[str, Any]]) -> str:
    requests = [str(m.get("content") or "")[:120] for m in old if m.get("role") == "user"]
    tool_names: list[str] = []
    for message in old:
        for call in message.get("tool_calls") or []:
            name = call.get("function", {}).get("name", "")
            if name and name not in tool_names:
                tool_names.append(name)
    parts = [f"[历史压缩] 此前 {len(old)} 条消息已压缩。"]
    if requests:
        parts.append("期间的用户请求：" + "；".join(requests))
    if tool_names:
        parts.append("已调用过的工具：" + "、".join(tool_names))
    return " ".join(parts)
