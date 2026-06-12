"""工具注册表：本地工具与 MCP 工具统一成 OpenAI function calling 形态。

K2 起调用链在 agent/tool_pipeline.py；registry 只管注册与 schema，
dispatch 保留为兼容入口（委托给内部管线），K5 收口时再评估是否移除。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ue5agent.core.permissions import PermissionGate, PermissionLevel

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
        self._pipeline: Any = None

    def __len__(self) -> int:
        return len(self._tools)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重名：{spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

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

    def _ensure_pipeline(self) -> Any:
        if self._pipeline is None:
            from ue5agent.agent.tool_pipeline import ToolPipeline  # 函数级导入避免循环依赖

            self._pipeline = ToolPipeline(self, self._gate)
        return self._pipeline

    async def dispatch(self, name: str, arguments_json: str) -> str:
        """兼容入口：委托给 ToolPipeline，只回文本。"""
        return (await self.run(name, arguments_json)).text

    async def run(self, name: str, arguments_json: str) -> Any:
        """结构化入口：返回 ToolOutcome（含 facts 证据信封），loop 用它写 trace。"""
        return await self._ensure_pipeline().run(name, arguments_json)
