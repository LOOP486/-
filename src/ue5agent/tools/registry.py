"""工具注册表：本地工具与 MCP 工具统一成 OpenAI function calling 形态。

工具失败/被拒绝时返回带标记的文本回传给模型（而不是中断循环），由模型自行调整。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

from ue5agent.core.permissions import PermissionGate, PermissionLevel, ToolDenied
from ue5agent.tools.validation import parse_arguments, validate_arguments

ToolHandler = Callable[..., Awaitable[str]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    """OpenAI function calling 的 JSON Schema。"""
    level: PermissionLevel
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, gate: PermissionGate):
        self._gate = gate
        self._tools: dict[str, ToolSpec] = {}

    def __len__(self) -> int:
        return len(self._tools)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重名：{spec.name}")
        self._tools[spec.name] = spec

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    async def dispatch(self, name: str, arguments_json: str) -> str:
        if name not in self._tools:
            similar = get_close_matches(name, list(self._tools), n=1)
            hint = f"，是不是想用 {similar[0]}？" if similar else ""
            return f"[error] 未知工具：{name}{hint}"
        tool = self._tools[name]
        try:
            arguments = parse_arguments(arguments_json)
        except ValueError as exc:
            return f"[error] {exc}"
        violations = validate_arguments(tool.parameters, arguments)
        if violations:
            return "[error] 参数不符合 schema：" + "；".join(violations)
        try:
            self._gate.check(name, tool.level, arguments)
        except ToolDenied as exc:
            return f"[denied] {exc}"
        try:
            return await tool.handler(**arguments)
        except Exception as exc:
            return f"[error] {type(exc).__name__}: {exc}"
