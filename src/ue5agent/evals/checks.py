"""评测检查器：对一次任务运行的结果做断言。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ue5agent.core.loop import LoopResult


@dataclass
class TaskOutcome:
    """一次任务运行的可观测产物。"""

    result: LoopResult | None
    calls: list[tuple[str, str]] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)


def evaluate_check(check: dict[str, Any], outcome: TaskOutcome) -> str | None:
    """通过返回 None，失败返回原因描述。未知检查类型视为失败（任务文件写错要暴露）。"""
    kind = check.get("type")
    if kind == "tool_called":
        return _tool_called(check, outcome)
    if kind == "tool_not_called":
        tool = check["tool"]
        if any(name == tool for name, _ in outcome.calls):
            return f"不应调用 {tool} 但调用了"
        return None
    if kind == "tool_called_times":
        tool, at_least = check["tool"], int(check["at_least"])
        count = sum(1 for name, _ in outcome.calls if name == tool)
        if count < at_least:
            return f"{tool} 应至少调用 {at_least} 次，实际 {count} 次"
        return None
    if kind == "final_contains":
        text = str(check["text"])
        final = outcome.result.final_text if outcome.result else ""
        if text not in final:
            return f"最终答复应包含「{text}」"
        return None
    if kind == "max_turns":
        limit = int(check["value"])
        turns = outcome.result.turns if outcome.result else 10**9
        if turns > limit:
            return f"轮数 {turns} 超过上限 {limit}"
        return None
    if kind == "no_tool_errors":
        bad = [r for r in outcome.tool_results if r.startswith(("[error]", "[denied]"))]
        if bad:
            return f"出现 {len(bad)} 次工具错误，首个：{bad[0][:80]}"
        return None
    return f"未知检查类型：{kind}"


def _tool_called(check: dict[str, Any], outcome: TaskOutcome) -> str | None:
    tool = check["tool"]
    args_contain = check.get("args_contain")
    matched = [args for name, args in outcome.calls if name == tool]
    if not matched:
        return f"未调用 {tool}"
    if args_contain is not None and not any(str(args_contain) in args for args in matched):
        return f"{tool} 的参数应包含「{args_contain}」"
    return None
