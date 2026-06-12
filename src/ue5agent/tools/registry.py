"""工具注册表：本地工具与 MCP 工具统一成 OpenAI function calling 形态。

K2 起调用链在 agent/tool_pipeline.py；registry 只管注册与 schema，
dispatch 保留为兼容入口（委托给内部管线），K5 收口时再评估是否移除。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ue5agent.core.permissions import PermissionGate, PermissionLevel
from ue5agent.tools.effects import ToolEffects, default_effects

ToolHandler = Callable[..., Awaitable[str]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    """OpenAI function calling 的 JSON Schema。"""
    level: PermissionLevel
    handler: ToolHandler
    effects: ToolEffects | None = None
    """副作用声明（B2）。不传则按权限级推导保守默认，构造后保证非 None。"""

    def __post_init__(self) -> None:
        if self.effects is None:
            self.effects = default_effects(self.level)


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


class ScopedRegistry:
    """按步骤契约收紧的注册表视图（B1）：工具白名单 + 权限上限。

    不修改底层注册表；loop 经此视图取 specs 与执行。白名单匹配支持裸名——
    planner 通常写 `wb_build`，而注册名是 `ue_whitebox__wb_build`。
    越权调用返回 [denied] 文本（不抛出），与管线的失败语义一致。
    """

    def __init__(
        self,
        base: ToolRegistry,
        *,
        allowed_tools: list[str] | None = None,
        permission_ceiling: str = "",
    ):
        self._base = base
        self._allowed = list(allowed_tools or [])
        self._ceiling: PermissionLevel | None = None
        if permission_ceiling:
            try:
                self._ceiling = PermissionLevel(permission_ceiling)
            except ValueError:
                self._ceiling = None  # 非法值视为不限，契约宽于实际比误杀安全

    def _deny_reason(self, spec: ToolSpec) -> str | None:
        """返回拒绝原因；放行返回 None。显式点名优先于权限上限——
        白名单按名指定是比上限更具体的契约声明（planner 实测会同时声明
        ceiling=read 与白名单内的写工具，按上限拦会让步骤无解）。"""
        from ue5agent.core.permissions import level_rank

        explicit = any(
            spec.name == entry or spec.name.endswith(f"__{entry}") for entry in self._allowed
        )
        if self._allowed and not explicit:
            return f"不在本步骤工具白名单（{self._allowed}）"
        if (
            not explicit
            and self._ceiling is not None
            and level_rank(spec.level) > level_rank(self._ceiling)
        ):
            return f"超出本步骤权限上限（{self._ceiling}，该工具为 {spec.level}）"
        return None

    def _tool_permitted(self, spec: ToolSpec) -> bool:
        return self._deny_reason(spec) is None

    def __len__(self) -> int:
        return len(self.names())

    def get(self, name: str) -> ToolSpec | None:
        spec = self._base.get(name)
        return spec if spec is not None and self._tool_permitted(spec) else None

    def names(self) -> list[str]:
        return [n for n in self._base.names() if self.get(n) is not None]

    def specs(self) -> list[dict[str, Any]]:
        permitted = set(self.names())
        return [s for s in self._base.specs() if s["function"]["name"] in permitted]

    async def run(self, name: str, arguments_json: str) -> Any:
        from ue5agent.agent.tool_pipeline import ToolOutcome  # 函数级导入避免循环依赖

        spec = self._base.get(name)
        reason = self._deny_reason(spec) if spec is not None else None
        if reason is not None:
            return ToolOutcome(
                ok=False,
                text=f"[denied] 本步骤契约不允许使用 {name}：{reason}，换契约内的工具完成",
                error_kind="denied",
            )
        return await self._base.run(name, arguments_json)

    async def dispatch(self, name: str, arguments_json: str) -> str:
        return (await self.run(name, arguments_json)).text
